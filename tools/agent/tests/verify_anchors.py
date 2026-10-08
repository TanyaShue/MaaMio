#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 .agent/artifacts 的原始帧按「人工标注」分成正/负样本集（硬链接，不占空间），
然后用 maa.py verify 复核仓库里真正要用的那 4 张模板。

标注依据（逐帧看过）：
  标题文字可见 : coldstart 017-029 + afterclick 000-002（点击后淡出，文字仍在）
  登录面板可见 : afterclick 004-035
  主界面可见   : afterlogin 002-023
  培育室可见   : afterroom 000-009
"""
import os
import subprocess
import sys

sys.stdout.reconfigure(encoding="utf-8")

# 仓库根从脚本位置推导，不要写死路径：tools/agent/tests -> tools/agent -> tools -> 仓库根
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
A = os.path.join(ROOT, ".agent", "artifacts")
S = os.path.join(ROOT, ".agent", "sets")
PY = sys.executable


def link_set(dst, rels):
    os.makedirs(dst, exist_ok=True)
    for name in os.listdir(dst):
        os.remove(os.path.join(dst, name))
    out = []
    for rel in rels:
        src = os.path.join(A, rel.replace("/", os.sep))
        if not os.path.isfile(src):
            continue
        dstf = os.path.join(dst, rel.replace("/", "_"))
        if os.path.exists(dstf):
            os.remove(dstf)
        os.link(src, dstf)
        out.append(dstf)
    return out


def names(prefix, rng):
    return [f"{prefix}/{i:03d}.png" for i in rng]


all_cs, all_ac, all_al, all_ar = (names("coldstart", range(30)), names("afterclick", range(36)),
                                 names("afterlogin", range(24)), names("afterroom", range(10)))

SETS = {
    "title": (names("coldstart", range(17, 30)) + names("afterclick", range(0, 3)),
              names("coldstart", range(0, 17)) + names("afterclick", range(3, 36))),
    "login": (names("afterclick", range(4, 36)),
              names("afterclick", range(0, 4)) + all_cs),
    "hub": (names("afterlogin", range(2, 24)),
            names("afterlogin", range(0, 2)) + all_cs + all_ac + all_ar),
    "room": (names("afterroom", range(10)),
             all_cs + all_ac + all_al),
}

TEMPLATES = {
    # 注意：这里的路径是相对仓库根的文件路径；pipeline JSON 里的 template 则是相对 image/ 的。
    "title": ("resource/base/image/打开游戏/打开游戏-点击开始游戏_1.png", "开始游戏 文字片段"),
    "login": ("resource/base/image/打开游戏/打开游戏-点击登录_1.png", "登录按钮"),
    "hub": ("resource/base/image/打开游戏/打开游戏-进入培育室_1.png", "主界面-培育室按钮"),
    "room": ("resource/base/image/打开游戏/打开游戏-确认完成_1.png", "培育室-底部工具栏"),
}

T = os.path.join(ROOT, "resource", "base", "image")
failed = []
print(f"{'锚点':<22}{'正min':>9}{'负max':>9}{'分离度':>9}{'阈值0.7':>10}   正/负帧数")
print("-" * 78)
for key, (tpl_rel, label) in TEMPLATES.items():
    pos_rels, neg_rels = SETS[key]
    pos = link_set(os.path.join(S, f"{key}_pos"), pos_rels)
    neg = link_set(os.path.join(S, f"{key}_neg"), neg_rels)
    cmd = [PY, os.path.join(ROOT, "tools", "agent", "maa.py"), "verify",
           "--template", tpl_rel,
           "--pos", os.path.join(S, f"{key}_pos"),
           "--neg", os.path.join(S, f"{key}_neg"),
           "--threshold", "0.7", "--json"]
    p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", cwd=ROOT)
    if p.returncode not in (0, 1) or not p.stdout.strip():
        print(f"{label:<22} 失败: {p.stderr.strip()[:120]}")
        failed.append(key)
        continue
    import json
    r = json.loads(p.stdout)
    ok = r["all_pos_above"] and r["all_neg_below"]
    if not ok:
        failed.append(key)
    print(f"{label:<22}{r['pos_min']:>9.4f}{r['neg_max']:>9.4f}{r['separation']:>+9.4f}"
          f"{('通过' if ok else '不通过'):>10}   {r['positives']}/{r['negatives']}"
          f"   最差正样本 {os.path.basename(r['pos_min_frame'])}")

print()
if failed:
    print(f"FAIL: {failed}")
    sys.exit(1)
print("OK: 4 个锚点在阈值 0.7 下正样本全过、负样本全不过")
