# 06 · Auth Lifecycle (Google; Microsoft removed, ADR 0008)

Maps to ARCHITECTURE §6, §11.

## Skills
| Skill | Source |
|---|---|
| `security-setup` | local |
| `code-review` | local |
| `python-testing-patterns` | `wshobson/agents` |

## Build
- `CredentialProvider` port; Keychain (`keyring`) store; access tokens in memory only.
- Google: Desktop client, auth code + **PKCE**, loopback redirect, incremental scopes (mail-read / compose / send, calendar-read / write), offline refresh token.
- Microsoft: dropped with Teams (ADR 0008).
- Single-flight refresh: `asyncio.Lock` + Postgres advisory lock per account.
- `integration_states`: `CONNECTED / NEEDS_REAUTH / NEEDS_SCOPE / REVOKED` (no secrets).
- `mindos auth <provider>`, `status`, `revoke`.
- Reauth resume: `AWAITING_AUTH` execution re-run only for blocked call; `AuthRequired/Restored` events; TUI inline auth panel.
- Circuit breaker + retry (full jitter, `Retry-After`) shared module in `infra/`.

## Verify first (open in ARCHITECTURE §17.3)
- Consent-screen publish status vs refresh-token lifetime (Testing mode = 7-day tokens).
- Gmail restricted-scope verification for intended distribution.

## Gate
- Failure injection: `401`, `invalid_grant`, `429`, network drop, concurrent refresh.
- Secret-leak scan of logs/checkpoints/tool outputs = clean.

## Status (2026-11-25)
Decisions: `docs/adr/0006-auth-lifecycle.md`.

Built and tested against scripted token endpoints (no live provider yet):
- `CredentialProvider` chain (Keychain first), `SecretStore` over `keyring`, tokens hidden from `repr`.
- `OAuth2Client` (PKCE, loopback redirect that accepts one request with the right `state`),
  Google as data, capability-to-scope tables, incremental consent.
- `TokenManager`: proactive and reactive refresh, single flight (in-process lock + Postgres
  advisory lock), status transitions, refusal of known-bad accounts. `AuthService`: connect, revoke.
- `integration_states` repository; `infra/resilience.py` (full jitter, `Retry-After`, breaker).
- `mindos auth google|status|revoke|gemini|langsmith`. The engine maps `AuthError` to typed results
  and `AuthRequired`.
- Gate: failure injection for `invalid_grant`, `429`/`5xx`, network drop, open breaker, concurrent
  refresh; secret-leak checks of repr, errors, and logs.

Not done: `AWAITING_AUTH` re-run, TUI auth panel, `AuthRestored`, `mindos doctor` integration rows,
and any adapter that uses the tokens. See ADR 0006. Needs a live sign-in to call this phase verified.
