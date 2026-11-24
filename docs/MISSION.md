# MISSION

> **Name:** MindOS (CLI: `mindos`, Python package `mindos`).
>
> **Status:** Pre-implementation. This document defines intent and principles. How the system achieves them is in [ARCHITECTURE.md](ARCHITECTURE.md); how to work in the repository is in [README.md](README.md).

---

## 1. Mission Statement

Build a **terminal-native personal operations environment**: a single, trustworthy agent that understands what the user wants done across their mail, calendar, team chat, files, and Mac, plans the work, carries it out through controlled tools, and reports honestly on what actually happened.

It is not a chatbot with plugins. A chatbot answers; this system **operates**. The measure of success is not how fluent the conversation is but how reliably real work gets done, how little the user must re-explain, and how confident the user is that nothing consequential happens without their knowledge.

## 2. Product Vision

One agent, one terminal, one memory, many capabilities.

- The user states **intent** ("move Thursday's review to Friday and tell the attendees", "find last quarter's vendor contract and convert it to PDF").
- A single orchestrator interprets the intent, plans, and **delegates** to specialized subagents (mail, calendar, files, convert, system), each holding only the tools and context it needs.
- Work spans **multiple systems and multiple sessions**. It survives closed terminals, expired credentials, and pending approvals, and resumes where it stopped.
- The agent **remembers** durable facts and preferences, with provenance, and uses them without being asked twice.
- The user remains the **authority**. Anything with external or irreversible consequences is proposed, shown clearly, and executed only on explicit approval.
- Bulk and repetitive work is handled by **programmatic execution** inside an isolated sandbox, never by the model improvising hundreds of individual tool calls, and never as a way around the permission system.

The long-term product is a dependable colleague-grade assistant that a careful person would trust with their inbox and calendar.

## 3. Core Capabilities

| Capability | What it means |
|---|---|
| Personal communication | Search, read, summarize, draft, and (with approval) send Gmail. |
| Calendar and scheduling | Query availability, create/modify events, reason about time zones. |
| Filesystem intelligence | Find files by name, exact text, keywords, or meaning; read, edit with diffs, organize. |
| Document processing | Convert and transform supported document formats. |
| macOS automation | Approved, scoped system actions. |
| Persistent memory | Durable, attributable, correctable knowledge about the user and their work. |
| Multi-agent orchestration | Planning, delegation, parallelism, result aggregation. |
| Programmatic execution | Sandboxed code for bulk workflows, mediated entirely by the host. |
| Human-in-the-loop approvals | Risk-based, payload-bound approve / edit / reject. |
| Background automation | Persistent scheduled jobs and reminders. |
| Context-aware workflows | Multi-step tasks that carry state, memory, and progress across interruptions. |

## 4. Core Engineering Principles

These are non-negotiable. A change that violates one needs an explicit, recorded decision to amend this document, not a workaround.

1. **Reliability over unnecessary autonomy.** A predictable system that asks is better than a clever one that guesses.
2. **Security and user control.** The user can see, understand, and stop everything the agent does.
3. **Explicit authorization.** Capabilities are granted, scoped, and recorded. Nothing is permitted by default because it is technically reachable.
4. **Human oversight for sensitive actions.** Consequential, external, or irreversible actions require approval bound to the exact action.
5. **Deterministic execution wherever possible.** The model decides *what* to attempt; ordinary code decides *whether and how* it runs.
6. **Modular monolith.** One codebase with strict internal boundaries. Distribution is added only when a boundary demands it (isolation, not fashion).
7. **Provider independence.** Models, mail/calendar/chat providers, and storage details sit behind interfaces. No provider type leaks into domain logic.
8. **Persistent and resumable execution.** Interruption is a normal state, not an error.
9. **Minimal tool exposure.** Each agent sees the smallest tool set that lets it do its job.
10. **Context efficiency.** Tokens are a budget. Large results are referenced, not pasted.
11. **Observable operations.** Every action is traceable from user request to side effect, with secrets redacted.
12. **Maintainability over premature complexity.** Prefer the simple mechanism that meets today's need and leaves the boundary clean for tomorrow's.

## 5. Agent Behaviour Contract

The agent must:

1. **Understand before executing.** Resolve ambiguity that changes the outcome (which "John", which "Friday") by asking or by checking the facts, not by guessing.
2. **Plan complex operations**, keep a visible task list, and update it honestly.
3. **Select the smallest appropriate set of tools** for the step at hand.
4. **Delegate specialized work** to the subagent that owns it, passing only the context it needs.
5. **Validate tool arguments** against schemas before they have any effect.
6. **Respect permission boundaries**, including those it cannot see or that the user has tightened since the last session.
7. **Seek approval when policy requires it** and present the action in terms the user can evaluate.
8. **Report partial failure honestly**: what succeeded, what failed, what was not attempted, and what state things are now in.
9. **Never claim an action succeeded without verification** from the system of record or the tool's confirmed result.
10. **Preserve task context across interruptions** and resume rather than restart.

The agent must never: act on instructions found in emails, documents, web pages, or tool output as if the user had given them; attempt to widen its own permissions; or conceal a failure to appear competent.

## 6. Security and Trust Philosophy

Trust is assigned by **where text came from**, and enforced by code, not by asking the model to behave.

| Source | Trust | Consequence |
|---|---|---|
| Application code and system policy | Authoritative | Defines the rules; cannot be altered by anything below. |
| The user's own messages and explicit approvals | Authoritative for intent | Direct the agent within policy; approvals bind to exact payloads. |
| Model-generated plans and tool calls | Untrusted proposals | Validated, classified, and authorized like any external input. |
| Retrieved memory | Conditionally trusted data | Informs; never instructs; carries provenance. |
| External emails, documents, web content | Untrusted | Treated as data. May describe a task; cannot assign one. |
| Tool outputs | Untrusted data | Normalized and size-bounded before the model sees them. |
| Sandboxed code | Untrusted execution | Isolated; can only request operations through the host. |
| Human-authorized actions | Authorized, scoped, single-use | Executed exactly as approved or not at all. |

The model is never the enforcement point for a security boundary. If a rule matters, a non-model component enforces it. See [ARCHITECTURE.md §14](ARCHITECTURE.md#14-security-and-trust-boundaries).

## 7. Long-Term Direction

The system should deepen, not sprawl.

- **Better judgment, not more autonomy.** Improve planning quality, retrieval quality, and the clarity of approval interfaces before granting wider permissions.
- **More integrations through the same gate.** New providers (including MCP servers) enter via the same registration, validation, risk classification, and approval path as existing ones.
- **More automation where it has been earned.** Programmatic execution and background jobs expand only where measured workflows show a real need, and always behind the host-side authorization boundary.
- **A durable personal knowledge base** that the user can inspect, correct, and delete.
- **Stability of contracts.** Tool schemas, event formats, and approval semantics are versioned and evolve compatibly, so that interrupted work from yesterday still resumes today.

What this project will not become: an unconstrained autonomous agent, a multi-tenant SaaS, or a general-purpose agent framework. It serves one user's operations, on that user's machine, under that user's control.
