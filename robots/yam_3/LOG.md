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
