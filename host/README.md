# 站点机的系统设置

每台站点机装一次；它的 `robots/<站点>/` 改了以后再跑一次。由用户自己用 root 跑：

```bash
sudo host/setup.sh                 # 按主机名找站点；也可以写明：sudo host/setup.sh yam_3
sudo host/setup.sh --pin-irqs      # 可选，默认不做：把相机和 CAN 控制器的中断钉到固定的核
```

`setup.sh` 可以重复跑：先比较再写，逐项打印 `changed` / `unchanged`。它**不打开相机、不往 CAN 上发东西、不启动站点**。CAN 规则写完立即重载并触发 udev（适配器改名、以 1 Mbit/s 拉起）；其余 udev 规则在设备重新插上或重启后生效。

| 装什么 | 装到 | 来源 |
| --- | --- | --- |
| 实时优先级和锁内存（rtprio 90、memlock 不限） | `/etc/security/limits.d/`、`user@.service.d/`、`user.conf.d/` | 直接调用 xpisode-i2rt 自己的 `scripts/setup_host.sh`（`setup.sh` 先以用户身份取钉住的版本到 `local/build/`），这里不再抄一份；控制器靠这个限制拿实时优先级，不用 `setcap` |
| 用户加入 `dialout`、`plugdev`、`video` | — | `setup.sh` |
| CAN 按适配器序列号起名（`can_follower_l` 等，每台 YAM 机器一样），1 Mbit/s 拉起，ModemManager 不碰 | `/etc/udev/rules.d/90-xpisode-can.rules` | `can_rules.py` 由 `robots/<站点>/can.toml`（`<角色> = "<序列号>"`）生成，重载并触发 udev，打印改了什么；有 FILL 或重复的序列号就拒绝。序列号是数据，不改共用规则 |
| D405 和 CAN 适配器不进 USB 自动挂起 | `/etc/udev/rules.d/91-xpisode-usb-power.rules` | 本目录 |
| 用户能打开 D405（pyrealsense2 走 libusb） | `/etc/udev/rules.d/92-xpisode-d405.rules` | librealsense `99-realsense-libusb.rules` 里 D405 那一行 |
| FTDI 串口延迟 1 ms | `/etc/udev/rules.d/99-ftdi-latency.rules` | 本目录（旧站点机） |
| usbfs 内存 16 → 1000 MB | `xpisode-usbfs.service` | 本目录 |
| （可选）中断钉核 | `/usr/local/sbin/xpisode-pin-irqs`、`xpisode-pin-irqs.service` | 先量此刻相机和 CAN 挂在哪些控制器上（`xhci_list.sh`），再按 PCI 地址钉，不按中断号；目标核取自 `machine.env` 的 `XPISODE_DEVICE_XHCI`，用到的控制器没写进去就拒绝 |

只读的小工具（什么都不改，不打开设备）：

- `host/can_list.sh`：每个 CAN 适配器的序列号、现在的网口名、USB 路径、控制器和核；
- `host/xhci_list.sh`：每台 D405 和 CAN 适配器挂在哪个控制器上，中断在哪个核；
- `host/can_rules.py <站点> --check`：规则会怎么改，不写；
- `host/doctor.sh`：逐项 PASS / FAIL，装机每一步做完都用它检查；
- `host/snapshot.sh`：机器事实，存为 `robots/<站点>/machine.md`，重装后拿来对照。

之后重启，在跑站点的终端里：`host/doctor.sh`（还有 xpisode-i2rt 自己的 `.venv/bin/python -m xpisode_i2rt.doctor`，它会真的试一次实时优先级）。完整步骤见 `docs/runbooks/add-machine.md`。

**后台核**：`scripts/station_env.sh` 用 `machine.env` 里的 `XPISODE_DEVICE_XHCI` 算出来：除去这些核和它们的超线程兄弟（从 sysfs 读），其余都给后台（代理视频、上传）。钉了中断，算出来的就一直对；不钉，重新布线或换内核后要重新量（`host/xhci_list.sh`；`host/doctor.sh` 的 `irq-layout` 会报出不一致）。

**不在这里自动做的**（建议，由用户决定）：
- 停掉或屏蔽 ModemManager（它会把 CAN 当成调制解调器去探测；规则里已经加了 `ID_MM_DEVICE_IGNORE`）、cups、unattended-upgrades；
- cpufreq 调速器从 `powersave` 改成 `performance`；
- NVMe 队列中断和 NVIDIA 中断也落在 CPU 4/5 上；桌面会话跑在 5090 上。

OSS 凭据（`~/.ossutilconfig`）不在这里，见 `docs/runbooks/add-machine.md` 第 11 步。
