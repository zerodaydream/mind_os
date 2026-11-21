# ADR 0002: Transcript is a virtualized line-rendered view, not one widget per block

Status: Accepted (Phase 01 spike 5, `spikes/s05/`)
Date: 2026-11-22
Versions tested: `textual` 8.2.8, Python 3.12, headless `run_test`, 120x40.

## Context
ARCHITECTURE §13.3 requires smooth streaming and long sessions. The obvious Textual design mounts one widget per transcript block.

## Measurements
Streaming Markdown (`Markdown.get_stream`) into the last block, flush every ~16 ms. Bare `pilot.pause()` overhead is about 21 ms, so subtract that.

| Transcript design | Blocks | Flush p50 | Flush p95 |
|---|---|---|---|
| Widget per block | 100 | 68 ms | 91 ms |
| Widget per block | 1,000 | 120 ms | 153 ms |
| Widget per block | 5,000 | 544 ms | 764 ms |
| `ScrollView` + `render_line` over a list model | 10,001 lines | 25 ms | 44 ms |
| same | 100,001 lines | 25 ms | 44 ms |

Mounting 5,000 widgets took 3.3 s. The virtualized view "set" 100k lines in 0.07 s. Page-up over 100k lines: 20 steps in 0.70 s. Peak memory with 5,000 widgets: about 208 MB.

## Decision
- The transcript is one `ScrollView` subclass that renders visible lines from the immutable view-model (§13.3). Cost does not grow with history.
- Only the prompt box, spinner, status line, and the **active** approval dialog are real widgets.
- Rich renderables (Markdown, syntax, diffs) are converted to cached line lists per block and re-rendered only for the streaming block and on width change.
- Collapse/expand (ctrl+o) is a view-model change, not widget mounting.
- Text selection and copy are implemented in the view (needed anyway for fullscreen parity).

## Not yet measured
- Rich Markdown and syntax-highlight cost per block, with a real width reflow.
- Mouse selection, find-in-transcript. These are Phase 02 work items with their own gates.

## Consequences
- More custom code than composing widgets; this is the cost of Claude Code-like scale and feel.
- The Phase 02 gate adds: 50k-line transcript, streaming flush p95 < 50 ms.
