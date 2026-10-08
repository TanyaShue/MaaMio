#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""maa.py — 给 agent 用的 MaaFramework 编写工具链。

Agent toolkit for authoring MaaFramework pipelines in this project. Every
subcommand is a thin, scriptable wrapper around primitives the agent needs to
close the loop "看屏幕 -> 造模板 -> 写 pipeline -> 跑 -> 读证据 -> 改":

    capture   把设备当前画面存成 PNG（adb screencap）
    crop      从截图里切出模板图，按项目约定放进 resource/base/image/<组>/
    match     离线模板匹配（不需要设备），用来定 ROI / 阈值
    trace     把 maafw.log 解析成「节点级时间线」，这是唯一的诊断真相来源
    run       用 maactl 跑 task / node（每次一个独立日志目录 + 自动 trace）
    probe     借用 -ol 覆盖层跑 agent 专用探测节点（如全屏 OCR）
    doctor    环境自检

设计约束（与项目现状对齐）：
  * 坐标是「短边 720」的设计分辨率坐标，与 interface.json 的 display_short_side 一致。
  * maactl 的 -log <dir> 会清空该目录，所以每次 run 必须用独立目录，否则证据会被覆盖。
  * 中间产物一律写在 .agent/ 下（已 gitignore），不污染可发布的 resource/。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone

# Windows 中文环境里 sys.stdout 默认是 cp936：中文会被编成 GBK 字节，而 agent 侧按 UTF-8
# 解码，于是「签到」变成「ǩ」（0xC7A9 恰好是合法 UTF-8）。必须显式改成 UTF-8。
# On zh-CN Windows sys.stdout defaults to cp936; GBK bytes 0xC7A9 are *valid* UTF-8
# for U+01E9, so the text silently garbles instead of failing. Force UTF-8.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))          # 项目根目录
AGENT_DIR = os.path.join(ROOT, ".agent")
SHOT_DIR = os.path.join(AGENT_DIR, "shots")
RUN_DIR = os.path.join(AGENT_DIR, "runs")
IMAGE_ROOT = os.path.join(ROOT, "resource", "base", "image")
PI_CONFIG = os.path.join(ROOT, "config", "maa_pi_config.json")
MATCH_PY = os.path.join(HERE, "match.py")

# ---------------------------------------------------------------- 基础设施 --

def die(msg: str, code: int = 1) -> "None":
    print(f"error: {msg}", file=sys.stderr)
    raise SystemExit(code)


def ensure_dir(path: str) -> str:
    os.makedirs(path, exist_ok=True)
    return path


def load_pi_config() -> dict:
    """读取客户端的 maa_pi_config.json（内含 adb 路径与地址）。"""
    if not os.path.isfile(PI_CONFIG):
        return {}
    try:
        with open(PI_CONFIG, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError) as exc:
        print(f"warning: 无法解析 {PI_CONFIG}: {exc}", file=sys.stderr)
        return {}


def resolve_adb(args) -> tuple:
    """返回 (adb 可执行文件, 设备序列号)。命令行 > 配置文件 > PATH。"""
    cfg = load_pi_config()
    adb_cfg = cfg.get("adb") or {}
    adb_path = getattr(args, "adb_path", None) or os.environ.get("MAA_ADB_PATH") or adb_cfg.get("adb_path")
    serial = (
        getattr(args, "serial", None)
        or os.environ.get("MAA_ADB_SERIAL")
        or adb_cfg.get("address")
    )
    if not adb_path:
        adb_path = shutil.which("adb")
    if not adb_path:
        die("找不到 adb：请在 config/maa_pi_config.json 里配置 adb_path，或用 --adb-path / MAA_ADB_PATH 指定")
    if not os.path.isfile(adb_path):
        found = shutil.which(adb_path)
        if not found:
            die(f"adb 不存在：{adb_path}")
        adb_path = found
    return adb_path, serial


