# 系统设置和体检脚本

怎么用，见 [准备一台机器人电脑](../docs/guides/setup-robot-pc.md)；为什么要这些设置，见 [本仓库是怎么组织的](../docs/how-it-works.md)。

`sudo host/setup.sh <站点>` 做这些事，可以重复运行，每一项都会显示"改了"或"没变"：

| 设置 | 为什么 |
| --- | --- |
| 允许手臂控制程序用实时优先级 | 否则控制会卡顿。实际用的是 xpisode-i2rt 仓库自带的设置脚本 |
| 把用户加进 `dialout`、`plugdev`、`video` 组 | 普通用户才能打开相机和 CAN |
| 按 `can.toml` 给 CAN 适配器起名，启用为 1 Mbit/s | 名字和插口无关（由 `can_rules.py` 生成） |
| 相机和 CAN 不进 USB 省电 | 省电会让设备断开 |
| 允许普通用户打开 D405 | 相机库通过 USB 直接访问相机 |
| USB 缓冲区从 16 MB 加大到 1000 MB | 三台相机同时跑才够用 |
| 可选，加 `--pin-irqs`：把相机和 CAN 的 USB 中断固定到 `machine.env` 写的核上 | 重启或换内核后不会变。默认不做 |

`setup.sh` 不会打开相机、不会往 CAN 上发数据，也不会启动站点。

以下几项没有自动做，可以按需要处理：关掉 ModemManager、打印服务、自动更新；把 CPU 调频从省电改成性能模式。
