# tools/agent — 给 agent 用的 MaaFW 编写工具链

这套工具把「看屏幕 → 造模板 → 写 pipeline → 跑 → 读证据 → 改」这条闭环补齐，
让 agent（或人）可以**在动手之前**就知道一个模板到底靠不靠得住，而不用靠猜。

配套的操作规程在仓库根的 [`AGENTS.md`](../../AGENTS.md)，可复用的技能包在
[`skills/maafw-authoring`](../../skills/maafw-authoring/SKILL.md)。

## 依赖

- Python 3.9+，`Pillow` + `numpy`（`match.py` / `crop` / `verify` 需要）。
  本机可直接用 `D:\DeveEnvironment\Program\Anaconda3\python.exe`（另外带 `cv2`，可当对照 oracle）。
- `maactl` 在 PATH 上（npm 全局安装），或设 `MAACTL_BINARY`。
- `adb`：默认从 `config/maa_pi_config.json` 的 `adb.adb_path` / `adb.address` 读取，
  也可用 `--adb-path` / `--serial` 或 `MAA_ADB_PATH` / `MAA_ADB_SERIAL` 覆盖。

先自检：

```powershell
python tools/agent/maa.py doctor
```

## 子命令

| 命令      | 作用                                                                      |
| --------- | ------------------------------------------------------------------------- |
| `capture` | 设备截图存到 `.agent/shots/`（adb screencap + pull）                      |
| `crop`    | 从截图切模板，按 `resource/base/image/<功能组>/<节点名>_1.png` 约定落盘   |
| `match`   | 离线模板匹配（等价 `match.py`），不连设备就能算分数                       |
| `verify`  | **模板体检**：正样本帧最低分 vs 负样本帧最高分，判断阈值能不能分开        |
| `run`     | 用 maactl 跑 task/node，每次独立日志目录，跑完自动打 trace                |
| `trace`   | 把 `maafw.log` 解析成节点级时间线（识别分数、阈值、命中/未命中）          |
| `probe`   | 通过 `-ol` 覆盖层跑探测节点（全屏 OCR、单模板实测分数），不改 `resource/` |
| `doctor`  | 环境自检                                                                  |

典型用法：

```powershell
$py = "D:\DeveEnvironment\Program\Anaconda3\python.exe"

# 1. 看屏幕
& $py tools\agent\maa.py capture --out title.png

# 2. 切模板（落在 resource/base/image/打开游戏/ 下）
& $py tools\agent\maa.py crop --shot title.png --roi 330,747,128,38 `
      --group 打开游戏 --name 打开游戏-点击开始游戏_1

# 3. 体检——这一步不做完不要写进 pipeline
& $py tools\agent\maa.py verify --template resource/base/image/打开游戏/打开游戏-点击开始游戏_1.png `
      --pos .agent/sets/title_pos --neg .agent/sets/title_neg --threshold 0.7

# 4. 跑任务并自动给时间线
& $py tools\agent\maa.py run --task 打开游戏 --timeout 300s

# 5. 问真机要答案（OCR 能不能读出某段文字、某模板在真机上几分）
& $py tools\agent\maa.py probe --node _probe-ocr-title
```

## verify 怎么读

```
锚点                正min      负max      分离度   阈值0.7   正/负帧数
开始游戏 文字片段     0.7769    0.4204    +0.3565     通过    16/50
```

- **分离度 = 正样本最低分 − 负样本最高分**。这项运动里唯一有意义的指标。
  单帧 1.0000 毫无意义——半透明蒙版下的模板在其它帧可以掉到 0.5 以下。
- 经验门槛：分离度 > 0.15 才算能用；> 0.3 比较安心；≤ 0 直接换锚点。
- 阈值取在 `(负max, 正min)` 中间，不要贴着任一端。

## probe 怎么用

`tools/agent/probe/pipeline/probe.json` 里的节点通过 `maactl -ol` 覆盖层加载，
所以**永远不会进可发布的资源包**。改完 probe.json 直接跑即可：

```powershell
& $py tools\agent\maa.py probe --node _probe-match-title   # 真机上的实际分数
& $py tools\agent\maa.py probe --node _probe-ocr-full      # 全屏 OCR 出所有文字
```

离线 `match.py` 只是 MaaFramework（OpenCV）的近似；要判定「模板在真机上到底几分」，
以 probe 的日志为准。

## 测试

```powershell
# numpy 引擎 vs 真正的 cv2.matchTemplate（需要 cv2 的 Python）
python tools/agent/tests/test_match_parity.py

# 用真实录屏帧复核仓库里 4 个锚点在阈值 0.7 下是否仍然可分
python tools/agent/tests/verify_anchors.py
```

`test_match_parity.py` 是这套工具的地基：它保证离线分数与 MaaFramework 用的
`cv2.matchTemplate` 在良态窗口上一致（m5 ≤ 1.1e-4，m3/m1 ≤ 2e-6），并且退化分支
（常量模板 → 1.0，常量窗口 → 0.0）完全对齐。

## 中间产物

全部写在 `.agent/`（已 gitignore）：`shots/` 截图、`runs/` 每次运行的
`maafw/maafw.log` + stdout/stderr、`sets/` 标注帧集、`tmp/` 临时脚本。
