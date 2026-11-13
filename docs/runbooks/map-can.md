# CAN 适配器对手臂

把每个 CAN 适配器的序列号对到它连的那条臂（`follower_l`、`follower_r`、`leader_l`、`leader_r`），写进 `robots/<站点>/can.toml`，再生成 udev 规则。换了适配器、换了线，就再做一遍。

**先记住：序列号认的是适配器，不是手臂。** 给每个适配器和它的线都贴上手臂名，两者始终在一起；适配器不和别的线混用。不按 USB 插口的顺序猜。

不需要起站点，手臂不动，只拔插 USB。下面以 `yam_3` 为例；别的机器换成自己的站点名。

### 1. 看现在插着的适配器

谁：操作员（不用 sudo；agent 也可以经 ssh 跑）

```bash
cd ~/xpisode-site && host/can_list.sh
```

应该看到：每个适配器一行，有序列号、网口名（还没生成规则时是 `can0`..`can3`）、USB 路径、控制器。yam_3 上是四个：`208F3783454650062`、`20833395594E50182`、`20673463584550182`、`20603463584550182`。

### 2. 一次拔一个，认出它是哪条臂

谁：操作员（在机器旁边）

1. 顺着一条臂的线找到它的适配器，拔下 USB。
2. 再跑一次 `host/can_list.sh`。
3. 少了的那个序列号就是这条臂的。给适配器和线贴上臂名（例如 `follower_l`），插回去。
4. 四条臂都做一遍。

应该看到：每次拔下，列表正好少一行；插回后恢复。四个序列号各对一条臂，没有重复。

### 3. 写进 can.toml

谁：操作员，或在 Mac 上改后同步过去

把四个 `"FILL"` 换成序列号，例如：

```toml
follower_l = "<贴着 follower_l 的适配器序列号>"
```

然后检查（只读，不写系统）：

```bash
host/can_rules.py yam_3 --check
```

应该看到：要写进 `/etc/udev/rules.d/90-xpisode-can.rules` 的差异，四行，每行 `NAME="can_<角色>"`、`bitrate 1000000`、`ID_MM_DEVICE_IGNORE`。如果说 `serial not filled in` 或 `is given to A, B`，回到第 2 步。

### 4. 生成规则并生效

谁：操作员（sudo）

```bash
sudo host/can_rules.py yam_3
```

应该看到：差异、`changed /etc/udev/rules.d/90-xpisode-can.rules`、`udev reloaded and triggered`。（`sudo host/setup.sh yam_3` 也会做这一步。）

### 5. 验证

谁：操作员（agent 也可以经 ssh 跑）

```bash
host/can_list.sh
ip -br link | grep can_
host/doctor.sh yam_3
```

应该看到：`can_list.sh` 的网口名变成 `can_follower_l` 等，且和标签一致；`ip` 列出四个 `can_*`，状态 `UP`；doctor 的 `can-rule` 和 `can-links` 是 PASS。网口名没变：把那个适配器拔下再插上，或者重启。

### 6. 记下来

在 `robots/<站点>/LOG.md` 追加一条：日期、谁、`map-can`、四个对应关系、有没有和本文不一样的地方；和 `can.toml` 一起提交。本文写错了，在同一个提交里改本文。