def adb(adb_path: str, serial, *argv: str, check: bool = True) -> subprocess.CompletedProcess:
    cmd = [adb_path] + (["-s", serial] if serial else []) + list(argv)
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if check and proc.returncode != 0:
        die(f"adb 失败 ({proc.returncode}): {' '.join(cmd)}\n{proc.stderr.strip()}")
    return proc


def resolve_maactl() -> str:
    exe = os.environ.get("MAACTL_BINARY") or shutil.which("maactl") or shutil.which("maactl.exe")
    if not exe:
        die("找不到 maactl：请 npm install -g maactl，或用 MAACTL_BINARY 指定 maactl.exe")
    return exe


def parse_roi(text: str) -> list:
    parts = re.split(r"[,\s]+", text.strip())
    if len(parts) != 4:
        die(f"--roi 需要 4 个数字 x,y,w,h，收到：{text!r}")
    try:
        return [int(p) for p in parts]
    except ValueError:
        die(f"--roi 必须是整数：{text!r}")


def rel(path: str) -> str:
    try:
        return os.path.relpath(path, ROOT).replace("\\", "/")
    except ValueError:
        return path


# ------------------------------------------------------------------ capture --

def cmd_capture(args) -> int:
    adb_path, serial = resolve_adb(args)
    out = args.out
    if not out:
        out = os.path.join(SHOT_DIR, datetime.now().strftime("%Y%m%d-%H%M%S") + ".png")
    elif not os.path.isabs(out):
        out = os.path.join(SHOT_DIR, out)
    if not out.lower().endswith(".png"):
        out += ".png"
    ensure_dir(os.path.dirname(out))

    remote = "/sdcard/_maa_agent_capture.png"
    adb(adb_path, serial, "shell", "screencap", "-p", remote)
    proc = adb(adb_path, serial, "pull", remote, out, check=False)
    if proc.returncode != 0 or not os.path.isfile(out):
        die(f"截图失败：{proc.stderr.strip() or proc.stdout.strip()}")

    size = None
    try:
        from PIL import Image  # noqa: PLC0415

        with Image.open(out) as img:
            size = list(img.size)
    except Exception:  # Pillow 可选
        pass
    print(json.dumps({"path": out, "rel": rel(out), "size": size}, ensure_ascii=False))
    return 0


# --------------------------------------------------------------------- crop --

def cmd_crop(args) -> int:
    try:
        from PIL import Image
    except ImportError:
        die("crop 需要 Pillow：pip install Pillow")

    shot = args.shot
    if not os.path.isabs(shot):
        candidate = os.path.join(SHOT_DIR, shot)
        shot = candidate if os.path.isfile(candidate) else os.path.join(ROOT, args.shot)
    if not os.path.isfile(shot):
        die(f"截图不存在：{args.shot}")

    roi = parse_roi(args.roi)
    if args.out:
        out = args.out if os.path.isabs(args.out) else os.path.join(ROOT, args.out)
    else:
        group = args.group or die("缺少 --out，此时必须用 --group 指定 resource/base/image/ 下的分组名")
        name = args.name or die("缺少 --out，此时必须用 --name 指定模板文件名（不含扩展名）")
        out = os.path.join(IMAGE_ROOT, group, f"{name}.png")
    ensure_dir(os.path.dirname(out))

    with Image.open(shot) as img:
        full_w, full_h = img.size
        x, y, w, h = roi
        box = (max(0, x), max(0, y), min(full_w, x + w), min(full_h, y + h))
        if box[2] - box[0] <= 0 or box[3] - box[1] <= 0:
            die(f"ROI {roi} 落在 {full_w}x{full_h} 图像之外")
        img.crop(box).save(out)

    with Image.open(out) as tpl:
        print(json.dumps({
            "path": out, "rel": rel(out), "roi": roi,
            "screen": [full_w, full_h], "template": list(tpl.size),
        }, ensure_ascii=False))
    return 0


# -------------------------------------------------------------------- match --

