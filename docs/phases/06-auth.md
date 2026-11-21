# 06 · Auth Lifecycle (Google, Microsoft)

Maps to ARCHITECTURE §6, §11.

## Skills
| Skill | Source |
|---|---|
| `entra-app-registration` | `microsoft/azure-skills` (753K) |
| `security-setup` | local |
| `code-review` | local |
| `python-testing-patterns` | `wshobson/agents` |

## Build
- `CredentialProvider` port; Keychain (`keyring`) store; access tokens in memory only.
- Google: Desktop client, auth code + **PKCE**, loopback redirect, incremental scopes (mail-read / compose / send, calendar-read / write), offline refresh token.
- Microsoft: MSAL public client, PKCE, delegated only, token cache → Keychain, work/school only, surface `consent_required`.
- Single-flight refresh: `asyncio.Lock` + Postgres advisory lock per account.
- `integration_states`: `CONNECTED / NEEDS_REAUTH / NEEDS_SCOPE / REVOKED` (no secrets).
- `ops auth <provider>`, `status`, `revoke`.
- Reauth resume: `AWAITING_AUTH` execution re-run only for blocked call; `AuthRequired/Restored` events; TUI inline auth panel.
- Circuit breaker + retry (full jitter, `Retry-After`) shared module in `infra/`.

## Verify first (open in ARCHITECTURE §17.3)
- Consent-screen publish status vs refresh-token lifetime (Testing mode = 7-day tokens).
- Gmail restricted-scope verification for intended distribution.

## Gate
- Failure injection: `401`, `invalid_grant`, `429`, network drop, concurrent refresh.
- Secret-leak scan of logs/checkpoints/tool outputs = clean.
