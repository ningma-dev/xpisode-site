# 系统设置和体检脚本

怎么用，见[准备一台机器人电脑](../docs/guides/setup-robot-pc.md)。

`sudo host/setup.sh` 会做下面这些事。它需要这台电脑已经能访问 GitHub，因为要取 xpisode-i2rt。可以重复运行，每一项都会显示 `changed`、`unchanged` 或 `skipped`。

| 设置 | 为什么 |
| --- | --- |
| 允许手臂控制程序使用实时优先级，并放开锁内存的限制 | 否则控制会卡顿。重新登录后才生效 |
| 把用户加进 `dialout`、`plugdev`、`video` 组 | 普通用户才能打开相机和 CAN。重新登录后才生效 |
| 按 `can.toml` 给 CAN 适配器起名，并以 1 Mbit/s 启用 | 名字和插口无关。`can.toml` 里还有 `FILL` 时跳过这一项 |
| 相机和 CAN 适配器不进入 USB 省电 | 省电会让设备断开 |
| 允许普通用户打开 D405 | 相机库直接通过 USB 访问相机 |
| USB 串口转换器的延迟设为 1 ms | 有的设备通过它通信 |
| USB 缓冲区从 16 MB 加大到 1000 MB | 三台相机同时跑才够用 |
| 加 `--pin-irqs` 时，把相机和 CAN 的 USB 中断固定在 `machine.env` 写的 CPU 核上 | 重启或换内核后核号不会变。默认不做 |

它不会打开相机，不会往 CAN 上发数据，也不会启动站点。
