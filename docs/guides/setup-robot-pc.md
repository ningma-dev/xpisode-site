# 准备一台机器人电脑

把一台刚装好 Ubuntu 的电脑，变成能录数据的机器人电脑。新机器、重装的机器都按这篇做。

做完以后：电脑上装好了 xpisode，认得出每台相机和每条手臂，体检全部通过。**这篇不会让手臂动，也不会打开相机。** 调参和真机验收要人在场，另有文档。

## 开始前

- 一台装好 **Ubuntu 24.04** 的电脑，相机和手臂的线都已经接上；
- 这台电脑的 sudo 密码；
- 这台机器人的名字，比如 `yam_3`。名字在全团队唯一，以后不再改。下文写作 `<站点>`；
- 大约一小时。

这台机器人的所有资料都放在本仓库的 `robots/<站点>/` 里：配置、序列号、调参、日志。**文档里不写具体机器的数值，数值都在那个目录里。**

## 1. 装基础工具

```bash
sudo apt update
sudo apt install -y git curl build-essential libclang-dev ffmpeg can-utils
curl -LsSf https://astral.sh/uv/install.sh | sh
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y
```

关掉终端再打开一个（经 ssh 操作的话，重新登录一次），然后检查：

```bash
git --version && uv --version && cargo --version
```

**成功的样子：** 三行版本号。

## 2. 让这台电脑能访问 GitHub

代码仓库是私有的，电脑要有一把自己的钥匙。

```bash
ssh-keygen -t ed25519 -C "$(hostname)" -N "" -f ~/.ssh/id_ed25519
cat ~/.ssh/id_ed25519.pub
```

把打印出来的那一行，加到负责人指定的 GitHub 账号上（Settings → SSH and GPG keys）。然后：

```bash
ssh -T git@github.com
```

第一次会问要不要信任 github.com，输入 `yes`。

**成功的样子：** `Hi <账号>! You've successfully authenticated`。

## 3. 取本仓库

```bash
git clone git@github.com:ningma-dev/xpisode-site.git ~/xpisode-site
cd ~/xpisode-site
```

**之后所有命令都在 `~/xpisode-site` 里运行。**

## 4. 告诉这台电脑它是哪个站点

```bash
echo <站点> > .station
ls robots/<站点>/
```

`.station` 只存在这台电脑上，不提交。之后的脚本都靠它知道自己是哪个站点。

- **重装的机器：** 目录已经有了，继续下一步。
- **新机器人：** 复制一台同种机器人的目录，然后改三处：

  ```bash
  cp -r robots/<同种机器人> robots/<站点>
  rm -f robots/<站点>/tuned/*.json
  ```

  1. `robots/<站点>/station.toml` 里的 `station = "..."` 改成 `<站点>`；
  2. `robots/<站点>/LOG.md` 清空，只留标题。

  相机和 CAN 的序列号先不管，第 6 步再填。

## 5. 系统设置

```bash
sudo host/setup.sh <站点>
sudo reboot
```

这一步让系统准备好跑机器人：
- 手臂控制程序可以用实时优先级，否则控制会卡顿；
- 普通用户能打开相机和 CAN；
- 相机和 CAN 不会因为 USB 省电而断开；
- 加大 USB 缓冲区，三台相机同时跑才够用。

可以重复运行，已经设置过的会跳过。第一次运行时 CAN 那一项会显示"跳过"，因为还不知道哪个适配器接的是哪条手臂，第 6 步做完后会补上。

重启后检查：

```bash
host/doctor.sh
```

**成功的样子：** `rtprio`、`memlock`、`groups`、`udev-rules`、`usbfs`、`autosuspend`、`irq-layout` 显示 PASS。其余项后面的步骤会让它们通过。

## 6. 认出每个设备

照 [认出每个设备](identify-devices.md) 做：哪个 CAN 适配器接哪条手臂，哪台相机在哪个位置。需要人在机器旁边拔插 USB 线，手臂不会动。

**成功的样子：** `host/doctor.sh` 里 `can-links`、`station-file` 显示 PASS。

## 7. 装 xpisode

```bash
uv sync --locked
scripts/build_pinned.sh
```

装的版本是本仓库写定的版本，每台机器都一样。第一次要下载和编译，二三十分钟，主要看网速。要先做完第 1 步（会用到 Rust）。这一步不需要 sudo，也不碰硬件，可以和第 5、6 步错开做。

过程中出现 `npm warn allow-scripts … esbuild` 不用管。

**成功的样子：** 没有 `error`；`host/doctor.sh` 里 `venv`、`rtcore`、`panel` 显示 PASS。

## 8. 准备数据目录

录制约 120 GB/小时，要放在大的固态硬盘上。

```bash
mkdir -p ~/xpisode-data
df -hT ~/xpisode-data
```

**成功的样子：** 文件系统是 `ext4`、设备是 `/dev/nvme…`，可用空间以 TB 计。如果显示 `ntfs` 或 `fuseblk`，说明这块盘不对，停下来问负责人。

## 9. 配上传钥匙

数据上传到团队共用的 OSS。每台电脑一把钥匙，**只能上传和下载，不能删除**。

1. 向负责人要这台电脑的钥匙。
2. 按阿里云文档安装 ossutil 2.x，用这把钥匙配置。配置文件是 `~/.ossutilconfig`，然后运行 `chmod 600 ~/.ossutilconfig`。钥匙只放在这个文件里，不要贴到聊天、文档或仓库里。
3. 检查：

   ```bash
   .venv/bin/python scripts/oss.py status
   ```

**成功的样子：** 显示 OSS 上已有多少条录制、本机还有多少没上传。`host/doctor.sh` 的 `tools` 一项要到这一步之后才会通过。

## 10. 体检

```bash
host/doctor.sh
```

**成功的样子：** 最后一行是 `0 FAIL`。只有 `tuned`（调参）显示 TODO，这要等人在场时做。

## 11. 记录并提交

```bash
host/snapshot.sh > robots/<站点>/machine.md
```

在 `robots/<站点>/LOG.md` 末尾加一条，格式见 [文档怎么写](../README.md#记日志)。然后：

```bash
git add robots/<站点>
git commit -m "<站点>: set up on $(hostname -s)"
git push
```

`machine.md` 记下了这台电脑的硬件和每个设备的序列号。以后重装或换硬件，可以拿新的结果和它对比。

## 下一步（要人在场）

1. [给手臂调参](tune-arm.md)；
2. 真机验收：[上线清单](../background/launch.md) 第 3 节那张表。全部通过才开始正式采集。

## 重装时有什么不同

- **重装前**：运行 `.venv/bin/python scripts/oss.py status`，确认没有还没上传的数据。本机没提交的改动先提交并推送。
- **重装后**：从第 1 步开始做，第 4 步直接跳过。
- **第 6 步**：适配器、线和相机都没动过的话，只需要核对，不用再拔插。
- **第 11 步**：先运行 `git diff robots/<站点>/machine.md`，看硬件有没有变。
- **调参**：手臂没换，就沿用原来的。

## 哪一步不对

先别绕过去。在 `LOG.md` 里记下看到了什么；如果是文档写错了，在同一次提交里把这篇改对。
