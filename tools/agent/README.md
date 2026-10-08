# tools/agent — MaaFW 编写工具

给 agent（或人）写 MaaFramework pipeline 时用的最小工具集。**先把流程看懂再动手**，
规程见仓库根 [`AGENTS.md`](../../AGENTS.md)，跨项目通用版见
[`skills/maafw-authoring/SKILL.md`](../../skills/maafw-authoring/SKILL.md)。

只需要 Python 3.9+ 与 `Pillow` + `numpy`；`maactl` 在 PATH 上；adb 从
`config/maa_pi_config.json` 读取（可用 `--adb-path` / `--serial` 覆盖）。

```powershell
$py = "D:\DeveEnvironment\Program\Anaconda3\python.exe"   # 本机随便哪个带 Pillow+numpy 的都行
& $py tools\agent\maa.py doctor
```

## 六个命令

| 命令      | 用途                                                                    |
| --------- | ----------------------------------------------------------------------- |
| `capture` | 设备截图存到 `.agent/shots/`                                            |
| `crop`    | 从截图切模板，按 `resource/base/image/<组>/<节点名>_1.png` 落盘         |
| `match`   | **离线**算一个模板在某张截图上的分数（用来查阈值安不安全）              |
| `run`     | 底层就是 `maactl run`，另外给你独立日志目录、连接失败重试、跑完打时间线 |
| `trace`   | 把 `maafw.log` 解析成节点时间线（分数 / 阈值 / OK\|MISS）               |
| `doctor`  | 环境自检                                                                |

## 平时怎么用

```powershell
# 1) 看懂界面：截图，然后放大 + 画网格量坐标（不要目测，预览会缩放）
& $py tools\agent\maa.py capture --out title.png

# 2) 切模板
& $py tools\agent\maa.py crop --shot title.png --roi 258,364,200,46 `
      --group 邮件 --name 邮件-获得物品横幅_1

# 3) 查阈值两面——「该命中」的一面
& $py tools\agent\maa.py match --screen title.png `
      --template resource/base/image/邮件/邮件-获得物品横幅_1.png

# 4) 查阈值两面——「不该命中」的一面（这一步别省）
& $py tools\agent\maa.py match --screen list.png `
      --template resource/base/image/邮件/邮件-返回按钮_1.png --roi 515,865,160,90

# 5) 跑任务 + 看时间线
& $py tools\agent\maa.py run --task 领取邮件 --timeout 180s
```

## 时间线怎么读

```
[ 7.857s] Reco.Succeeded  邮件-第2封-返回  TemplateMatch  best=0.9945  thr=0.850  OK
[16.321s] Node.Failed     邮件-第4封  (3016ms)
```

- `best` 是这次识别的实际最高分，`thr` 是节点阈值，`OK`/`MISS` 是判定结果。
- 同一节点反复 `MISS` 最后 `OK` 属正常（轮询式流程），只看最终 `Task.Succeeded/Failed`。
- `maa.py run` 每次给你 `.agent/runs/<id>/`：`stdout.log`、`stderr.log`、`maafw/maafw.log`
  都在。**别拿同一个日志目录跑第二次——`maactl -log` 会把它清空。**

## match 的其他参数

`--roi x,y,w,h` 只在该区域搜索；`--threshold` 给判定；`--method` 默认 5
（TM_CCOEFF_NORMED，越高越像）；`--top N` 列出前 N 个候选位置。实现见 `match.py`
（Pillow + numpy，`TM_CCOEFF_NORMED` 与 OpenCV 对齐）。

## 中间产物

全在 `.agent/`（已 gitignore）：`shots/` 截图、`runs/` 每次运行的日志。
