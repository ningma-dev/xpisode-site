# yam_2 日志

每在这台机器上做一件事，就在末尾加一条。格式见 [docs/README.md](../../docs/README.md#记日志)。

## 2026-11-13 · agent（经协调者）· 只读调查
- 机器 ubuntu2：硬件和系统同 ubuntu3（9800X3D、RTX 5090、Ubuntu 24.04.5、内核 7.0.0-43 PREEMPT_DYNAMIC），NVMe 3.1 T 空闲。详见 machine.md。
- 三台 D405 登记：260323074516（0d:00.0，CPU4），255323071678、262723072302（ASMedia 集线器后，0f:00.0，CPU5）。位置未定。
- 重新插线后四个 CAN 适配器：20843399594E50182（1-4，0d:00.0）、20973485584550182（3-4.1，0f:00.0）、206B3393594E50182（3-4.2，0f:00.0）、20743395594E50182（9-2，79:00.4，CPU8）。对哪条臂未定。
- 同一型号主板，相机插的口和 ubuntu3 不同，中断落的核也不同：每台都要量。
- 没有改动这台机器。

## 2026-11-13 · ningma + Claude · 准备一台机器人电脑，第 1–5、7、8 步
- 第 1 步的 apt、第 5 步的 sudo host/setup.sh 和重启：ningma；其余步骤由 Claude 经 ssh 完成
- 版本：xpisode-site a9b6bed；xpisode 01d6d59，xpisode-realsense 223c00d，xpisode-i2rt c458074
- 结果：体检 2 FAIL：tools（还没装 ossutil，第 9 步）、autosuspend（相机仍是 auto，已改规则，待重跑 setup.sh 后验证）；其余 PASS 或 TODO
- 和文档不一样的地方：中断列表误报、第 7 步耗时和顺序、ssh 下找不到 uv，都已经在文档和脚本里改了

## 2026-11-13 · ningma（现场）+ Claude · 认出每个设备
- CAN：follower_l 206B3393594E50182，follower_r 20973485584550182，leader_l 20743395594E50182，leader_r 20843399594E50182
- 相机：cam_head 255323071678，cam_left_wrist 262723072302，cam_right_wrist 260323074516

## 2026-11-13 · Claude · 修正相机序列号
- 站点启动时三台相机都报 `No device connected`：站点文件里填的是 USB 序列号，librealsense 要的是相机自己的序列号（标签上那个）
- 用 host/camera_serials.py 读出对应关系，改了 station.toml；体检加了 `cameras` 一项，以后这种错会直接查出来

## 2026-11-17 · Claude（ningma 同意）· 更新版本，打开同步（docs/guides/update.md，acceptance.md「打开同步」）
- 站点已由操作员停下（最后一条录制 11:59）；停了面板（12:07），更新后用 `scripts/serve.sh` 重新开了面板，站点没有重启
- 版本：本仓库 5b80309 → 25f7a99；`uv sync --locked`、`scripts/build_pinned.sh` 通过，体检 0 FAIL
- `host/setup.sh` 这次只改了参数写法，系统设置没变，没有重跑
- 同步服务 `xpisode-sync` 已装上并在运行：拉下 428 个小记录，开始上传 1036 个文件、287 GB
- 还没做：`sudo loginctl enable-linger ubuntu`（要 sudo）。在那之前，桌面登录着服务才运行
- 本机有两个没提交的调参文件 `tuned/can_follower_l.json`、`tuned/can_follower_r.json`
- 和文档不一样的地方：无

## 2026-11-17 · Claude（ningma 要求）· 关掉后台面板
- 更新后用 `setsid nohup scripts/serve.sh` 开的面板（127.0.0.1:8043）不挂在任何终端上，按 PID 发 SIGINT 关掉了；8043 已空出来
- 同步服务 `xpisode-sync` 没动，还在上传
