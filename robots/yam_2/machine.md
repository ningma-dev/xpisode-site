# ubuntu2

Hand-written from the survey of 2026-11-13 (read-only, after re-cabling), in the sections of
host/snapshot.sh. Replace it with `host/snapshot.sh > robots/yam_2/machine.md` at setup
(docs/runbooks/add-machine.md step 13).

## System

```
Ubuntu 24.04.5 LTS
7.0.0-43-generic
PREEMPT_DYNAMIC (not an RT kernel)
```

## CPU

```
AMD Ryzen 7 9800X3D, 8 cores / 16 threads (same motherboard as ubuntu3)
SMT siblings: CPU N and N+8
```

## GPU

```
NVIDIA GeForce RTX 5090; the desktop session runs on it
```

## Disks

```
NVMe: 3.1 T free -> data root /home/ubuntu/xpisode-data
```

## Cameras and CAN adapters

```
kind  serial               usb        xhci           irq:cpu
d405  260323074516         2-5        0000:0d:00.0   45:4
d405  255323071678         4-5.3      0000:0f:00.0   53:5   (behind an ASMedia hub)
d405  262723072302         4-5.4      0000:0f:00.0   53:5   (behind an ASMedia hub)
can   20843399594E50182    1-4        0000:0d:00.0   45:4
can   20973485584550182    3-4.1      0000:0f:00.0   53:5
can   206B3393594E50182    3-4.2      0000:0f:00.0   53:5
can   20743395594E50182    9-2        0000:79:00.4   79:8
CAN adapters: candleLight gs_usb 1d50:606f. Which is which arm: not known yet.
Camera roles (head / left wrist / right wrist): not known yet.
Controllers empty at the first survey: 77:00.0, 79:00.3, 79:00.4, 7a:00.0 (a CAN adapter is on
79:00.4 since re-cabling).
```

## Interrupts

```
the controllers on CPU4/CPU5 also carry the keyboard, Bluetooth, audio and LED devices
NVMe queue and NVIDIA interrupts also land on CPU4/5
```

## Settings host/setup.sh touches

```
usbfs_memory_mb 16
usbcore.autosuspend 2
```
