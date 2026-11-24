# ADR 0006: One OAuth client for Google and Microsoft; tokens only in the Keychain

Status: Accepted (Phase 06)
Date: 2026-11-25

## Context
ARCHITECTURE 6 asks for authorization code + PKCE with a loopback redirect for Google, and MSAL for
Microsoft. Both providers speak the same protocol, and MSAL would add a second token cache, a
second retry story, and a dependency whose cache format we would have to put in the Keychain.

## Decision
- **One `OAuth2Client`** in `integrations/auth/` serves both providers. Each provider is data
  (`OAuthEndpoints`, a capability-to-scope table). This replaces MSAL: Entra v2 public clients use
  the plain authorization-code + PKCE flow, so nothing MSAL adds is needed here. If a tenant needs
  something MSAL does (device code, broker sign-in), add it behind the same `OAuthProvider` port.
- **Tokens.** The refresh token is written to the Keychain (`refresh:<provider>:<account>`) and
  read only by `TokenManager`. Access tokens live in memory. `AccessToken` and `TokenGrant` hide
  their values from `repr`, so a log line or traceback cannot print one. The database holds
  `integration_states` (status and scopes) and nothing secret.
- **Single flight.** An `asyncio.Lock` per account, then a Postgres transaction-scoped advisory
  lock, so ten callers (or the app and the worker) cause one refresh. A crash cannot leave the
  lock held. Cached tokens refresh 60 s before expiry, and a 401 from an API drops the cache.
- **Status drives behaviour.** `invalid_grant` becomes `NEEDS_REAUTH`, `invalid_scope` becomes
  `NEEDS_SCOPE`, tenant consent is `ConsentRequired` (the sign-in is not broken). An account that
  is not `CONNECTED` refuses calls without asking the provider again. Outages never change status.
- **Resilience.** `infra/resilience.py` provides full-jitter retry (honouring `Retry-After`, with a
  total deadline) and a circuit breaker with one half-open probe. A definite answer from the
  provider is never retried and does not count against the breaker.
- **Engine.** An `AuthError` raised by an adapter becomes a typed tool error
  (`auth_required`, `needs_scope`, `consent_required`, `auth_unavailable`) and emits
  `AuthRequired` for the first three. It is never retried and tells the model not to retry.
- **Gemini key.** `ops auth gemini` stores it in the Keychain, and the Keychain wins over `.env`.
- **Revocation.** `ops auth revoke` always deletes locally. Google is revoked at the provider.
  Entra v2 has no revocation endpoint, and the command says "removed locally" for it.

## Not done here (honest list)
- **`AWAITING_AUTH` resume** (ARCHITECTURE 6.3): the blocked call is not parked and re-run after
  sign-in; the model is told to ask the user, and the user repeats the request. The execution log
  has the state; nothing sets it yet. Do this with the first adapter that has a write tool.
- **TUI inline auth panel**, and `AuthRestored`: the event exists and `AuthRequired` is emitted,
  but the UI does not render either yet.
- **Adapters.** No Gmail, Calendar, or Graph adapter calls `TokenManager` yet (Phase 07). The calendar
  tools still read a fixture.
- **Live verification.** Nothing here has run against Google or Entra. The flow is tested against
  scripted token endpoints. First live sign-in is the real test.
- **Open facts to check** (ARCHITECTURE 17.3): refresh-token lifetime in Google's "Testing"
  consent mode (7 days), and Gmail restricted-scope verification for wider distribution.
