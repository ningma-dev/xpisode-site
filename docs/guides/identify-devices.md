# 认出每个设备

电脑能看到每个 CAN 适配器和每台相机的序列号，但不知道哪个对应哪条手臂、哪台相机在哪个位置。这篇把它们对上，写进 `robots/<站点>/`。

**什么时候做：** 第一次装机；换了适配器、线或相机之后。

**要求：** 人在电脑旁边，只拔插 USB 线；xpisode 已经装好（[准备一台机器人电脑](setup-robot-pc.md)第 6 步）。手臂不会动，相机不会开始拍摄。

命令都在 `~/xpisode-site` 里运行。下面以 YAM 为例，手臂和相机的名字见 `profiles/yam-bimanual-3cam.md`。

## 先记住

序列号认的是**适配器**，不是手臂。适配器从左臂换到右臂，系统发现不了。所以每个适配器和它的线都要**贴上手臂名**，一直绑在一起。也不要按插口顺序猜。

## 1. CAN 适配器对手臂

每条手臂做一遍：

1. 顺着这条手臂的线找到它的适配器，拔下 USB；
2. 运行 `host/can_list.sh`，少了的那个序列号就是这条手臂的；
3. 给适配器和线贴上手臂名，插回去。

把四个序列号填进 `robots/<站点>/can.toml`，替换掉 `"FILL"`。然后预览要写进系统的规则：

```bash
host/can_rules.py --check
```

**成功的样子：** 一段差异，其中四行以 `+` 开头，每行把一个序列号命名为 `can_<手臂名>`。如果提示有序列号没填或重复，回去核对。

生效：

```bash
sudo host/can_rules.py
```

**成功的样子：** 显示 `changed` 和 `udev reloaded and triggered`。

## 2. 相机对位置

相机有两个序列号，站点文件要用的是贴在相机标签上的那个，只有下面这个命令能看到。`lsusb` 和 `host/xhci_list.sh` 显示的是另一个，填进去相机会打不开。

每台相机做一遍：

1. 拔下这台相机的 USB；
2. 运行 `.venv/bin/python host/camera_serials.py`，少了的那个就是它，记下第一列；
3. 插回去。

在 `robots/<站点>/station.toml` 里，把 `FILL_cam_head_serial` 这类占位换成第一列的序列号。每台相机在文件里出现两次，两处都要换。

## 3. 记下 USB 控制器

后台任务要避开相机和 CAN 所用的 CPU 核，所以要记下它们插在哪个 USB 控制器上。

运行 `host/xhci_list.sh`，看每一行的 `xhci` 列和 `irq:cpu` 列。在 `robots/<站点>/machine.env` 里，每个用到的控制器写一行：

```bash
XPISODE_DEVICE_XHCI=(
  "0000:0d:00.0 4"   # <控制器> <冒号后面的 CPU>
)
```

## 4. 检查

```bash
host/doctor.sh
```

**成功的样子：** `can-rule`、`can-links`、`irq-layout`、`station-file`、`cameras` 都是 PASS。

如果 `can-links` 是 FAIL，把那个适配器拔下再插上，或者重启电脑。

## 5. 记下来

在 `robots/<站点>/LOG.md` 里记一条，写上每个对应关系，和改过的文件一起提交、推送。
