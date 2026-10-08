---
name: maafw-authoring
description: Author or repair MaaFramework (MaaFW / ProjectInterface v2) pipelines and templates by walking the real UI first with adb, then writing nodes and testing them one step at a time on a real device through maactl. Use when adding a task, writing pipeline nodes, sizing thresholds, or diagnosing why a node never fires or fires at the wrong moment. Covers one-shot resources (mail, unread badges, one-time popups) and the modal-popup trap that silently eats every later tap.
---

# Authoring MaaFramework pipelines

A pipeline is a state machine over **screen recognitions**, driven by `maactl`. The work is:
understand the real UI by walking it, then write nodes and fix them from run evidence.
Do not build verification frameworks around the task — write `tasks/*.json` and
`pipeline/*.json` well, and read the trace.

## Loop

1. **Walk the flow by hand first, with adb.** One step at a time, screenshotting as you go.
   Never reason about a screen you have not actually looked at.
    - **Do not eyeball coordinates.** Crop the region, upscale it, draw a labelled 20px grid, and
      read numbers off that. Rendered previews are scaled and estimates will be wrong.
      (Real cases: a button read as "y≈918" was at 878; a list row read as "y≈808" was 743.)
    - **Measure list rows from their separators**, not by even spacing — scan for the horizontal
      dashed lines (rows of low-luminance pixels) and take each band's centre.
    - **When you hit a choice, stop and ask.** Claim-all vs. only-attachments, delete vs. keep,
      whether a one-shot or destructive step may be consumed for testing.
2. **Crop only the anchors you need**, into `<resource>/image/<feature>/<NodeName>_1.png`.
3. **Write the two files**: `tasks/<task>.json` (registration) and
   `<resource>/pipeline/<task>.json` (nodes). Nothing else is required.
    - **There are no loops.** Repeated rows/steps must be unrolled into the state machine.
    - **Every node that waits must set `timeout`.** A node's `next` list is only polled until
      _that node's_ timeout expires; the default (20 s) will not cover an app cold start.
    - Route "nothing to do here" cases through `on_error` so a step can be skipped rather than
      failing the whole task.
4. **Run it and read the trace.** `maactl run -t <task>` plus the run's `maafw.log`, which records
   per-recognition `best=<score> thr=<threshold> OK|MISS`. Fix whatever MISSes.
5. **Repeat 3–4**, and always re-run once so the task is a no-op (already in the goal state) —
   that is the path a user hits on every subsequent day.

## Discipline that prevents real breakage

- **One-shot resources need step-by-step verification.** Mail rewards, unread badges and one-time
  popups do not come back. Verify "can I even reach this step" _before_ writing the step that
  consumes it, or one bad run destroys the test data permanently.
- **After every claim, expect a modal popup — dismiss it before anything else.** The game in this
  project shows 「获得物品」 + `触·摸·继·续` full-screen after any reward, and it **eats every
  subsequent tap**: the next node's click silently does nothing and the task stalls mid-screen.
  This was by far the most expensive bug found.
- **Check both sides of a threshold.** Knowing the score where it _should_ match is not enough.
  Real case: the "back" button template scored **0.6972** on the list screen — where that same
  rectangle is the **delete** button — against a threshold of 0.7. A 0.003 margin from deleting mail.
  Always score the template on a screen where it must NOT fire, and leave a wide margin.
- **A wrong-but-plausible anchor is worse than no anchor.** A screen title that remains visible while
  a detail panel is open cannot answer "am I on the list page?". Choose an element that exists in
  exactly one state.
- **Blind clicks are only acceptable where the position is inert.** Clicking "collect" when that
  button is absent is harmless only if nothing else lives there; clicking where a destructive button
  sits is not. Prefer a recognition whenever the position is shared.

## Reporting

State the node graph, each anchor's score where it must fire **and** where it must not, the
thresholds and why, and the measured end-to-end result including the no-op re-run.
Never call a template "working" on the strength of a single screenshot.