def cmd_match(args) -> int:
    if not os.path.isfile(MATCH_PY):
        die(f"缺少 {rel(MATCH_PY)}")
    argv = [sys.executable, MATCH_PY, "--screen", args.screen, "--template", args.template]
    if args.roi:
        argv += ["--roi", args.roi]
    if args.threshold is not None:
        argv += ["--threshold", str(args.threshold)]
    if args.method:
        argv += ["--method", str(args.method)]
    if args.engine:
        argv += ["--engine", args.engine]
    if args.green_mask:
        argv += ["--green-mask"]
    if args.top is not None:
        argv += ["--top", str(args.top)]
    argv.append("--json")
    proc = subprocess.run(argv, text=True, encoding="utf-8", errors="replace")
    return proc.returncode


# -------------------------------------------------------------------- trace --

LOG_LINE = re.compile(
    r"^\[(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3})\]"
    r"\[(?P<lvl>[A-Z]+)\]\[Px\d+\]\[Tx\d+\]"
    r"\[(?P<comp>[^\]]+)\]\[L\d+\]\[(?P<func>[^\]]+)\]\s?(?P<msg>.*?)\s*$"
)
EVENT = re.compile(
    r"!!!OnEventNotify!!! \[handle=true\] \[msg=(?P<msg>[^\]]+)\] \[details=(?P<details>.*?)\]\s*$"
)


def _ts_seconds(ts: str) -> float:
    try:
        return datetime.strptime(ts, "%Y-%m-%d %H:%M:%S.%f").timestamp()
    except ValueError:
        return 0.0


def _safe_json(text: str, default):
    try:
        return json.loads(text)
    except (ValueError, TypeError):
        return default


def _balanced(text: str, start: int) -> str:
    """返回 text[start] 处 `[`/`{` 开始的那个配平片段（会跳过字符串里的括号）。"""
    depth, in_str, esc = 0, False, False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch in "[{":
            depth += 1
        elif ch in "]}":
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
    return text[start:]


def _field(text: str, key: str):
    """取 MaaFramework 日志里 `key=value` 的 value（支持嵌套 JSON 与标量），找不到返回 None。"""
    marker = key + "="
    idx = text.find(marker)
    if idx < 0:
        return None
    start = idx + len(marker)
    if start >= len(text):
        return ""
    if text[start] in "[{":
        return _balanced(text, start)
    end = text.find("] [", start)
    raw = text[start:end] if end >= 0 else text[start:]
    return raw.rstrip().rstrip("]").strip()


