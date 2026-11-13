# 准备一台机器人电脑

把一台刚装好 Ubuntu 的电脑，变成能录数据的机器人电脑。新装和重装都按这篇做。

做完以后，电脑上装好了 xpisode，认得出每个设备，体检通过。**这篇不会让手臂动，也不会打开相机。**

## 开始前

- 一台装好 Ubuntu 24.04 的电脑，设备的线都已接好；
- 这台电脑的 sudo 密码；
- **站点名**：这台机器人在 `robots/` 下的目录名，比如 `yam_3`，全团队唯一，定了就不改。下文写作 `<站点>`。

下面的步骤以 YAM 为例。其他机型见[接入一种新机器人](new-robot-type.md)。

## 1. 装基础工具

```bash
sudo apt update
sudo apt install -y git curl build-essential libclang-dev ffmpeg can-utils
curl -LsSf https://astral.sh/uv/install.sh | sh
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y
```

退出终端（或 ssh）重新登录，然后：

```bash
git --version && uv --version && cargo --version
```

**成功的样子：** 打印三行版本号。

## 2. 让电脑能访问 GitHub

代码仓库是私有的，电脑需要自己的钥匙。

```bash
ssh-keygen -t ed25519 -C "$(hostname)" -N "" -f ~/.ssh/id_ed25519
cat ~/.ssh/id_ed25519.pub
```

把打印出的那一行加到负责人指定的 GitHub 账号上（Settings → SSH and GPG keys）。这个账号要能读 `xpisode`、`xpisode-realsense`、`xpisode-i2rt`，能写 `xpisode-site`。

```bash
ssh -T git@github.com
```

第一次会问是否信任 github.com，输入 `yes`。

**成功的样子：** `Hi <账号>! You've successfully authenticated`。

## 3. 取本仓库

```bash
git clone git@github.com:ningma-dev/xpisode-site.git ~/xpisode-site
cd ~/xpisode-site
git config user.name "<站点> robot PC"
git config user.email "<站点>@xpisode-site.invalid"
```

后两行让这台电脑提交日志时署上站点名。

之后的命令都在 `~/xpisode-site` 里运行。

## 4. 告诉电脑它是哪个站点

```bash
echo <站点> > .station
```

`.station` 只留在这台电脑上，不提交。之后的脚本靠它知道自己是哪个站点。

**重装的电脑**：`robots/<站点>/` 已经有了，做完上面这一行就进入下一步。

**新的机器人**：先从模板建目录，

```bash
cp -r robots/_template/yam robots/<站点>
```

然后把 `robots/<站点>/station.toml` 里的 `station = "FILL"` 改成站点名。

设备序列号先留着 `FILL`，第 7 步再填。

## 5. 系统设置

```bash
sudo host/setup.sh
sudo reboot
```

它做的事：让手臂控制程序能用实时优先级，让普通用户能打开相机和 CAN，关掉相机和 CAN 的 USB 省电，加大 USB 缓冲区。重复运行也没关系。

重启后：

```bash
host/doctor.sh
```

**成功的样子：** `rtprio`、`memlock`、`groups`、`udev-rules`、`usbfs`、`autosuspend` 都是 PASS。其他项这时是 FAIL 或 TODO，属于正常，后面的步骤会逐项解决。

## 6. 装 xpisode

```bash
uv sync --locked
scripts/build_pinned.sh
```

装的是本仓库写定的版本，每台电脑都一样。第一次要下载和编译，二三十分钟。中途出现 `npm warn allow-scripts` 可以不管。

**成功的样子：** 没有 `error`；`host/doctor.sh` 里 `venv`、`rtcore`、`panel` 都是 PASS。

## 7. 认出每个设备

照[认出每个设备](identify-devices.md)做，需要人在电脑旁边拔插 USB 线。

**成功的样子：** `host/doctor.sh` 里 `can-rule`、`can-links`、`station-file`、`cameras` 都是 PASS。

## 8. 准备数据目录

录制很占空间，要放在最大的固态硬盘上。

```bash
mkdir -p ~/xpisode-data
```

如果最大的盘不是系统盘，就在那块盘上建目录，再链接过来：`ln -s <那块盘上的目录> ~/xpisode-data`。

**成功的样子：** `host/doctor.sh` 里 `data-root` 是 PASS，可用空间以 TB 计。

## 9. 配上传钥匙

数据上传到团队共用的 OSS。所有机器人电脑共用一把上传钥匙，它只能上传和下载，不能删除。

1. 下载阿里云的 ossutil 2.x，把可执行文件放到 `~/.local/bin/ossutil`。
2. 运行 `~/.local/bin/ossutil config`，填入负责人给的钥匙，地域填 `cn-shanghai`。然后运行 `chmod 600 ~/.ossutilconfig`。钥匙只放在这个文件里。
3. 检查：

   ```bash
   .venv/bin/python scripts/oss.py status
   ```

**成功的样子：** 显示 OSS 上已有多少条录制；`host/doctor.sh` 里 `tools` 是 PASS。

## 10. 体检

```bash
host/doctor.sh
```

**成功的样子：** 最后一行是 `0 FAIL`。只剩 `tuned` 是 TODO，留到调参时做。

## 11. 记录并提交

```bash
host/snapshot.sh > robots/<站点>/machine.md
```

在 `robots/<站点>/LOG.md` 末尾加一条，格式见[记日志](../README.md#记日志)。然后：

```bash
git add robots/<站点>
git commit -m "<站点>: set up"
git push
```

`machine.md` 是这台电脑的硬件快照。以后重装或换硬件，拿新的和它对比。

只改了 `robots/<站点>/` 时，可以直接推送到 `main`。改到其他地方，要走 Pull Request。

## 下一步

1. [给手臂调参](tune-arm.md)
2. [真机验收](acceptance.md)，全部通过才开始正式采集

## 重装时有什么不同

- **重装前**：运行 `.venv/bin/python scripts/oss.py status`，确认"没上传的"是 0。本机没推送的提交先推送。
- **第 4 步**：只写 `.station`。
- **第 7 步**：设备和线都没动过的话，只运行 `host/doctor.sh`，确认 `can-links`、`cameras` 是 PASS。
- **第 11 步**：先运行 `git diff robots/<站点>/machine.md`，看硬件有没有变化。

## 哪一步不对

不要绕过去。在 `LOG.md` 里记下看到了什么。如果是文档写错了，在同一次提交里把文档改对。
