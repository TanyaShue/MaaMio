# AGENTS.md — MaaMio 里 agent 的作业规程

MaaFramework（PI v2）项目。改 pipeline / 模板就按下面五步走，不要自己发明流程。
命令速查在 [`tools/agent/README.md`](tools/agent/README.md)，跨项目通用版在
[`skills/maafw-authoring/SKILL.md`](skills/maafw-authoring/SKILL.md)。

## 项目地图

| 路径                                      | 是什么                                            |
| ----------------------------------------- | ------------------------------------------------- |
| `interface.json`                          | PI 入口：控制器、资源、`import` 任务              |
| `tasks/*.json`                            | 任务注册，**只允许** `task` / `option` / `preset` |
| `resource/base/pipeline/*.json`           | 节点定义；**节点名在整个资源包里必须唯一**        |
| `resource/base/image/<组>/<节点名>_N.png` | 模板图，`template` 写相对 `image/` 的路径         |
| `config/maa_pi_config.json`               | 本机 adb/设备配置，已 gitignore                   |
| `.agent/`                                 | 截图与运行日志，已 gitignore                      |

坐标一律是**短边 720 的设计分辨率**（本游戏竖屏 `720x1280`）。

## 五步

1. **先用 adb 手动走一遍，把流程看懂。** 每一步截图看，别猜。
    - 坐标不要目测：把区域**放大 + 画网格**再读。预览会缩放，目测必错。
    - 列表的行位用**分隔线**量（扫暗像素占比 >0.5 的横线），不要按等距猜。
    - 中途遇到需要定的选择，**停下来问**。
2. **切模板**，只切真正要用的锚点：
   `python tools/agent/maa.py crop --shot x.png --roi x,y,w,h --group <组> --name <节点名>_1`
3. **写两个文件**：`tasks/<任务>.json` + `resource/base/pipeline/<任务>.json`。
   MaaFW **没有循环**，多行/多次要展开成状态机；每个"要等"的节点都要给足 `timeout`
   （`next` 只轮询到该节点 timeout 为止，默认 20s 撑不过冷启动）。
4. **跑**：`python tools/agent/maa.py run --task <任务>`（底层就是 `maactl run`，
   另外给你独立日志目录和自动时间线）。
5. **看时间线改**：每个识别都有 `best=分数 thr=阈值 OK|MISS`。哪里 MISS 改哪里。

## 纪律（都是真踩过的）

- **一次性资源必须一步一验。** 邮件奖励领完就没了、红点读过就消失。先把"能进入这一步"
  验证通过再写下一步；否则一次跑崩就永远失去了测试数据。
- **弹窗会吞掉点击。** 本游戏任何"领取"之后都弹「获得物品」+`触·摸·继·续`，盖住整屏，
  后面的点击全部无效。所以**领完必须先关弹窗再继续**——邮件任务卡住的根因就是这个。
- **阈值要查两面。** 只看"该命中"的分数不够：`返回` 按钮模板在列表页同一位置是 `删除` 按钮，
  误命中 **0.6972**——阈值 0.7 只差 0.003 就会点到删除。用 `maa.py match` 在
  "不该命中"的界面上也跑一次。
- **坐标用量的。** 我把邮件第 4 行写成 y=808，真实中心是 743，结果点在了列表底部空白处。
- **`maactl -log <dir>` 会清空该目录**，每次运行必须用独立日志目录（`maa.py run` 已处理）。
- **首次 `maactl run` 偶发 `Error: connect controller`**，重跑即可（`maa.py run` 自带重试）。
- **别让 `pnpm check:maa` 中途被打断**：它首次会下载 MaaFramework 运行时，且中断时会把
  `resource/base/image/**` 里受跟踪的图片删掉（`git checkout -- resource/base/image` 可恢复）。
  本地校验只用 `pnpm check:schema`。
- **`inverse: true` 遇上 `DirectHit` 永远不命中**（`mio.json` 里的 `mio-441848` 就是）。
- **`post_delay` 实际默认 1000ms**（schema 写 200，被 `default_pipeline.json` 覆盖）。

## 当前任务

| 任务         | 入口节点        | 状态                                                                               |
| ------------ | --------------- | ---------------------------------------------------------------------------------- |
| 领取邮件     | `邮件-开始`     | 逐封打开判断有无「收取」；全部已领路径实测通过（17.1s），领取+关弹窗路径已单独验证 |
| 打开游戏     | `打开游戏-开始` | 冷启动 → 登录 → 培育室（冷启动 40s，已在培育室时 3.5s）                            |
| 签到         | `签到-开始签到` | 既有                                                                               |
| 自动挂机卖蛋 | `mio-103994`    | `debug/on_error` 曾连续多日每 11 分钟留一张失败截图，值得按本规程复查              |

## 提交前

```powershell
pnpm format
pnpm check:schema
```
