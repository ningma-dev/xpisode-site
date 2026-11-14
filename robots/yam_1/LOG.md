# yam_1 日志

每在这台机器上做一件事，就在末尾加一条。格式见 [docs/README.md](../../docs/README.md#记日志)。

## 2026-11-13 · Claude · 只读调查
- ubuntu1 重装后：Ubuntu 24.04.5，内核 7.0.0-43，9800X3D 8 核 16 线程，59 GiB，RTX 5090（驱动 595.99），NVMe 剩 3.1 T。
- 三台 D405：262723071809、262723076941、262723078069，都在 USB 控制器 0f:00.0 上（中断在 CPU5）。位置还没定。
- USB 上没有 CAN 适配器。
- 没装 git、curl、编译工具、uv、Rust、ffmpeg、can-utils。

## 2026-11-14 · ningma（现场）+ Claude · 认出每个设备（相机）
- 相机：cam_head 262622273092，cam_left_wrist 262622274566，cam_right_wrist 262422273602（和旧站点 yam_abc 的三台相同，位置也相同）
- CAN：现场给的是 can0–can3 的对应关系，但当时 USB 上只有三个适配器，而且对应关系和旧站点左右相反、和相机不一致，等核对后再填
