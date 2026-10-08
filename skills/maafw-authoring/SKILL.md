---
name: maafw-authoring
description: Author or repair MaaFramework (MaaFW / ProjectInterface v2) pipelines and templates by walking the real UI once with adb, landing a runnable skeleton fast, then fixing it from run traces on a real device through maactl. Use when adding a task, writing pipeline nodes, sizing thresholds, or diagnosing why a node never fires, fires at the wrong moment, or gets stuck on a modal dialog. Covers one-shot resources, modal popups that silently eat taps, and when to stop and ask the human.
---

# Authoring MaaFramework pipelines

A pipeline is a state machine over **screen recognitions**, driven by `maactl`. The deliverable is
exactly two files (`tasks/<task>.json` + `<resource>/pipeline/<task>.json`) plus the template crops.

**The run is the test.** Your job is to get something runnable in front of the device as fast as
possible, then let the trace tell you what is wrong. Everything below exists to keep you from
turning that around.

## The loop — deliverable first

1. **Walk the path once, by hand, with adb.** One pass, screenshotting each step. You need the
   screens and rough coordinates — not proof that anything is correct. **Time-box this.**
2. **Crop the anchors you saw.** Rough crops are fine; they get fixed in step 4.
3. **Write both files now** and make them runnable. Placeholder thresholds (0.7) and approximate
   coordinates are fine. An imperfect pipeline that runs is worth far more than a perfect plan.
4. **Run a slice, read the trace, fix.** `maactl run -n <node>` or `--stop-after` to run only the
   part you are working on. The trace shows every recognition as `best=<score> thr=<threshold> OK|MISS`.
   Fix the MISSes. Repeat.
5. **Run end to end, then run it again in the goal state.** The second run must be a clean no-op —
   that is what a user hits every day after.

If you have spent a long time without a runnable file on disk, you are doing it wrong. Stop
exploring and land the skeleton.

## What NOT to do (the trap this skill exists to prevent)

- **Do not two-sided-score every template up front.** A missing or wrong anchor shows up as `MISS`
  in the trace, and fixing it is a one-line change. Scoring 16 templates against every other screen
  costs hours and finds nothing the first run would not have told you in a minute.
- **Only pre-check the dangerous positions**: places where a false match clicks something
  destructive (delete, sell, buy, consume, spend currency). That is a small minority and the check
  is worth it. Everything else: let the run speak.
- **Do not build harnesses** — batch scorers, positive/negative sample sets, parity tests, overlay
  probe pipelines. If you need a number once, call the matcher once.
- **Do not re-screenshot what you already have.** Reuse the frames on disk.
- **Do not "verify" a step by doing it manually again** when the pipeline can do it and tell you.

## Looking at the screen properly

- **Do not eyeball coordinates.** Crop the region, upscale 2–3×, draw a labelled 20 px grid, read the
  numbers off that. Real failures: a button read as "y≈918" was at 878; a list row read as "y≈808"
  was 743.
- **Measure repeated rows from their separators**, not by even spacing — scan the band for
  horizontal dashed lines and take each band's centre.
- **Take a burst for anything that transitions** (launch, loading, reward) and build one contact
  sheet. One image beats ten reads.
- **Prefer anchors that are static, opaque, and exist in exactly one state.** A screen title that is
  still visible under a detail panel cannot answer "which page am I on?".

## Popups: the failure that looks like nothing is wrong

**Any claim/reward/confirm action may raise a modal covering the whole screen.** Every later tap
lands on the modal instead of the UI behind it.

- **Symptom:** recognition keeps succeeding (the button underneath is still visible and scores high)
  but the click does nothing — the task stalls, re-enters the same node, or dies far from the cause.
- **Always write `claim → popup → dismiss → continue`.** Never assume a claim returns you to the
  previous screen.
- **Recognise the popup by a stable element inside it** (banner title), never by the reward icons.
- **Dismiss via the popup's own affordance** ("tap to continue" text or the modal body).
- **Give the dismissal node an `on_error` fallback** so "no popup this time" continues instead of
  failing.
- Popups also _strand_ mid-run: a dialog offering to buy something must be closed, not accepted.
  Read every new dialog before clicking through it.
- **Order matters:** if a node's own anchor is still visible under the popup, the popup handler must
  come first in the parent's `next` list.

## Thresholds

- **Check both sides only where it matters** (see above), and leave a wide margin. Real case: a
  "back" button template scored **0.6972** on the list screen, where that same rectangle is the
  **delete** button, against a threshold of 0.7 — a 0.003 margin from destroying data.
- **Prefer fixing the anchor over widening the threshold.** Widening a threshold to make something
  pass is a smell, not a fix.
- A blind click is acceptable **only where the position is inert** when the target is absent.

## MaaFW specifics worth remembering

- **No loops.** Repeated rows/steps must be unrolled into the state machine.
- **`timeout` belongs to the node whose `next` list is polled**, not to the candidate. Every node
  that waits must set it; the default is far too short for an app launch.
- **`on_error` means "this step did not apply — carry on elsewhere"** (optional popup, empty row).
- **`next` order is priority.** First candidate that recognises wins.
- **`[JumpBack]Child`** runs Child and returns to the parent's `next` list — for "handle an
  interruption, then resume".
- **OCR for text-driven state** (`expected` regex), **template match for fixed glyphs/icons**.
  OCR on a status bar is often a cleaner stop condition than any visual check.
- **Make stop conditions structural, not reactive.** Prefer "read the value, only act when it is
  enough" over "act, then handle the failure dialog". Then the failure path never executes.
- **Node names must be unique across the whole resource pack.**
- **Coordinates live in the display-scaled space** (`display_short_side`), not raw pixels.
- **`inverse: true` on `DirectHit` can never match.**

## When to stop and ask the human

Ask **before** acting when guessing costs data or money:

- **The goal is under-specified**: which stage/difficulty/mode, what counts as done, what to do when
  a resource runs out.
- **The action is one-shot or irreversible**: claiming a reward, deleting mail, spending a limited
  currency, using a consumable.
- **You would have to pick a policy the spec does not state.**
- **The on-screen state is not one you recognise** — unknown dialog, event popup, maintenance
  notice, a different region. Do not click through it hoping.
- **You are about to widen a threshold or add a retry** to make something pass.
- **Two plausible fixes, one destructive.**

Do _not_ ask about anything you can find out yourself: screenshot, run the node, read the log.
Ask only when the answer changes what you should do and you cannot determine it.

## Testing without destroying your test data

- One-shot resources (mail rewards, unread badges, first-clear bonuses) do not come back.
  **Verify you can reach a step before writing the step that consumes it.**
- For anything costly or limited (energy, tickets, currency), **validate navigation first without
  spending** — run only up to the point of consumption. Spend once, at the end, on the full run.
- Prefer a renewable trigger as the test bed for shared mechanics like popups.

## Reporting

State the node graph, the trace evidence for each step (scores where it fired, MISSes and how you
fixed them), the thresholds and why, and the measured end-to-end result including the no-op re-run.
Never call a template "working" on the strength of a single screenshot.
