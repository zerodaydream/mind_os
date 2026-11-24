"""Phase 06 gate: failure injection on the token path, PKCE, the loopback, and secret hygiene."""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta

import httpx
import pytest

from mindos.application import AuthService
from mindos.domain import (
    AccessToken,
    AuthError,
    AuthStatus,
    ConsentRequired,
    ReauthRequired,
    ScopeRequired,
    TokenGrant,
    TransientAuthError,
)
from mindos.integrations.auth import LoopbackError, LoopbackReceiver
from mindos.integrations.auth.pkce import challenge_for, new_verifier
from mindos.integrations.auth.providers import scopes_for
from tests.support.auth_kit import (
    ACCESS,
    REFRESH,
    Rig,
    grant,
    id_token,
    json_response,
    token_json,
)

GMAIL = "https://www.googleapis.com/auth/gmail.readonly"
KEY = "refresh:google:me@x.com"


# ---- the happy path and caching --------------------------------------------------------------
async def test_a_token_is_refreshed_once_then_served_from_memory_until_near_expiry() -> None:
    rig = Rig(json_response(200, token_json()))
    token = await rig.tokens.access_token("google", "me@x.com")
    assert token.value == ACCESS and len(rig.endpoint.calls) == 1
    assert rig.endpoint.calls[0]["grant_type"] == "refresh_token"
    await rig.tokens.access_token("google", "me@x.com")
    assert len(rig.endpoint.calls) == 1  # cached

    rig.clock.advance(timedelta(seconds=3600 - 30))  # inside the 60 s skew: refresh ahead of expiry
    await rig.tokens.access_token("google", "me@x.com")
    assert len(rig.endpoint.calls) == 2


async def test_concurrent_callers_cause_a_single_refresh() -> None:
    rig = Rig(json_response(200, token_json()))
    tokens = await asyncio.gather(
        *(rig.tokens.access_token("google", "me@x.com") for _ in range(20))
    )
    assert {t.value for t in tokens} == {ACCESS}
    assert len(rig.endpoint.calls) == 1 and rig.lock.holds == 1


async def test_a_rotated_refresh_token_replaces_the_stored_one() -> None:
    rig = Rig(json_response(200, token_json(refresh_token="rt-NEW")))
    await rig.tokens.access_token("google", "me@x.com")
    assert rig.secrets.get(KEY) == "rt-NEW"


async def test_a_401_from_the_api_makes_the_next_call_refresh() -> None:
    rig = Rig(json_response(200, token_json()))
    await rig.tokens.access_token("google", "me@x.com")
    await rig.tokens.rejected("google", "me@x.com")
    await rig.tokens.access_token("google", "me@x.com")
    assert len(rig.endpoint.calls) == 2


# ---- failure injection -----------------------------------------------------------------------
async def test_invalid_grant_means_needs_reauth_and_is_not_asked_again() -> None:
    rig = Rig(json_response(400, {"error": "invalid_grant"}))
    with pytest.raises(ReauthRequired):
        await rig.tokens.access_token("google", "me@x.com")
    state = await rig.states.get("google", "me@x.com")
    assert state is not None and state.status is AuthStatus.NEEDS_REAUTH
    with pytest.raises(ReauthRequired):  # known-bad: the provider is not hammered
        await rig.tokens.access_token("google", "me@x.com")
    assert len(rig.endpoint.calls) == 1 and rig.sleep.delays == []  # never retried


async def test_429_is_retried_with_jitter_and_retry_after_then_succeeds() -> None:
    rig = Rig(
        json_response(429, {}, **{"Retry-After": "7"}),
        json_response(503, {}),
        json_response(200, token_json()),
    )
    token = await rig.tokens.access_token("google", "me@x.com")
    assert token.value == ACCESS and len(rig.endpoint.calls) == 3
    assert rig.sleep.delays[0] >= 7  # the server's Retry-After wins over a shorter jitter
    assert all(d <= 1.0 or d >= 7 for d in rig.sleep.delays)  # capped jitter otherwise


