# 加一台机器 / 重装一台机器

照着做，不需要别的背景。命令按 **ubuntu3 / 站点 `yam_3`** 写好了，可以直接用；别的机器把 `yam_3`、`ubuntu3` 换成自己的（`robots/README.md` 有对照表）。

每一步写了**谁**来做：
- **操作员（sudo）**：用户本人在机器上，要输密码的都是这一类；
- **操作员**：在机器上，不用 sudo；
- **agent（ssh）**：用户同意后，agent 可以经 ssh 跑的只读或不用 sudo 的步骤；
- **Mac**：在用户的 Mac 上做。

每一步写了**应该看到**什么。看到的不一样：停下，按第 14 节记下来。

**本文到第 13 步为止，不碰相机、不让手臂动、不起站点。** 调参和真机验收由操作员在约好的时间窗里另做（第 14 节之后）。

## 开始之前

| 要先有的 | 现在 |
| --- | --- |
| 本仓库的 `uv.lock` | 有（2026-11-13 在 Mac 上锁的，56 个包，只解析 Linux x86_64） |
| 站点名、机器名 | `yam_3` / `ubuntu3`（`robots/yam_3/`） |
| 相机对位置、CAN 对手臂 | 未知，在第 7、8 步确认 |

## 1. 系统基础包

谁：操作员（sudo）

```bash
sudo apt update
sudo apt install -y git curl build-essential libclang-dev ffmpeg can-utils
```

- `build-essential`、`libclang-dev`：编译框架中心（Rust，iceoryx2 要 libclang）和 i2rt 控制器；
- librealsense **不用装 apt 包**：`pyrealsense2` 在第 9 步随 `uv sync` 从 PyPI 装（xpisode-realsense `docs/installation.md`），它用 libusb，访问权限由第 6 步的 `92-xpisode-d405.rules` 给；
- `ffmpeg`：录制和代理视频；`can-utils`：`candump`、`cansend`，排查 CAN 用。

应该看到：最后没有 `E:` 开头的错误。`git --version`、`gcc --version`、`ffmpeg -version`、`candump` 都能运行（`candump` 不带参数会打印用法）。

## 2. uv 和 Rust

谁：操作员（不用 sudo，装在自己账号下）

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y
source "$HOME/.local/bin/env" && source "$HOME/.cargo/env"
uv --version && cargo --version
```

应该看到：`uv 0.x.y` 和 `cargo 1.x.y`。新开的终端会自动有这两个命令。

## 3. GitHub 访问（操作员自己做）

谁：操作员

三个代码库和本仓库都是私有的。在机器上生成一把 key，把公钥加到 GitHub：

```bash
ssh-keygen -t ed25519 -C "ubuntu3" -f ~/.ssh/id_ed25519 -N ""
cat ~/.ssh/id_ed25519.pub
```

把打印出来的一行加到 GitHub。两种做法，由用户定：
- 加到你自己的账号（Settings → SSH and GPG keys）：一把 key 能读所有库，但也能写；
- 一个只读的机器账号，加为三个库的协作者（Read），key 加到那个账号。
（GitHub 的 deploy key 一把只能给一个库，而 `pyproject.toml` 里三个库用同一个主机名，所以不用 deploy key。）

```bash
ssh -T git@github.com
```

应该看到：`Hi <账号>! You've successfully authenticated, but GitHub does not provide shell access.` 第一次会问是否信任 github.com 的指纹，答 `yes`。key 只放在这台的 `~/.ssh/`。

## 4. 取本仓库

本仓库还没有 GitHub 远端（建远端要用户同意）。在那之前由 Mac 打成 git bundle 拷过去：

谁：Mac

```bash
git -C ~/Dev/xirra/xpisode/xpisode-site bundle create /tmp/xpisode-site.bundle main
scp /tmp/xpisode-site.bundle ubuntu@ubuntu3:
```

谁：操作员

```bash
git clone -b main ~/xpisode-site.bundle ~/xpisode-site
cd ~/xpisode-site && git log --oneline -1
```

应该看到：`~/xpisode-site` 里有 `robots/`、`host/`、`scripts/`；`git log` 是 Mac 上最新的提交。有了 GitHub 远端以后改成 `git clone git@github.com:ningma-dev/xpisode-site.git ~/xpisode-site`。

## 5. 机器快照

谁：操作员，或 agent（ssh）

