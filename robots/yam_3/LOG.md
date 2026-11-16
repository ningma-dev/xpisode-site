# yam_3 日志

每在这台机器上做一件事，就在末尾加一条。格式见 [docs/README.md](../../docs/README.md#记日志)。

## 2026-11-13 · agent（经协调者）· 只读调查
- 机器 ubuntu3：Ubuntu 24.04.5，内核 7.0.0-43 PREEMPT_DYNAMIC，9800X3D 8C/16T，59 GiB，RTX 5090（驱动 595.99，CUDA 13.2），NVMe 3.1 T 空闲。详见 machine.md。
- 三台 D405 登记：262723072517、262723077218（xHCI 0d:00.0，CPU4），262723071989（79:00.4，CPU8）。位置未定。
- 先接了一个 CAN 适配器，后来四个都接上：208F3783454650062、20833395594E50182、20673463584550182、20603463584550182，都在 0f:00.0（CPU5）。对哪条臂未定。
- 用户 ubuntu：ulimit -r 0，不在 dialout/video；缺 git、uv、rust、ffmpeg、ossutil、can-utils 等。
- 没有改动这台机器。

## 2026-11-13 · ningma + Claude · 准备一台机器人电脑，第 1–5、7、8 步
- 第 1 步的 apt、第 5 步的 sudo host/setup.sh 和重启：ningma；其余步骤由 Claude 经 ssh 完成
- 版本：xpisode-site a9b6bed；xpisode 01d6d59，xpisode-realsense 223c00d，xpisode-i2rt c458074
- 结果：体检 2 FAIL：tools（还没装 ossutil，第 9 步）、autosuspend（相机仍是 auto，已改规则，待重跑 setup.sh 后验证）；其余 PASS 或 TODO
- 和文档不一样的地方：中断列表误报、第 7 步耗时和顺序、ssh 下找不到 uv，都已经在文档和脚本里改了

## 2026-11-13 · ningma（现场）+ Claude · 认出每个设备
- CAN：follower_l 208F3783454650062，follower_r 20833395594E50182，leader_l 20603463584550182，leader_r 20673463584550182
- 相机：cam_head 262723077218，cam_left_wrist 262723071989，cam_right_wrist 262723072517（右腕第二次确认）

## 2026-11-13 · ningma + Claude · 准备一台机器人电脑第 5–11 步、给手臂调参
- 版本：本仓库 d8d2547
- 调参：两条跟随臂都调了（20:50），调完后误差 7–11 mm（按速度分档，见 tuned/）
- 结果：体检 0 FAIL；machine.md 换成了 host/snapshot.sh 的输出
- 和文档不一样的地方：无

## 2026-11-13 · Claude · 修正相机序列号
- 站点启动时三台相机都报 `No device connected`：站点文件里填的是 USB 序列号，librealsense 要的是相机自己的序列号（标签上那个）
- 用 host/camera_serials.py 读出对应关系，改了 station.toml；体检加了 `cameras` 一项，以后这种错会直接查出来

## 2026-11-16 · Claude · 装上 ABC GELLO（左主臂）
- 版本：本仓库 1e8ae53，新增 xpisode-abc-gello 19f91e3（及 dynamixel-sdk 4.1.0、pyserial）；其余版本不变，没跑 build_pinned.sh
- GELLO 1L：U2D2 FTBPFNFF，舵机 ID 10–16，2 Mbaud；站点文件 `station_gello.toml`（`station.toml` 不变）
- 结果：体检 0 FAIL；`python -m xpisode_abc_gello.check`（舵机不上力矩）7 个舵机在线，总线 400 Hz，0 次重试，开机标定通过
- 还没做：起站点联调（手臂会动，需现场）

## 2026-11-16 · Claude · 更新到统一入口的版本（docs/guides/update.md 第 2 步）
- 版本：本仓库 fb77da7；依赖没变，没跑 uv sync 和 build_pinned.sh
- 变化：启动、面板、OSS、调参、存姿态、相机对照各只有一个 `scripts/*.sh`；`upload.sh` 换成 `scripts/oss.sh push --follow`；GELLO 版用 `scripts/run.sh --file station_gello.toml`
- 结果：体检 0 FAIL（两个站点文件都 PASS）；`scripts/oss.sh status --here` 正常
- 和文档不一样的地方：无

## 2026-11-16 · Claude · GELLO 改为标定后使用（docs/guides/update.md 第 2、4 步）
- 版本：本仓库 ef76fe3，xpisode-abc-gello 1cf33cc；其余版本不变，没跑 build_pinned.sh
- 原因：GELLO 版站点上"只有几个关节能用"。关节方向沿用了 abc 的默认值，没按这台实测；YAM 第 2、3 关节只能往一个方向转，方向一反，跟随臂就停在限位上
- 结果：体检 0 FAIL，`gello` 是 TODO（还没标定）；`scripts/gello.sh leader_l ids scan --file station_gello.toml` 7 个舵机都在（ID 10–16，2 Mbaud）
- 还没做：标定（docs/guides/gello.md 第 2 步，操作员做），之后再起 GELLO 版站点