async def test_a_network_drop_that_outlasts_the_retries_is_transient_not_reauth() -> None:
    rig = Rig(httpx.ConnectError("down"))
    with pytest.raises(TransientAuthError):
        await rig.tokens.access_token("google", "me@x.com")
    assert len(rig.endpoint.calls) == 3
    assert await rig.states.get("google", "me@x.com") is None  # an outage never flips the status


async def test_an_open_circuit_refuses_without_calling_the_provider() -> None:
    rig = Rig(json_response(503, {}))
    for _ in range(2):  # 3 attempts each: the default threshold of 5 opens during the second
        with pytest.raises(TransientAuthError):
            await rig.tokens.access_token("google", "me@x.com")
    calls = len(rig.endpoint.calls)
    with pytest.raises(TransientAuthError):
        await rig.tokens.access_token("google", "me@x.com")
    assert len(rig.endpoint.calls) == calls


async def test_a_missing_refresh_token_is_reauth_not_a_crash() -> None:
    rig = Rig(json_response(200, token_json()), connected=False)
    with pytest.raises(ReauthRequired, match="mindos auth google"):
        await rig.tokens.access_token("google", "me@x.com")
    assert rig.endpoint.calls == []


async def test_a_scope_that_was_never_granted_is_refused_before_any_request() -> None:
    rig = Rig(json_response(200, token_json()))
    await rig.connected(GMAIL)
    with pytest.raises(ScopeRequired) as caught:
        await rig.tokens.access_token("google", "me@x.com", scopes=["https://x/gmail.send"])
    assert caught.value.scope_delta == ("https://x/gmail.send",) and rig.endpoint.calls == []


async def test_tenant_consent_is_reported_as_consent_required() -> None:
    rig = Rig(json_response(400, {"error": "admin_policy_enforced"}))
    with pytest.raises(ConsentRequired):
        await rig.tokens.access_token("google", "me@x.com")
    state = await rig.states.get("google", "me@x.com")
    assert state is None  # consent is not a broken sign-in


async def test_invalid_scope_marks_needs_scope() -> None:
    rig = Rig(json_response(400, {"error": "invalid_scope"}))
    with pytest.raises(ScopeRequired):
        await rig.tokens.access_token("google", "me@x.com")
    state = await rig.states.get("google", "me@x.com")
    assert state is not None and state.status is AuthStatus.NEEDS_SCOPE


async def test_a_misconfigured_client_does_not_break_the_account() -> None:
    rig = Rig(json_response(401, {"error": "invalid_client"}))
    with pytest.raises(AuthError, match="invalid_client"):
        await rig.tokens.access_token("google", "me@x.com")
    assert await rig.states.get("google", "me@x.com") is None


# ---- connect and revoke ----------------------------------------------------------------------
def service(rig: Rig) -> AuthService:
    return AuthService(
        providers={"google": rig.client},
        secrets=rig.secrets,
        states=rig.states,
        tokens=rig.tokens,
    )


async def test_connect_stores_the_refresh_token_in_the_secret_store_only() -> None:
    rig = Rig(json_response(200, token_json()), connected=False)
    state = await service(rig).connect("google", grant())
    assert (state.account_id, state.status) == ("me@x.com", AuthStatus.CONNECTED)
    assert rig.secrets.get(KEY) == REFRESH
    assert REFRESH not in repr(await rig.states.all())  # the state row holds no secret


async def test_connect_without_a_refresh_token_is_refused_with_advice() -> None:
    rig = Rig(json_response(200, token_json()), connected=False)
    with pytest.raises(ReauthRequired, match="no refresh token"):
        await service(rig).connect("google", grant(refresh=None))
    assert rig.secrets.data == {}


async def test_revoke_deletes_locally_even_if_the_provider_refuses_and_says_so() -> None:
    rig = Rig(json_response(500, {}), json_response(500, {}), json_response(500, {}))
    await rig.connected(GMAIL)
    assert await service(rig).revoke("google", "me@x.com") is False
    assert rig.secrets.get(KEY) is None
    state = await rig.states.get("google", "me@x.com")
    assert state is not None and state.status is AuthStatus.REVOKED
    with pytest.raises(ReauthRequired):  # and nothing can use it again
        await rig.tokens.access_token("google", "me@x.com")