```bash
cd ~/xpisode-site
host/snapshot.sh > /tmp/machine.md
diff robots/yam_3/machine.md /tmp/machine.md
```

应该看到：只有日期、`usbfs_memory_mb`、用户组、udev 规则这些第 6 步要改的地方不同；OS、内核、CPU、相机和 CAN 的序列号与控制器和 `robots/yam_3/machine.md` 一样。硬件不一样（换了口、换了相机）：以新的为准，把 `/tmp/machine.md` 拷成 `robots/yam_3/machine.md`，并更新 `robots/yam_3/machine.env` 的 `XPISODE_DEVICE_XHCI`（第 8 步）。

## 6. 主机设置

谁：操作员（sudo）

```bash
cd ~/xpisode-site
sudo host/setup.sh yam_3
```

它做的事（`host/README.md`）：实时优先级（调用 xpisode-i2rt 自己的 `setup_host.sh`，它先以你的身份从 GitHub 取钉住的版本）、用户组 `dialout plugdev video`、USB 不自动挂起、D405 访问权限、FTDI 延迟、usbfs 内存 1000 MB。CAN 规则这一次会跳过（第 7 步之后再生成）。可以重复跑。

应该看到：每个文件一行 `changed` 或 `unchanged`；CAN 那一项是 `skipped map the adapters first`；最后 `NEXT: reboot ...`。没有 `FAILED`（有的话多半是第 3 步的 GitHub 访问没好）。

然后**重启**（实时限制和新加的组要重新登录才生效，桌面终端要重启才拿到限制）：

```bash
sudo reboot
```

重启后：

```bash
cd ~/xpisode-site && host/doctor.sh yam_3
```

应该看到：`rtprio`、`memlock`、`groups`、`udev-rules`、`usbfs`、`autosuspend` 是 PASS。

## 7. CAN 适配器对手臂

谁：操作员（在机器旁边）

照 [map-can.md](map-can.md) 做：贴标签、一次拔一个、填 `robots/yam_3/can.toml`、`sudo host/can_rules.py yam_3`。

应该看到：`host/doctor.sh yam_3` 的 `can-rule`、`can-links` 是 PASS（四个 `can_*` 在 1 Mbit/s 上 UP）。

## 8. 相机对位置，量中断

谁：操作员（在机器旁边）。不打开相机，只拔插 USB。

```bash
host/xhci_list.sh
```

应该看到：三台 D405（`262723072517`、`262723077218`、`262723071989`）和四个 CAN 适配器，各自的 USB 路径、控制器（`0000:0d:00.0`、`0000:79:00.4`、`0000:0f:00.0`）和中断所在的核（4、8、5）。

1. **相机对位置**：顺着头顶相机的线拔下它，再跑 `host/xhci_list.sh`，少了的那个序列号就是 `cam_head`；插回。左腕、右腕同样做。把 `robots/yam_3/station.toml` 里的 `FILL_cam_head_serial` 等换成序列号（每台相机两处：`exclusive` 和 `serial`）。
2. **中断**：核对 `robots/yam_3/machine.env` 的 `XPISODE_DEVICE_XHCI` 和上面列出的一致（每个用到的控制器一行，后面是它的核）。不一致就改 `machine.env`。
3. **可选，默认不做**：把这些控制器的中断钉在 `machine.env` 写的核上，重启也不变：

   ```bash
   sudo host/setup.sh yam_3 --pin-irqs
   ```

   应该看到：每个控制器的每个中断一行 `0000:0d:00.0 irq 45 -> cpu 4` 这样的输出。用到的控制器没写进 `machine.env` 会拒绝并列出来。

应该看到：`host/doctor.sh yam_3` 的 `irq-layout`、`background-cpus` 是 PASS；yam_3 的后台核是 `1,2,3,6,7,9,10,11,14,15`（避开 4、5、8 和它们的超线程 12、13、0）。`station-file` 是 PASS（相机序列号都填了）。

## 9. 部署钉住的版本

谁：操作员，或 agent（ssh）

```bash
cd ~/xpisode-site
uv sync --locked
scripts/build_pinned.sh
```

应该看到：`uv sync` 没有 `error`，`.venv/` 建好（第一次要编译框架中心，几分钟）；`build_pinned.sh` 打印 `rtcore: ... at xpisode-i2rt <sha>` 和 `panel: ... at xpisode <sha>`。详细见 [deploy.md](deploy.md)。