def read_events(log_path: str) -> list:
    """按顺序读出 MaaFramework 的 sink 事件（Tasker/Node 级），附时间戳。"""
    events = []
    with open(log_path, "r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            if "OnEventNotify" not in line:
                continue
            m = LOG_LINE.match(line.rstrip("\n"))
            if not m:
                continue
            ev = EVENT.search(m.group("msg"))
            if not ev:
                continue
            events.append({
                "ts": m.group("ts"),
                "t": _ts_seconds(m.group("ts")),
                "msg": ev.group("msg"),
                "details": _safe_json(ev.group("details"), {}),
            })
    return events


def read_scores(log_path: str) -> list:
    """从 Matcher/OCRer 的 analyze 行里取出每次识别的细节（按日志顺序）。"""
    out = []
    with open(log_path, "r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            if "::analyze]" not in line:
                continue
            m = LOG_LINE.match(line)
            if not m:
                continue
            comp, msg = m.group("comp"), m.group("msg")
            is_tpl, is_ocr = "TemplateMatcher" in comp, "OCRer" in comp
            if not (is_tpl or is_ocr):
                continue
            head, _, tail = msg.partition(" [")
            raw_all = _field(tail, "all_results_")
            raw_filtered = _field(tail, "filtered_results_")
            raw_best = _field(tail, "best_result_")
            raw_cost = _field(tail, "cost")
            entry = {
                "ts": m.group("ts"),
                "node": head.strip(),
                "algorithm": "TemplateMatch" if is_tpl else "OCR",
                "all": _safe_json(raw_all or "[]", []),
                "filtered": _safe_json(raw_filtered or "[]", []),
                "best": _safe_json(raw_best or "null", None),
                "cost_ms": int(raw_cost[:-2]) if raw_cost and raw_cost.endswith("ms") else None,
            }
            if is_tpl:
                entry["templates"] = _safe_json(_field(tail, "param_.template_") or "[]", [])
                entry["thresholds"] = _safe_json(_field(tail, "param_.thresholds") or "[]", [])
                entry["method"] = _safe_json(_field(tail, "param_.method") or "null", None)
            else:
                entry["expected"] = _safe_json(_field(tail, "param_.expected") or "[]", [])
                entry["only_rec"] = _safe_json(_field(tail, "param_.only_rec") or "null", None)
            out.append(entry)
    return out


def cmd_trace(args) -> int:
    log_path = args.log
    if not os.path.isabs(log_path) and not os.path.isfile(log_path):
        candidate = os.path.join(ROOT, log_path)
        if os.path.isfile(candidate):
            log_path = candidate
    if os.path.isdir(log_path):
        candidate = os.path.join(log_path, "maafw.log")
        if os.path.isfile(candidate):
            log_path = candidate
    if not os.path.isfile(log_path):
        die(f"日志不存在：{args.log}")

    events = read_events(log_path)
    if args.json:
        print(json.dumps(events, ensure_ascii=False, indent=2))
        return 0

    # 把 analyze 行的识别明细按节点索引，用来给事件补上「分数 < 阈值 / 模板 / OCR 文本」
    analyze_index = {}
    for entry in read_scores(log_path):
        analyze_index.setdefault(entry["node"], []).append(entry)

    def aux_for(name: str, ts: str):
        items = analyze_index.get(name)
        if not items:
            return None
        picked = None
        for item in items:                     # ts 形如 2026-10-08 15:29:13.411，可直接字典序比较
            if item["ts"] <= ts:
                picked = item
            else:
                break
        return picked or items[0]

    t0 = events[0]["t"] if events else 0.0
    records, summary = [], {}
    started = {}
    failures = []
    node_order = []
    fail_index = {}

    def add(t, tag, text, node=None, score=None, collapse=False):
        """识别失败按节点聚合（同一节点几十次失败在 agent 眼里就是一条信息）。"""
        if collapse:
            rec = fail_index.get(node)
            if rec is not None:
                rec["count"] += 1
                if score is not None:
                    rec["scores"].append(score)
                return
            rec = {"t": t, "tag": tag, "text": text, "count": 1,
                   "scores": [score] if score is not None else []}
            fail_index[node] = rec
            records.append(rec)
            return
        records.append({"t": t, "tag": tag, "text": text, "count": 1, "scores": []})

    for ev in events:
        msg, d, t = ev["msg"], ev["details"], ev["t"]
        if msg == "Tasker.Task.Starting":
            add(t, "Task.Starting", f"entry={d.get('entry')} task_id={d.get('task_id')}")
        elif msg == "Tasker.Task.Succeeded":
            add(t, "Task.Succeeded", f"entry={d.get('entry')}")
            summary["result"] = "Succeeded"
        elif msg == "Tasker.Task.Failed":
            add(t, "Task.Failed", f"entry={d.get('entry')}")
            summary["result"] = "Failed"
        elif msg == "Node.PipelineNode.Starting":
            name = d.get("name")
            started[name] = t
            node_order.append(name)
            add(t, "Node.Starting", str(name))
        elif msg in ("Node.PipelineNode.Succeeded", "Node.PipelineNode.Failed"):
            name = d.get("name")
            dur = (t - started[name]) * 1000 if name in started else None
            tag = "Node.Succeeded" if msg.endswith("Succeeded") else "Node.Failed"
            add(t, tag, f"{name}" + (f"  ({dur:.0f}ms)" if dur is not None else ""))
            if tag == "Node.Failed":
                failures.append(name)
        elif msg == "Node.Recognition.Starting":
            if args.full:
                add(t, "Reco.Starting", str(d.get("name")))
        elif msg in ("Node.Recognition.Succeeded", "Node.Recognition.Failed"):
            name = d.get("name")
            rd = d.get("reco_details") or {}
            algo = rd.get("algorithm") or "?"
            box = rd.get("box")
            allres = (rd.get("detail") or {}).get("all") or []
            score = max((float(r.get("score", 0)) for r in allres), default=None)
            aux = aux_for(name, ev["ts"])
            failed = msg.endswith("Failed")
            if failed:
                failures.append(name)
            if failed and not (args.full or args.show_failures):
                continue
            detail = f"{name}  {algo}"
            if score is not None:
                detail += f"  best={score:.4f}"
            if aux and aux.get("thresholds"):
                try:
                    detail += f"  thr={float(aux['thresholds'][0]):.3f}"
                    detail += "  MISS" if failed else "  OK"
                except (TypeError, ValueError, IndexError):
                    pass
            if algo == "OCR":
                if aux and aux.get("expected"):
                    detail += f"  expect={aux['expected']}"
                if allres:
                    preview = ",".join(str(r.get("text")) for r in allres[:4])
                    detail += f"  ocr=[{preview}]"
            elif aux and aux.get("templates"):
                detail += f"  tmpl={','.join(str(x) for x in aux['templates'])}"
            if box:
                detail += f"  box={box}"
            add(t, "Reco.Succeeded" if not failed else "Reco.Failed", detail,
                node=str(name), score=score, collapse=failed and not args.full)
        elif msg == "Node.Action.Starting" and args.full:
            add(t, "Action.Starting", str(d.get("name")))
        elif msg == "Node.Action.Succeeded":
            add(t, "Action.Succeeded", str(d.get("name")))
        elif msg == "Node.Action.Failed":
            add(t, "Action.Failed", str(d.get("name")))
        elif msg in ("Node.NextList.Starting", "Node.NextList.Succeeded", "Node.NextList.Failed") and args.full:
            lst = d.get("list") or []
            names = [(">" if x.get("jump_back") else "") + str(x.get("name")) for x in lst]
            add(t, msg.replace("Node.", ""), f"{d.get('name')} -> {names}")
    for rec in records:
        text = rec["text"]
        if rec["count"] > 1:
            scores = rec["scores"]
            span = f"best={min(scores):.4f}..{max(scores):.4f}" if scores else "best=?"
            text += f"  ×{rec['count']}  {span}"
        print(f"[{rec['t'] - t0:8.3f}s] {rec['tag']:<22} {text}")
    if not records:
        print("（该日志里没有 sink 事件：可能不是一次 run 的日志，或日志级别过低）")

    summary["nodes_entered"] = node_order
    summary["recognition_misses"] = sorted(set(failures))
    summary["note"] = "recognition_misses 是「至少失败过一次识别」的节点，轮询式流程里出现属正常，只看最终 result。"
    if events:
        summary["duration_s"] = round(events[-1]["t"] - t0, 3)
    print("\n--- summary ---")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


# ---------------------------------------------------------------------- run --

def cmd_run(args) -> int:
    maactl = resolve_maactl()
    run_id = args.id or (datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + re.sub(r"[^\w.-]+", "_", args.target))
    run_root = ensure_dir(os.path.join(RUN_DIR, run_id))

    target = args.target
    attempts = max(1, args.retries + 1)
    code = None
    log_dir = os.path.join(run_root, "maafw")
    for attempt in range(1, attempts + 1):
        # 每次尝试用独立日志目录：maddfw 的 sink 事件会追写同一个 maafw.log，
        # 混在一起会让 trace 出现两次 Task.Starting，容易误判。
        log_dir = os.path.join(run_root, "maafw" if attempt == 1 else f"maafw.{attempt}")
        ensure_dir(log_dir)
        argv = [maactl, "run", "-f", ROOT, "-log", log_dir]
        if args.node:
            argv += ["-n", target]
        else:
            argv += ["-t", target]
        for overlay in args.overlay or []:
            argv += ["-ol", overlay]
        if args.stop_after:
            argv += ["-sa", args.stop_after]
        if args.timeout:
            argv += ["-to", args.timeout]
        if args.events:
            argv += ["-e", args.events]
        if args.resource:
            argv += ["-r", args.resource]
        if args.serial:
            argv += ["-a", args.serial]
        if args.extra:
            argv += args.extra
        stdout_path = os.path.join(run_root, "stdout.log" if attempt == 1 else f"stdout.{attempt}.log")
        stderr_path = os.path.join(run_root, "stderr.log" if attempt == 1 else f"stderr.{attempt}.log")
        with open(stdout_path, "w", encoding="utf-8") as out, open(stderr_path, "w", encoding="utf-8") as err:
            proc = subprocess.run(argv, cwd=ROOT, stdout=out, stderr=err, text=True)
        code = proc.returncode
        tail = ""
        try:
            with open(stderr_path, "r", encoding="utf-8", errors="replace") as fh:
                tail = fh.read()
        except OSError:
            pass
        # 连接控制器偶发失败（MuMu/EmulatorExtras 首次握手），重试即可
        if code != 0 and "connect controller" in tail and attempt < attempts:
            print(f"run: 连接控制器失败，重试 {attempt + 1}/{attempts} …", file=sys.stderr)
            time.sleep(3)
            continue
        break

    print(json.dumps({
        "run_id": run_id,
        "run_dir": rel(run_root),
        "log": rel(os.path.join(log_dir, "maafw.log")),
        "stdout": rel(os.path.join(run_root, "stdout.log")),
        "stderr": rel(os.path.join(run_root, "stderr.log")),
        "exit_code": code,
        "attempts": attempt,
    }, ensure_ascii=False, indent=2))

    if args.no_trace:
        return code or 0

    log_file = os.path.join(log_dir, "maafw.log")
    if os.path.isfile(log_file):
        print("\n=== trace ===")
        cmd_trace(argparse.Namespace(log=log_file, json=False, full=args.full, show_failures=args.show_failures))
    else:
        print("warning: 没有生成 maafw.log，无法给出节点时间线", file=sys.stderr)

    print("\n=== stderr tail ===")
    with open(os.path.join(run_root, "stderr.log"), "r", encoding="utf-8", errors="replace") as fh:
        print(fh.read().strip()[-2000:])
    return code or 0


# ------------------------------------------------------------------- doctor --

def cmd_doctor(args) -> int:
    report = {"root": ROOT, "python": sys.version.split()[0], "checks": {}}
    try:
        from PIL import Image  # noqa: F401

        report["checks"]["Pillow"] = True
    except ImportError:
        report["checks"]["Pillow"] = False
    try:
        import numpy  # noqa: F401

        report["checks"]["numpy"] = True
    except ImportError:
        report["checks"]["numpy"] = False
    try:
        import cv2  # noqa: F401

        report["checks"]["cv2"] = getattr(cv2, "__version__", True)
    except ImportError:
        report["checks"]["cv2"] = False
    report["checks"]["maactl"] = shutil.which("maactl") or os.environ.get("MAACTL_BINARY")
    cfg = load_pi_config()
    report["checks"]["pi_config"] = rel(PI_CONFIG) if cfg else None
    try:
        adb_path, serial = resolve_adb(args)
        report["checks"]["adb"] = adb_path
        report["checks"]["serial"] = serial
        proc = adb(adb_path, serial, "shell", "wm", "size", check=False)
        report["checks"]["wm_size"] = proc.stdout.strip().splitlines()[-1] if proc.stdout.strip() else None
    except SystemExit as exc:
        report["checks"]["adb"] = f"unavailable ({exc.code})"
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


# --------------------------------------------------------------------- main --

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="maa.py",
        description="agent 用的 MaaFramework 编写工具链（见 tools/agent/README.md）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="示例：\n"
               "  python tools/agent/maa.py capture --out title.png\n"
               "  python tools/agent/maa.py crop --shot title.png --roi 245,745,215,45 "
               "--group 打开游戏 --name 打开游戏-点击开始游戏_1\n"
               "  python tools/agent/maa.py match --screen title.png "
               "--template resource/base/image/打开游戏/打开游戏-点击开始游戏_1.png\n"
               "  python tools/agent/maa.py run --task 打开游戏\n"
               "  python tools/agent/maa.py trace --log .agent/runs/<id>/maafw/maafw.log --full\n",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    def add_common(sp):
        sp.add_argument("--adb-path", help="adb 可执行文件（默认取 config/maa_pi_config.json）")
        sp.add_argument("--serial", help="设备序列号/地址（默认取 config/maa_pi_config.json）")

    sp = sub.add_parser("capture", help="截取设备当前画面")
    add_common(sp)
    sp.add_argument("--out", help="输出路径，默认 .agent/shots/<时间戳>.png")
    sp.set_defaults(func=cmd_capture)

    sp = sub.add_parser("crop", help="从截图切模板")
    sp.add_argument("--shot", required=True, help="截图路径（可只写文件名，去 .agent/shots/ 找）")
    sp.add_argument("--roi", required=True, help="x,y,w,h")
    sp.add_argument("--group", help="resource/base/image/ 下的分组名")
    sp.add_argument("--name", help="模板文件名（不含扩展名）")
    sp.add_argument("--out", help="直接指定输出路径（优先于 --group/--name）")
    sp.set_defaults(func=cmd_crop)

    sp = sub.add_parser("match", help="离线模板匹配")
    sp.add_argument("--screen", required=True)
    sp.add_argument("--template", required=True)
    sp.add_argument("--roi")
    sp.add_argument("--threshold", type=float)
    sp.add_argument("--method", type=int)
    sp.add_argument("--engine", choices=["auto", "cv2", "numpy"])
    sp.add_argument("--green-mask", action="store_true")
    sp.add_argument("--top", type=int)
    sp.set_defaults(func=cmd_match)

    sp = sub.add_parser("trace", help="解析 maafw.log 成节点时间线")
    sp.add_argument("--log", required=True, help="maafw.log 或包含它的目录")
    sp.add_argument("--json", action="store_true", help="输出原始事件 JSON")
    sp.add_argument("--full", action="store_true", help="连 NextList/Action/识别成功都打印")
    sp.add_argument("--no-failures", dest="show_failures", action="store_false", default=True,
                    help="不打印识别失败行")
    sp.set_defaults(func=cmd_trace)

    sp = sub.add_parser("run", help="用 maactl 跑 task/node，并自动 trace")
    sp.add_argument("--task", dest="target", help="PI task 名")
    sp.add_argument("--node", action="store_true", help="把 --task 的值当 Pipeline 节点名跑")
    sp.add_argument("--id", help="本次运行目录名")
    sp.add_argument("--overlay", action="append", help="额外资源根目录（-ol），可重复")
    sp.add_argument("--stop-after", help="只跑这么久（如 30s）")
    sp.add_argument("--timeout", help="超过该时长判失败")
    sp.add_argument("--events", default="focus", help="maactl -e：focus|all|off")
    sp.add_argument("--resource")
    sp.add_argument("--serial")
    sp.add_argument("--retries", type=int, default=2, help="连接控制器失败时的重试次数（默认 2）")
    sp.add_argument("--full", action="store_true", help="trace 更详细")
    sp.add_argument("--show-failures", action="store_true")
    sp.add_argument("--no-trace", action="store_true")
    sp.add_argument("extra", nargs="*", help="原样透传给 maactl 的额外参数")
    sp.set_defaults(func=cmd_run)

    sp = sub.add_parser("doctor", help="环境自检")
    add_common(sp)
    sp.set_defaults(func=cmd_doctor)

    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    if args.cmd == "run" and not args.target:
        die("run 需要一个目标：--task 任务名（加 --node 表示节点名）")
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
