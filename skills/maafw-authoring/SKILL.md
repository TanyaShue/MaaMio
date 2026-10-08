---
name: maafw-authoring
description: Author or repair MaaFramework (MaaFW / ProjectInterface v2) pipelines and template images with evidence instead of guesswork. Use when adding a task, writing pipeline nodes, cropping recognition templates, diagnosing why a node never matches, or driving a real device through maactl. Covers the capture -> crop -> verify-separation -> author -> run -> read-trace loop, and the failure modes that silently poison template thresholds.
---

# Authoring MaaFramework pipelines

A MaaFW pipeline is a state machine over **screen recognitions**. Almost every real bug is a
recognition that fires when it should not, or does not fire when it should. Both are invisible
unless you measure, so this skill is built around one number: **separation**.

## The rule that matters

> A template is only usable if its **worst positive frame** still scores **above** its
> **best negative frame**, with margin. Require **separation > 0.15**.

A template that scores `1.0000` on your one screenshot proves nothing. Real UI has
semi-transparent overlays, panning art, fades and animations; the same template can score `0.40`
on another frame of the _same_ screen. Never pick a threshold from a single frame.

## Loop

1. **Orient.** Find the project root (has `interface.json`), the resource pack
   (`resource/<pack>/pipeline/**`), and the template dir (`resource/<pack>/image/**`).
   Coordinates are in the _scaled_ space given by `display_short_side` (default 720 short side),
   **not** raw screenshot pixels.

2. **Capture, and capture sequences.** One screenshot tells you a layout; a _sequence_ tells you
   what moves. For any transition (launch, loading, login, scene change) take a burst of frames
   and build a contact sheet to read the timeline at a glance. Never reason about a transition
   from a single frame.

3. **Choose anchors.** Prefer static, opaque, screen-unique UI.
    - Avoid semi-transparent strips, animated banners, and anything reused across screens.
    - **Check for reuse across screens explicitly** — a chest labelled "daily supply" that appears
      on both the hub and the room is a guaranteed false anchor.
    - If a wide element drifts, crop **tighter onto opaque glyphs** rather than lowering the
      threshold. A tighter crop of an opaque part is often the whole fix.

4. **Crop to the project's convention**: `resource/<pack>/image/<feature>/<NodeName>_1.png`,
   written as `"<feature>/<NodeName>_1.png"` in the node's `template` field.

5. **Verify separation** against labelled frame sets before writing anything:

    ```
    maa.py verify --template <tpl.png> --pos <dir-or-glob> --neg <dir-or-glob> --threshold 0.7
    ```

    Label frames by _what is on screen_, not by what you hope. Report the frame that achieves the
    worst positive score — if it is a transition frame that legitimately contains the target, it
    belongs in the positive set.

6. **Author the node.** Keep the entry node's `next` list ordered by "already in the goal state
   first", so re-running the task is idempotent. Give **every node that waits** a generous
   `timeout`: the `next` list is only polled until _that node's_ timeout expires, so the default
   20 s cannot cover a cold app launch.

7. **Validate, then run.** Schema-check first (fast, read-only). Then run the task through
   `maactl` with a **per-run log directory** and read the trace.

8. **Read the trace, not the vibes.** The trace gives node sequence plus, per recognition,
   `best=<score> thr=<threshold> OK|MISS`. A node that reported MISS many times and then OK is
   normal for a polling flow — only the final result matters. A node that never fired is the bug.

## Where the evidence lives

- **`maafw.log` in the run's log dir is the ground truth.** It records, per recognition attempt,
  the full candidate list: `TemplateMatcher::analyze ... [all_results_=[{"box":...,"score":...}]]
[param_.thresholds=[...]]`, and for OCR `[all_results_=[{"text":"...","box":...,"score":...}]]`.
  This means you can answer "why did this never match" and "what text is on screen" without any
  extra vision stack. Parse it, don't eyeball it.
- **`log_dir/screencap/`** holds frames written by the `Screencap` action — the _exact_ frame the
  engine saw, which is how you compare an offline score against the engine's own.
- **Probe nodes through the overlay mechanism** (`maactl -ol <dir>`) let you ask the device
  questions — "can OCR read this text", "what score does this template really get" — **without
  polluting the shipped resource pack**. Prefer this over guessing.

## Failure modes to check before anything else

| Symptom                                             | Cause                                                                                                                                                                |
| --------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Template scores 1.0 on a black/white/loading screen | A constant _template_ is defined to score 1.0 (matches everywhere). A constant _window_ scores 0. Verify your offline scorer reproduces both, or it will lie to you. |
| Score swings 0.4–1.0 across frames of one screen    | Semi-transparent overlay: background art bleeds through. Crop tighter onto opaque pixels.                                                                            |
| Node never fires though the button is clearly there | Threshold picked from a single frame; or `timeout` too short for the preceding transition; or a `roi` that clips the target.                                         |
| Task "succeeds" instantly with no action            | A `DirectHit` (or an `inverse` on it) short-circuited the chain. `DirectHit` + `inverse: true` can never match.                                                      |
| Node names collide / dangling reference             | Node names must be unique across the whole resource pack; a string `roi`/`target` naming a node whose result is empty makes the recognition fail.                    |

## Environment traps (Windows)

- Force **UTF-8** on tool stdout. On a zh-CN Windows, `cp936` encodes `签到` to bytes `C7 A9`,
  which are _valid_ UTF-8 for `ǩ` — the corruption is silent, not an error.
- `cv2.imread` cannot open non-ASCII paths. Use
  `cv2.imdecode(np.fromfile(p, np.uint8), cv2.IMREAD_COLOR)`.
- Always pass **absolute** paths and use a **fresh** log directory per run: some tools clear the
  log directory they are given, destroying the previous run's evidence.
- The controller may report a landscape resolution on the emulator's home screen while the game is
  portrait. Expect the first `DirectHit` to report the launcher's resolution; it is not a bug.
- A first controller connect can fail transiently on emulators; retry once before debugging.

## Reporting

When you finish, state: the node graph you added, each anchor's **positive-min / negative-max /
separation**, the thresholds chosen and why, and the measured end-to-end result (cold-start time
and the idempotent re-run time). Never report a template as working on the basis of one screenshot.
