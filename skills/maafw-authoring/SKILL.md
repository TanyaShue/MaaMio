---
name: maafw-authoring
description: Author or repair MaaFramework (MaaFW / ProjectInterface v2) pipelines and templates by walking the real UI first with adb, then writing nodes and testing them one step at a time on a real device through maactl. Use when adding a task, writing pipeline nodes, sizing thresholds, or diagnosing why a node never fires, fires at the wrong moment, or gets stuck on a modal dialog. Covers one-shot resources (mail, unread badges), modal popups that silently eat taps, and when to stop and ask the human.
---

# Authoring MaaFramework pipelines

A pipeline is a state machine over **screen recognitions**, driven by `maactl`. The work is:
walk the real UI, write two files (`tasks/<task>.json` + `<resource>/pipeline/<task>.json`),
run it, and fix what the trace says is wrong. Do not build verification frameworks around the
task; do not invent abstractions. The deliverable is the two JSON files plus the templates.

## The loop

1. **Walk the flow by hand first, with adb.** One step at a time, screenshotting as you go.
   Never reason about a screen you have not actually looked at.
2. **Crop only the anchors you need** into `<resource>/image/<feature>/<NodeName>_1.png`.
3. **Write the two files.**
4. **Run it and read the trace** — the run log records per-recognition
   `best=<score> thr=<threshold> OK|MISS`. Fix whatever MISSes.
5. **Repeat**, and always finish by re-running in the goal state: the second run should be a
   clean no-op, because that is what a user hits every day after.

## Looking at the screen properly

- **Do not eyeball coordinates.** Crop the region, upscale 2–3×, draw a labelled 20 px grid, and
  read the numbers off that. Rendered previews are scaled and estimates *will* be wrong.
  Real failures: a button read as "y≈918" was at 878; a list row read as "y≈808" was 743.
- **Measure repeated rows from their separators**, not by assuming even spacing — scan the band
  for horizontal dashed lines (rows of low-luminance pixels) and take each band's centre.
- **Take a burst, not a single frame**, for anything that transitions (launch, loading, reward).
  Build a contact sheet so you can see the whole timeline in one look.
- **Prefer anchors that are static, opaque, and exist in exactly one state.** A screen title that
  is still visible while a detail panel is open cannot answer "which page am I on?".
  Semi-transparent overlays make scores swing wildly from frame to frame.

## Popups: the failure that looks like nothing is wrong

**Any claim/reward/confirm action may raise a modal that covers the whole screen.** Every
subsequent tap lands on the modal instead of the UI behind it.

- **Symptom:** recognition keeps succeeding (the button underneath is still visible and scores
  high) but the click does nothing. The task stalls, or re-enters the same node, or the next
  node's click silently misses and the flow dies far from the real cause.
- **Therefore: assume `claim → popup → dismiss → continue`.** Never assume a claim returns you
  straight to the previous screen.
- **Recognise the popup by something stable inside it** (a banner title, a fixed button) — not by
  the reward icons, which change every time. Verify the template on *two* different popups if you
  can, and on frames where it must not fire.
- **Dismiss it via the popup's own affordance** ("tap to continue" text or the modal body).
- **Give the dismissal node an `on_error` fallback** so "there was no popup this time" routes to
  the next step instead of failing the task.
- Popups are also how you get *stuck mid-run*: a dialog offering to buy something must be
  dismissed, not accepted. Read every new dialog before clicking through it.

## Thresholds

- **Check both sides.** Score the template on a screen where it *must* fire and on one where it
  must *not*. Real case: a "back" button template scored **0.6972** on the list screen, where that
  same rectangle is the **delete** button, against a threshold of 0.7 — a 0.003 margin from
  destroying data.
- **Leave a wide margin**, and prefer fixing the anchor over widening the threshold. Widening a
  threshold to make something pass is a smell, not a fix.
- A blind click is acceptable **only where the position is inert** when the target is absent.
  Where a destructive button shares the rectangle, require a recognition.

## MaaFW specifics worth remembering

- **No loops.** Repeated rows/steps must be unrolled into the state machine.
- **`timeout` belongs to the node whose `next` list is being polled**, not to the candidate.
  Every node that waits must set it explicitly; the default is far too short for an app launch.
- **`on_error` means "this step didn't apply — carry on elsewhere"**, e.g. an optional popup, or
  a row with nothing to do. Use it instead of letting a node time out and fail the whole task.
- **`next` order is priority.** The first candidate that recognises wins.
- **`[JumpBack]Child`** runs Child and then returns to the parent's `next` list — the tool for
  "handle an interruption, then resume what I was doing".
- **OCR is the right tool for text-driven state** (`expected` regex); template matching for fixed
  glyphs, buttons and icons. Full-screen OCR also works as a "what is on screen right now" probe.
- **Node names must be unique across the whole resource pack**; a dangling name is undefined
  behaviour, so always verify referenced names exist.
- **Coordinates live in the display-scaled space** (`display_short_side`), not raw screenshot
  pixels. Templates must be crops of the image at that scale.
- **`inverse: true` on `DirectHit` can never match** (DirectHit is always true).

## When to stop and ask the human

Ask **before** acting when any of these is true — these are the moments where guessing costs data
or money:

- **The goal is under-specified**: which stage/difficulty/mode, what counts as "done", what to do
  when a resource runs out, whether a fallback is acceptable.
- **The action consumes something one-shot or irreversible**: claiming a reward, deleting mail,
  spending a limited currency, using a consumable, overwriting a save.
- **You would have to pick a policy the spec doesn't state**: claim everything vs. only items;
  delete after claiming; stop at first failure or continue.
- **The on-screen state is not one you recognise** — an unknown dialog, an event popup, a
  maintenance notice, a different region/map. Do not click through it hoping.
- **You are about to widen a threshold or add a retry** to make something pass. That means the
  anchor is wrong; say so instead.
- **You have two plausible fixes and one of them is destructive.** Ask which.

Do *not* ask about things you can find out yourself: take a screenshot, run the node, read the
log. Ask only when the answer changes what you should do and you cannot determine it.

## Testing without destroying your test data

- One-shot resources (mail rewards, unread badges, first-clear bonuses) do not come back.
  **Verify that you can reach a step before writing the step that consumes it.**
- If a feature can be triggered more than once (a renewable daily, a repeatable stage), prefer it
  as the test bed for shared mechanics such as popups.
- Keep the state recoverable: know how to get back to a known screen before you start clicking.

## Reporting

State the node graph, each anchor's score where it must fire **and** where it must not, the
thresholds and why, and the measured end-to-end result including the no-op re-run.
Never call a template "working" on the strength of a single screenshot.
