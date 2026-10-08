# AGENTS.md — MaaMio 里 agent 的作业规程

本仓库是 MaaFramework（PI v2）项目。**在这里改 pipeline / 模板，一律按下面的闭环走**，
不要凭截图直觉得出阈值。完整命令说明见 [`tools/agent/README.md`](tools/agent/README.md)，
可复用技能包见 [`skills/maafw-authoring/SKILL.md`](skills/maafw-authoring/SKILL.md)。

## 项目地图

| 路径                                          | 是什么                                                                      |
| --------------------------------------------- | --------------------------------------------------------------------------- |
| `interface.json`                              | PI 入口：控制器、资源、`import` 任务文件                                    |
| `tasks/*.json`                                | 任务注册（**只允许** `task` / `option` / `preset` 三个键）                  |
| `resource/base/pipeline/*.json`               | 节点定义，文件名随意，**节点名在整个资源包里必须唯一**                      |
| `resource/base/image/<功能组>/<节点名>_N.png` | 模板图，`template` 字段相对 `image/` 写                                     |
| `resource/base/default_pipeline.json`         | 全局默认值（`timeout` 20000、`post_delay` 1000 等），**不参与 schema 校验** |
| `config/maa_pi_config.json`                   | 本机 client 配置（adb 路径、设备地址、默认资源/任务），已 gitignore         |
| `tools/agent/`                                | agent 工具链                                                                |
| `.agent/`                                     | 中间产物（截图、运行日志、标注帧集），已 gitignore                          |

坐标一律是**短边 720 的设计分辨率**（本游戏竖屏 `720x1280`），与
`interface.json` 的 `display_short_side: 720` 对应。模板图必须是 720p 无损原图的裁剪。

## 闭环（九步，别跳）

```powershell
# 任何 Python 3.9+ 且装了 Pillow + numpy 即可（只有跑 parity 对照测试才额外需要 cv2）。
# 本机这台 Anaconda 两样都有，所以下文都用它。
$py = "D:\DeveEnvironment\Program\Anaconda3\python.exe"
```

1. **自检**：`& $py tools\agent\maa.py doctor`（确认 maactl / adb / 设备 / Python 库）。
2. **拿到真实画面**：`maa.py capture`。要研究界面变化就**连续截一组帧**
   （冷启动、转场各截一次），再做拼图看时间线。
3. **选锚点**：优先挑**静态、不透明、该界面独有**的元素。避开半透明蒙版、
   动画、以及跨界面复用的元素（本项目的「每日补给」宝箱在主界面和培育室**都出现**，
   拿它当锚点必然误判）。
4. **切模板**：`maa.py crop --shot ... --roi x,y,w,h --group <组> --name <节点名>_1`。
5. **体检（最关键的一步）**：`maa.py verify --pos <正样本帧> --neg <负样本帧> --threshold 0.7`。
   **分离度 > 0.15 才允许写进 pipeline**；不合格就换裁剪范围或换锚点，不要靠调阈值硬凑。
6. **写节点**：锚点识别 + `action`，`next` 串成状态机。凡是「要等」的节点
   （等启动、等加载、等转场）**必须显式给足 `timeout`**——`next` 列表只会轮询到该节点的
   `timeout` 为止，默认 20000ms 撑不过冷启动。
7. **校验**：`pnpm check:schema`（快、只读、安全）。
   **不要随手跑 `pnpm check:maa`**：它首次会下载 MaaFramework 运行时，而且中途被中断会把
   `resource/base/image/**` 里的受跟踪图片删掉（`git checkout -- resource/base/image` 可恢复）。
8. **跑**：`& $py tools\agent\maa.py run --task <任务名> --timeout 300s`。
   它会给你独立的运行目录 + 自动 trace；连接控制器偶发失败会自动重试。
9. **读证据再改**：看 trace 的节点序列与「分数 / 阈值 / OK|MISS」。要看某个模板在真机上
   到底几分、或某段文字 OCR 能不能读出来，用 `maa.py probe`（走 `-ol` 覆盖层，不动 `resource/`）。

## 平台的坑（都实测过，别踩第二遍）

- **半透明蒙版会让模板分数剧烈漂移**。牧羊人之心标题界面的「点击开始游戏」盖在随背景插画
  变化的压暗条上，整串文字在 16 帧里从 0.4031 飘到 1.0000；只取不透明的「开始游戏」四字后
  最低 0.777、非标题帧最高 0.421。**判据永远是分离度，不是某一张截图的分数。**
- **模板匹配对「常量窗口」返回 0，对「常量模板」返回 1**（OpenCV 自己的约定，已实测）。
  后者意味着纯色模板会在**任何**画面上满分命中。离线 `match.py` 已对齐这一点，
  并由 `tests/test_match_parity.py` 用 cv2 当 oracle 守住。
- **首帧分辨率可能是横屏**：MuMu 的桌面是 `1280x720`，游戏是 `720x1280`。
  冷启动一开始的 `DirectHit` 会报 `box=[0,0,1280,720]`，属正常。
- **`maactl -log <dir>` 会清空该目录**。每次运行都要用独立日志目录，否则证据被覆盖——
  `maa.py run` 已经这么做（`.agent/runs/<id>/maafw/`）。
- **首次 `maactl run` 偶发 `Error: connect controller "Android"`**，重跑即好。`maa.py run` 自带重试。
- **Windows 中文环境必须强制 UTF-8 输出**，否则 Python 的 cp936 会把「签到」编成 `0xC7A9`，
  而对方按 UTF-8 解码正好得到一个合法字符 `ǩ`——**静默错字，不是报错**。
- **`cv2.imread` 打不开非 ASCII 路径**（本仓库模板路径全是中文）。必须
  `cv2.imdecode(np.fromfile(p, np.uint8), ...)`；`tools/agent/match.py` 用 Pillow 读取，本身没问题。
- **`inverse: true` 遇上 `DirectHit` 永远不命中**（DirectHit 恒真，取反恒假）。
  `resource/base/pipeline/mio.json` 里的 `mio-441848` 就是这种写法。
- **`tasks/*.json` 只允许 `task`/`option`/`preset`**；写 `group`/`pretask`/`setting` 会过不了校验。
- **`post_delay` 默认是 1000ms**（schema 写 200，本项目 `default_pipeline.json` 覆盖成 1000），
  串联多步时会明显变慢，属预期。

## 当前任务清单

| 任务         | 入口节点        | 状态                                                                                                                                                   |
| ------------ | --------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------ |
| 打开游戏     | `打开游戏-开始` | 冷启动 → 登录 → 培育室，已端到端验证（冷启动 35s，已在培育室时 3.5s 直接返回）                                                                         |
| 签到         | `签到-开始签到` | 既有任务                                                                                                                                               |
| 自动挂机卖蛋 | `mio-103994`    | `debug/on_error` 里曾连续多日每 11 分钟留一张 `mio-103994` 失败截图，说明该节点在超时重试，值得按本规程复查（`timeout: 620000` / `rate_limit: 60000`） |

## 提交前

```powershell
pnpm format          # prettier + maafw 排序插件会重排 pipeline 键顺序
pnpm check:schema    # 只跑这个，安全
& $py tools\agent\tests\verify_anchors.py   # 锚点在新帧上是否还分得开
```
