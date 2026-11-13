# ubuntu3

Hand-written from the survey of 2026-11-13 (read-only, before host/setup.sh), in the sections of
host/snapshot.sh. Replace it with `host/snapshot.sh > robots/yam_3/machine.md` at setup
(docs/guides/setup-robot-pc.md step 11).

## System

```
Ubuntu 24.04.5 LTS
7.0.0-43-generic
PREEMPT_DYNAMIC (not an RT kernel)
```

## CPU

```
AMD Ryzen 7 9800X3D, 8 cores / 16 threads
SMT siblings: CPU N and N+8 (verify with lscpu -e)
```

## Memory

```
59 GiB
```

## GPU

```
NVIDIA GeForce RTX 5090, driver 595.99, CUDA 13.2; the desktop session runs on it
```

## Disks

```
NVMe root filesystem: 3.1 T free -> data root /home/ubuntu/xpisode-data
NTFS disks: present, not used
```

## Cameras and CAN adapters

```
kind  serial               usb        xhci           irq:cpu
d405  262723072517         2-4        0000:0d:00.0   45:4
d405  262723077218         2-5        0000:0d:00.0   45:4
d405  262723071989         10-2       0000:79:00.4   79:8
can   208F3783454650062    3-4.1      0000:0f:00.0   53:5
can   20833395594E50182    3-4.2      0000:0f:00.0   53:5
can   20673463584550182    3-5.2      0000:0f:00.0   53:5
can   20603463584550182    3-5.4      0000:0f:00.0   53:5
CAN adapters: candleLight gs_usb 1d50:606f, 12M. Which is which arm: not known yet.
Camera roles (head / left wrist / right wrist): not known yet.
```

## Interrupts

```
irqbalance inactive; every IRQ affinity 0-15; effective CPUs as above
the controllers on CPU4/CPU5 also carry the keyboard, Bluetooth, audio and LED devices
NVMe queue and NVIDIA interrupts also land on CPU4/5
```

## Settings host/setup.sh touches

```
usbfs_memory_mb 16
usbcore.autosuspend 2
user ubuntu: ulimit -r 0, no realtime group, not in dialout or video
no xpisode udev rules
cpufreq governor powersave
ModemManager active (tags can0 as a modem candidate); cups, unattended-upgrades present
missing tools: git curl build-essential/gcc uv rust ffmpeg librealsense ossutil can-utils
network: GitHub HTTPS 443 and SSH 22 reachable, no SSH key; OSS endpoint reachable
```
