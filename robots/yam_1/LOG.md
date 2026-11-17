# yam_1 日志

每在这台机器上做一件事，就在末尾加一条。格式见 [docs/README.md](../../docs/README.md#记日志)。

## 2026-11-13 · Claude · 只读调查
- ubuntu1 重装后：Ubuntu 24.04.5，内核 7.0.0-43，9800X3D 8 核 16 线程，59 GiB，RTX 5090（驱动 595.99），NVMe 剩 3.1 T。
- 三台 D405：262723071809、262723076941、262723078069，都在 USB 控制器 0f:00.0 上（中断在 CPU5）。位置还没定。
- USB 上没有 CAN 适配器。
- 没装 git、curl、编译工具、uv、Rust、ffmpeg、can-utils。

## 2026-11-14 · ningma（现场）+ Claude · 认出每个设备（相机）
- 相机：cam_head 262622273092，cam_left_wrist 262622274566，cam_right_wrist 262422273602（和旧站点 yam_abc 的三台相同，位置也相同）
- CAN：leader_l 20743393594E50182，leader_r 207D346D584550172，follower_l 207D3463584550182（左右和旧站点 yam_abc 相反，现场确认是对调了）。follower_r 的适配器不在 USB 上，没填
- 补上 follower_r 20823393594E50182（插上后是 can3，USB 3-4.1）

## 2026-11-14 · ningma + Claude · 任务和计划
- 任务：stack_bowls 新版本（三只大小不同的碗，2 分钟），fold_shirt 新建（T 恤、长袖或卫衣，平铺开始，3 分钟），sort_legos 用原有版本
- 计划，各 50 行，站点 yam_1，判断人 jianping_yin：stack_bowls_yam_1_1114、sort_legos_yam_1_1114、fold_shirt_yam_1_1114（判断时问"衣服"：T恤 / 长袖 / 卫衣）
- 写在 ubuntu1 本机，配好 OSS 钥匙、开着上传以后会推到 OSS

## 2026-11-14 · ningma + Claude · 换成 RoboDojo 的九个任务
- 删掉了上午建的三个计划，以及 stack_bowls、fold_shirt 的新版本、sort_legos 的本机副本（都没上过 OSS）
- 新建任务（来自 RoboDojo 真机任务，instruction 照原文）：cap_pen、hang_mugs、sweep_blocks、fill_pen_holder、stack_and_cover_blocks、stack_bowls、stand_up_bottles、pack_and_pour_fruit、cover_blocks。stack_bowls、hang_mugs 接在 OSS 上旧版本之后
- 写在 ubuntu1 本机，还没有计划
- 九个任务的 description 加上了 RoboDojo 的部分得分档；建计划时用判断时的"得分"选项记下来（替换了上一版，上一版没上过 OSS）

## 2026-11-17 · Claude（ningma 同意）· 更新版本，打开同步（docs/guides/update.md，acceptance.md「打开同步」）
- 站点开着但没在录（最后一条录制 12:00）；12:14 停了站点和面板（同 Ctrl+C），更新后用 `scripts/serve.sh` 重新开了面板，站点没有重启
- 版本：本仓库 eccc09c → 25f7a99；`uv sync --locked`、`scripts/build_pinned.sh` 通过，体检 0 FAIL
- `host/setup.sh` 这次只改了参数写法，系统设置没变，没有重跑
- 同步服务 `xpisode-sync` 已装上并在运行：拉下 268 个小记录，待上传 1296 个文件、256 GB
- 还没做：`sudo loginctl enable-linger ubuntu`（要 sudo）。在那之前，桌面登录着服务才运行
- 本机有两个没提交的调参文件 `tuned/can_follower_l.json`、`tuned/can_follower_r.json`，和上次手动上传留下的 `ossutil_output/`
- 和文档不一样的地方：无

## 2026-11-17 · Claude（ningma 要求）· 关掉后台面板
- 12:14 用 `setsid nohup scripts/serve.sh` 开的面板（127.0.0.1:8043）不挂在任何终端上，按 PID 发 SIGINT 关掉了；8043 已空出来
- 同步服务 `xpisode-sync` 没动，还在上传