async def test_revoke_at_the_provider_succeeds() -> None:
    rig = Rig(httpx.Response(200))
    await rig.connected(GMAIL)
    assert await service(rig).revoke("google", "me@x.com") is True
    assert rig.endpoint.calls[0] == {"token": REFRESH}


# ---- secret hygiene --------------------------------------------------------------------------
def test_tokens_do_not_appear_in_repr_or_str() -> None:
    token = AccessToken(ACCESS, grant().access.expires_at)
    full = TokenGrant(access=token, scopes=("a",), refresh_token=REFRESH)
    for text in (repr(token), str(token), repr(full), str(full)):
        assert ACCESS not in text and REFRESH not in text


async def test_no_token_reaches_the_logs_or_error_messages(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    for responses in (
        [json_response(400, {"error": "invalid_grant", "error_description": REFRESH})],
        [json_response(503, {})],
    ):
        rig = Rig(*responses)
        with pytest.raises(AuthError) as caught:
            await rig.tokens.access_token("google", "me@x.com")
        assert REFRESH not in str(caught.value) and ACCESS not in str(caught.value)
    assert REFRESH not in caplog.text and ACCESS not in caplog.text


async def test_the_authorization_url_carries_pkce_and_no_secret() -> None:
    rig = Rig(json_response(200, token_json()))
    url = rig.client.authorization_url(
        redirect_uri="http://127.0.0.1:9", scopes=["openid"], state="st", challenge="ch"
    )
    query = httpx.URL(url).params
    assert query["code_challenge"] == "ch" and query["code_challenge_method"] == "S256"
    assert query["access_type"] == "offline" and "client_secret" not in query


async def test_the_account_comes_from_the_id_token() -> None:
    rig = Rig(json_response(200, token_json(refresh_token=REFRESH, id_token=id_token("Me@X.com"))))
    result = await rig.client.exchange(code="c", verifier="v", redirect_uri="http://127.0.0.1:9")
    assert result.account_id == "me@x.com"
    assert rig.endpoint.calls[0]["code_verifier"] == "v"


# ---- PKCE and scopes -------------------------------------------------------------------------
def test_pkce_challenge_matches_the_rfc_7636_vector() -> None:
    verifier = "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"
    assert challenge_for(verifier) == "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"
    assert 43 <= len(new_verifier()) <= 128 and new_verifier() != new_verifier()


def test_scopes_are_the_smallest_set_for_the_capabilities_asked() -> None:
    assert scopes_for("google", ["mail-read"]) == (
        "openid",
        "email",
        "https://www.googleapis.com/auth/gmail.readonly",
    )
    send = scopes_for("google", ["mail-send"])
    assert send[-1].endswith("gmail.send") and not any("readonly" in s for s in send)
    with pytest.raises(KeyError):
        scopes_for("google", ["mail-everything"])


# ---- the loopback redirect -------------------------------------------------------------------
async def test_the_loopback_accepts_only_the_request_with_the_right_state() -> None:
    receiver = LoopbackReceiver("good")
    base = await receiver.start()
    try:
        async with httpx.AsyncClient() as http:
            assert (await http.get(f"{base}/?code=evil&state=bad")).status_code == 400
            assert (await http.get(f"{base}/?code=nostate")).status_code == 400
            assert (await http.get(f"{base}/?code=abc&state=good")).status_code == 200
            assert (await http.get(f"{base}/?code=again&state=good")).status_code == 400
        assert (await receiver.wait(1)).code == "abc"
    finally:
        await receiver.close()


async def test_the_loopback_reports_a_refusal_and_a_timeout() -> None:
    refused = LoopbackReceiver("s")
    base = await refused.start()
    try:
        async with httpx.AsyncClient() as http:
            await http.get(f"{base}/?error=access_denied&state=s")
        with pytest.raises(LoopbackError, match="access_denied"):
            await refused.wait(1)
    finally:
        await refused.close()
    silent = LoopbackReceiver("s")
    await silent.start()
    try:
        with pytest.raises(LoopbackError, match="Timed out"):
            await silent.wait(0.05)
    finally:
        await silent.close()
