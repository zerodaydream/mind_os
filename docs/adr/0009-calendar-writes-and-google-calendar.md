# ADR 0009: Calendar writes, bound to the event; Google Calendar behind the port

Status: Accepted (Phase 07, step 1)
Date: 2026-11-25

## Decision

### Tools (`tools/calendar.py`)
- `calendar.create_event`, `calendar.update_event`, `calendar.delete_event`, version 1.
  Arguments are validated before anything is asked: attendee addresses, ISO times in the user's
  zone, an update must change something, an event may not end before it starts.
- **Risk** comes from who is affected, not from the model: a create with no attendees is R2 (your
  own calendar); with attendees it is R3 (an invitation reaches people); update and delete are
  always R3, because they may change other people's calendars. By default R2 and R3 both ask. The
  user may relax R2 in `policy.toml`; R3 cannot be relaxed. Reading untrusted event text taints the
  run, so even a relaxed R2 write then asks (tested).
- **Approval is bound to the event** (ADR 0005). `bind_state` fetches the event and records a label
  (title, local time, attendees) as `resolved_targets` and a fingerprint of the whole event as
  `base_state_hashes`. The approval panel shows the label. If the event changes before execution,
  the engine voids the approval and nothing runs. A missing event is refused before any approval
  is requested.
- **Idempotency.** Every create carries a key derived from `(run_id, tool_call_id)`. A repeat of the
  same call finds the event it already made instead of making another. The engine never retries a
  side effect, and a timeout or crash reports "it may or may not have happened: check before
  trying again".
- `notify_attendees` (default true) is part of the approved payload, so the user sees whether
  invitations go out.

### Ports and adapters
- `CalendarWriter` (create with `request_key`, `find_created`, update, delete) sits beside
  `CalendarReader`; `Calendar` is both. `FixtureCalendar` implements both for development and tests.
- `GoogleCalendar` (`integrations/google/calendar.py`) implements both over the REST API with a
  `TokenSource`. A 401 drops the token and retries once; two 401s are `ReauthRequired`. A 403 for a
  missing scope is `ScopeRequired` (the engine emits `AuthRequired` with the scope). Reads retry
  transient faults with full jitter and `Retry-After`. **Writes retry only what provably never
  left this machine** (connection refused, 429). A create that fails ambiguously is reconciled:
  the event carries a private extended property with its key, and the adapter looks it up by
  that key before reporting failure, so it neither duplicates nor lies.
- Reads need `calendar.readonly`; writes need `calendar.events`. Enable both:
  `mindos auth google -c calendar-read -c calendar-write`.
- `bootstrap` uses Google when `MINDOS_GOOGLE_CLIENT_ID` is set (not signed in: tools return the
  sign-in instruction); otherwise the sample calendar.
- `ProviderError` (domain) is how adapters report a refused or failed request. Transient ones are
  retried by the engine for read tools only.

### Who may write
Only the calendar subagent holds the write tools, and only when a writer is wired. Its approvals
are deferred (ADR 0005): the subagent queues the change and stops, the orchestrator tells the user
what is waiting, and the application runs the approved call. The model is not told the outcome.

## Not done / not verified
- Nothing has run against the real Google Calendar API; the adapter is tested against a scripted
  server (mapping, auth, retries, reconcile).
- No `If-Match`: an update may overwrite a change made in the instant between verification and the
  call (the state check just before it makes the window small, not zero). Add an etag when the
  event model carries one.
- Recurring events are treated as the instance Google returns (`singleEvents=true`); editing a
  whole series is not supported.
- The subagent cannot be given the outcome of an approved change. If that matters, add a follow-up
  turn that reads the calendar.
- No eval scenarios against a real model yet (phase gate asks for five, including injection).
  Injection is covered deterministically by the taint tests.
