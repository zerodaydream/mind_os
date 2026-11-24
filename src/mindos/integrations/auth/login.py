"""The interactive sign-in: browser, loopback redirect, code exchange."""

from __future__ import annotations

from collections.abc import Callable, Sequence

from mindos.domain import ReauthRequired, TokenGrant
from mindos.integrations.auth.loopback import LoopbackError, LoopbackReceiver
from mindos.integrations.auth.oauth2 import OAuth2Client
from mindos.integrations.auth.pkce import challenge_for, new_state, new_verifier


async def sign_in(
    client: OAuth2Client,
    scopes: Sequence[str],
    *,
    open_url: Callable[[str], object],
    say: Callable[[str], None],
    timeout_s: float = 300.0,
    login_hint: str | None = None,
) -> TokenGrant:
    """Run one authorization-code + PKCE sign-in. `state` and the verifier never leave this call."""
    state, verifier = new_state(), new_verifier()
    receiver = LoopbackReceiver(state)
    redirect_uri = await receiver.start()
    try:
        url = client.authorization_url(
            redirect_uri=redirect_uri,
            scopes=scopes,
            state=state,
            challenge=challenge_for(verifier),
            login_hint=login_hint,
        )
        say("Opening your browser to sign in. If it does not open, visit:\n" + url)
        open_url(url)
        try:
            callback = await receiver.wait(timeout_s)
        except LoopbackError as exc:
            raise ReauthRequired(client.name, str(exc)) from exc
    finally:
        await receiver.close()
    return await client.exchange(code=callback.code, verifier=verifier, redirect_uri=redirect_uri)