再跑 `host/doctor.sh yam_3`：`venv`、`rtcore`、`panel` 是 PASS。

## 10. 数据根

谁：操作员，或 agent（ssh）

```bash
mkdir -p ~/xpisode-data
df -hT ~/xpisode-data
```

应该看到：文件系统是 NVMe 上的 `ext4`（`/dev/nvme...`），可用约 3 T；**不是** `ntfs`、`fuseblk`。所有机器的数据根都是 `~/xpisode-data`（`pyproject.toml` 的 `[tool.xpisode] data`），不在仓库里。`host/doctor.sh yam_3` 的 `data-root` 是 PASS。

## 11. OSS（只能新增的 key）

谁：管理员（用户）在阿里云控制台；操作员在机器上

1. 管理员给这台机器建一个 RAM 用户，只给 `oss://shanghai-ai-prod-oss/xpisode_data/store/` 前缀的 `oss:PutObject`、`oss:GetObject`、`oss:ListObjects`，**不给删除**（`docs/rules.md` 管理员设置）。
2. 按阿里云文档装 ossutil 2.x 到 `~/.local/bin/ossutil`，用这把 key 配置 `~/.ossutilconfig`，然后 `chmod 600 ~/.ossutilconfig`。key 不进仓库、日志、聊天记录。
3. 检查（只读）：

   ```bash
   .venv/bin/python scripts/oss.py status
   ```

   应该看到：本机没有要传的；OSS 上有多少条录制（按索引）。几台机器共用一个 store，不按机器分目录。

`host/doctor.sh yam_3` 的 `tools` 是 PASS（包括 ossutil）。

## 12. 全部检查

谁：操作员，或 agent（ssh）

```bash
host/doctor.sh yam_3
```

应该看到：所有项 PASS，只剩 `TODO tuned`（调参要手臂动，留到时间窗里）；最后一行 `0 FAIL`。

## 13. 保存快照，记日志，提交

谁：操作员，或 agent（ssh）

```bash
host/snapshot.sh > robots/yam_3/machine.md
git add -A robots/yam_3 && git commit -m "yam_3: first setup on ubuntu3"
```

`robots/yam_3/LOG.md` 追加一条（第 14 节的格式）。本仓库还没有远端：提交先留在机器上，由 Mac 那边取回（`git bundle create` 反方向拷，或者等有了远端再推，推送要用户同意）。

**到这里停。** 打开相机、让手臂动、起站点的都不在本文里。

## 14. 记日志的规矩

每次在机器上做事，都在 `robots/<站点>/LOG.md` 末尾追加一条：

```markdown
## 2026-11-14 · ningma · add-machine 1–13
- 版本：xpisode-site <sha>；xpisode 01d6d59，xpisode-realsense 223c00d，xpisode-i2rt c458074
- 结果：doctor 0 FAIL，TODO tuned
- 和文档不一样的：第 6 步 …（已在同一个提交里改了 add-machine.md）
```

**某一步失败了，或者文档写错了：在记这条日志的同一个提交里把 runbook 改对。**

## 之后，在约好的时间窗里（操作员）

- 调参：每条跟随臂测一次（`robots/README.md`），结果放 `robots/yam_3/tuned/`，提交；
- 真机验收：`docs/launch.md` 第 3 节的表，一项一项做，结果记进 `LOG.md`。**有一项不过，就不开始正式采集。**
- 之后：`scripts/run.sh --who <名字>` 起站点，`scripts/serve.sh` 开面板，`scripts/upload.sh` 常开上传。

## 重装一台机器

站点名不变（`yam_3` 还是 `yam_3`），录制里记的就是它。

1. 重装前（agent 可以经 ssh 跑）：`.venv/bin/python scripts/oss.py status`，确认"没上传的"是 0；要留的本机文件只有 `~/xpisode-data/local/sync/ledger.jsonl` 和 `local/deleted.jsonl`（框架 `docs/how-to/local-workspace.md`），其余都能从 OSS 重建。没提交的 `robots/yam_3/` 改动先提交并取回 Mac。
2. 重装系统，然后从第 1 步做起。第 5 步的 `diff` 就是重装前后的对照：一样的跳过确认，不一样的以新的为准。
3. 适配器、线、相机都没动过：第 7、8 步只做验证（`host/can_list.sh`、`host/xhci_list.sh` 和文件一致），不用重新拔插。
4. 调参：臂没换就沿用 `robots/yam_3/tuned/`。
