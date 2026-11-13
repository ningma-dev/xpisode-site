# 认出每个设备

电脑能看到每个 CAN 适配器和每台相机，但不知道哪个对应哪条手臂、哪台相机在哪个位置。这篇把它们对上，写进 `robots/<站点>/`。

**什么时候做：** 第一次装机；换了适配器、线或相机之后。

**要求：** xpisode 已经装好（[准备一台机器人电脑](setup-robot-pc.md)第 6 步），人在电脑旁边。只拔插 USB 线，手臂不会动。

命令都在 `~/xpisode-site` 里运行。下面以 YAM 为例，手臂和相机的名字见 `profiles/yam-bimanual-3cam.md`。

## 怎么认

```bash
host/devices.sh
```

它列出现在插着的每个 CAN 适配器和每台相机，以及要填进文件的序列号。**拔下一个设备，再运行一次，少了的那一行就是它。** 认完插回去。

序列号认的是适配器，不是手臂：适配器从左臂换到右臂，系统发现不了。所以每认出一个适配器，就给它和它的线**贴上手臂名**，以后一直绑在一起。

## 1. CAN 适配器对手臂

每条手臂认一次，把序列号填进 `robots/<站点>/can.toml`，替换掉 `"FILL"`。然后预览要写进系统的规则：

```bash
host/can_rules.py --check
```

**成功的样子：** 一段差异，其中四行以 `+` 开头，每行把一个序列号命名为 `can_<手臂名>`。如果提示有序列号没填或者重复了，回去核对。

生效：

```bash
sudo host/can_rules.py
```

**成功的样子：** 显示 `changed` 和 `udev reloaded and triggered`。再运行 `host/devices.sh`，`name` 一列变成 `can_<手臂名>`。

## 2. 相机对位置

每台相机认一次。在 `robots/<站点>/station.toml` 里，把 `FILL_cam_head_serial` 这类占位换成序列号。每台相机在文件里出现两次，两处都要换。

## 3. 检查

```bash
host/doctor.sh
```

**成功的样子：** `can-rule`、`can-links`、`station-file`、`cameras` 都是 PASS。

如果 `can-links` 是 FAIL，把那个适配器拔下再插上，或者重启电脑。

## 4. 记下来

在 `robots/<站点>/LOG.md` 里记一条，写上每个对应关系，和改过的文件一起提交、推送。
