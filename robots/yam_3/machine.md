# ubuntu-System-Product-Name

host/snapshot.sh, 2026-11-13T13:05Z

## System

```
Ubuntu 24.04.5 LTS
7.0.0-43-generic
#43~24.04.1-Ubuntu SMP PREEMPT_DYNAMIC Sat Oct 31 06:52:47 UTC 2
```

## CPU

```
CPU(s):                                  16
Model name:                              AMD Ryzen 7 9800X3D 8-Core Processor
Thread(s) per core:                      2
Core(s) per socket:                      8
CPU(s) scaling MHz:                      53%

CPU CORE SOCKET ONLINE
  0    0      0    yes
  1    1      0    yes
  2    2      0    yes
  3    3      0    yes
  4    4      0    yes
  5    5      0    yes
  6    6      0    yes
  7    7      0    yes
  8    0      0    yes
  9    1      0    yes
 10    2      0    yes
 11    3      0    yes
 12    4      0    yes
 13    5      0    yes
 14    6      0    yes
 15    7      0    yes
```

## Memory

```
               total        used        free      shared  buff/cache   available
Mem:              59           2          54           0           3          57
Swap:              7           0           7
```

## GPU

```
NVIDIA GeForce RTX 5090, 595.99.02, 32607 MiB
```

## Disks

```
NAME         SIZE FSTYPE MOUNTPOINTS
sda          3.6T        
└─sda1       3.6T ntfs   
nvme0n1      3.7T        
├─nvme0n1p1  300M vfat   /boot/efi
├─nvme0n1p2   16M        
├─nvme0n1p3  500G ntfs   
└─nvme0n1p4  3.2T ext4   /

Filesystem     Type  Size  Used Avail Use% Mounted on
/dev/nvme0n1p4 ext4  3.2T   29G  3.0T   1% /
/dev/nvme0n1p1 vfat  296M   38M  259M  13% /boot/efi
```

## Cameras and CAN adapters

```
kind  serial               usb        xhci           irq:cpu
d405  262723071989         2-2        0000:0d:00.0   45:4
d405  262723072517         2-4        0000:0d:00.0   45:4
d405  262723077218         2-5        0000:0d:00.0   45:4
can   208F3783454650062    3-4.1      0000:0f:00.0   53:5
can   20833395594E50182    3-4.2      0000:0f:00.0   53:5
can   20673463584550182    3-5.2      0000:0f:00.0   53:5
can   20603463584550182    3-5.4      0000:0f:00.0   53:5
    45:          0          0          0          0      14692          0          0          0          0          0          0          0          0          0          0          0 IR-PCI-MSIX-0000:0d:00.0    0-edge      xhci_hcd
    53:          0          0          0          0          0    6248341          0          0          0          0          0          0          0          0          0          0 IR-PCI-MSIX-0000:0f:00.0    0-edge      xhci_hcd
    61:          0          0          0          0          0          0          0          0          0          0          0          0          0          0          0          0 IR-PCI-MSIX-0000:77:00.0    0-edge      xhci_hcd
    70:          0          0          0          0          0          0          0          0          0          0          0          0          0          0          0          0 IR-PCI-MSIX-0000:79:00.3    0-edge      xhci_hcd
    79:          0          0          0          0          0          0          0          0        945          0          0          0          0          0          0          0 IR-PCI-MSIX-0000:79:00.4    0-edge      xhci_hcd
    87:          0          0          0          0          0          0          0          0          0          0          0          0          0          0          0          0 IR-PCI-MSIX-0000:7a:00.0    0-edge      xhci_hcd
```

## CAN interfaces

```
serial               interface        usb        xhci           irq:cpu
208F3783454650062    can_follower_l   3-4.1      0000:0f:00.0   53:5
20833395594E50182    can_follower_r   3-4.2      0000:0f:00.0   53:5
20673463584550182    can_leader_r     3-5.2      0000:0f:00.0   53:5
20603463584550182    can_leader_l     3-5.4      0000:0f:00.0   53:5
```

## Interrupts

```
inactive
irq 45 affinity 0-15 effective 4
irq 53 affinity 0-15 effective 5
irq 61 affinity 0-15 effective 6
irq 70 affinity 0-15 effective 7
irq 79 affinity 0-15 effective 8
irq 87 affinity 0-15 effective 9
```

## Settings host/setup.sh touches

```
usbfs_memory_mb 1000
usbcore.autosuspend 2
user ubuntu: ulimit -r 90, ulimit -l unlimited, groups ubuntu adm dialout cdrom sudo dip video plugdev users lpadmin lxd
90-xpisode-can.rules
91-xpisode-usb-power.rules
92-xpisode-d405.rules
99-ftdi-latency.rules
cpufreq governor powersave
ModemManager active
cups active
unattended-upgrades active
```
