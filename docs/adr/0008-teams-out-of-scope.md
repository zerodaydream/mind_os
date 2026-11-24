# ADR 0008: Microsoft Teams is out of scope

Status: Accepted
Date: 2026-11-25

## Decision
The user does not want Teams. All Teams and Microsoft code is removed: the Microsoft OAuth provider
and its scopes, `MINDOS_MS_*` settings, `mindos auth microsoft`, the `teams.send_message` approval label,
and the related tests. The generic `OAuth2Client`, `OAuthProvider` port, `TokenManager`, and
`AuthService` stay; a provider is one entry in `integrations/auth/providers.py`.

## Consequences
- Providers: Google only. `ConsentRequired` stays for Google Workspace policy blocks
  (`admin_policy_enforced`).
- Docs that describe Teams (ARCHITECTURE §1-§7, §14, §17; ADRs 0001-0007; older phase files) are left
  as history. Where they conflict, this ADR wins. README, MISSION, and phases 06 and 07 are updated.
- Revisit by writing a new ADR. The earlier design (Entra public client, delegated `Chat.*` and
  `ChatMessage.Send`, work or school accounts only) is in ADR 0006's history and git.
