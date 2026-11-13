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
