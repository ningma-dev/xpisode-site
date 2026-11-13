# 规则卡 · Rule cards

每种角色一张卡（设计第 7.6 节、M9）。卡片就是上手说明。
One card per role (design §7.6, M9). The card is the onboarding.

## 采集员 · Collector

1. 开工前在顶栏选自己的名字。
2. 录不了时，站点页和语音会说原因。
3. 录完一条：做成了或者失败了，都按"要"（失败的要判为失败）；录坏了、不算一次有效的尝试，按"重录"，它会被直接删掉，不会上传。

---

1. Before you start, pick your name in the top bar.
2. When it cannot record, the station page and the voice say why.
3. After a recording: success or failure, press 要 (keep; judge a failure as failure). Spoiled, not a valid attempt: press 重录 (redo). It is deleted here and never uploaded.

## 研究员和 agent · Researchers and agents

1. 拿数据：数据集 id 加一条拉取命令。
2. 训练在服务器本地跑，结果推回 OSS，并声明读了哪个数据集、哪份配方、哪个基模。
3. 上真机：客户端声明读了哪个 checkpoint。
4. 上真机前：在一条训练录制上跑 `scripts/replay_check.py`，看重影，误差和延迟要小。
5. 后判的取代先判的；建数据集时用的是每条录制当前的判断，并会记下来。

---

1. Data: a dataset id and one pull command.
2. Train locally on the server, push the results back to OSS, and declare which dataset, recipe and base model it read.
3. On the robot: the client declares which checkpoint it read.
4. Before the robot: `scripts/replay_check.py` on a training recording; watch the ghost; errors and lag must be small.
5. A later judgment replaces an earlier one; a dataset uses each recording's current judgment, and records it.

```bash
.venv/bin/python scripts/oss.py --root <local> pull --dataset <id> --link --mount /mnt/data_oss/xpisode_data  # 拿数据 · bring data
.venv/bin/python scripts/oss.py --root <local> push                        # 推回 · push back
.venv/bin/python scripts/oss.py pull --run <training run>                  # 取回 checkpoint，不带数据 · bring back a checkpoint
```

训练服务器把桶只读挂在 `/mnt/data_oss/xpisode_data`；`--root` 是服务器本地的目录，不在挂载点下面。 · Training servers mount the bucket read only at `/mnt/data_oss/xpisode_data`; `--root` is a local folder, never under the mount.

## 管理员 · Admin

1. 数据自动推到 OSS，面板上只显示"还有多少没上传"。
2. 本机满了会自动清理，永远不删最后一份。
3. 已经上传的录制要删除：`oss.py delete <id> --reason ...`，不能撤回，执行前会列出受影响的数据集。
4. 加机器时定好站点名，发给它一份只能新增的 OSS 凭证。

---

1. Data goes to OSS by itself; the panel shows only how much is not uploaded yet.
2. When a machine fills up it cleans itself up, never deleting the last copy.
3. To delete an uploaded recording: `oss.py delete <id> --reason ...`. It cannot be undone; it lists the affected datasets first.
4. When adding a machine, fix its station name and give it an add-only OSS key.

```bash
.venv/bin/python scripts/oss.py push --follow                              # 每台机器常开，新录的先传 · always on, every machine, newest first
.venv/bin/python scripts/oss.py push --dataset <id>                        # 先传一个数据集，传完就停 · one dataset first, then stop
.venv/bin/python scripts/oss.py status                                     # 没上传的、等着的、OSS 上有的 · unpushed, waiting, on OSS
.venv/bin/python scripts/oss.py delete <id> --reason "..." --admin-config ~/.ossutil-admin --who <name>
```

## 管理员设置 · Admin setup

- **站点名全局唯一。** 加机器时定好，不和任何一台重名。 · Station names are unique across all machines.
- **每台采集机一份只能新增的凭证**：一个 RAM 用户，只给 `xpisode_data/store/` 前缀的 `oss:PutObject`、`oss:GetObject`、`oss:ListObjects`，不给 `oss:DeleteObject`。写进那台机器的 `~/.ossutilconfig`（权限 600）。 · One add-only key per collection machine: put, get and list on the prefix, no delete.
  - **还没做：** 站点机器现在的 `~/.ossutilconfig` 能删除（2026-11-11 在测试前缀上试过，`delete` 成功）。只能新增的 RAM 用户要管理员在阿里云控制台建；建好后，现在这把从采集机上拿走，归管理员作 `--admin-config`。 · **Not yet done:** this station's current key can delete (tried 2026-11-11 on a test prefix). The add-only RAM user must be created in the console by the admin; then the current key leaves the station and becomes the admin's `--admin-config`.
- **管理员凭证只用于删除**，放在单独的配置文件里，只在 `delete --admin-config` 时给出，不放在采集机上。 · The admin key is only for `delete`, in its own config file, never on a collection machine.
- **桶打开版本控制，被删的（和被覆盖的）版本保留 30 天**：生命周期规则，历史版本 30 天后过期。 · Turn on OSS versioning; a lifecycle rule expires noncurrent versions after 30 days, so a mistaken delete (or overwrite) can be undone within 30 days.
  - **还没做：** 2026-11-11 桶的版本控制没有打开，误删的不能找回。 · **Not yet done:** on 2026-11-11 versioning is off; a mistaken delete cannot be undone.
- **索引段**：每次上传都在 `xpisode_data/store/index/` 留一个段，`pull` 和 `status` 只读它们，不列整个桶；段只是索引，不要删。 · Every upload leaves an index segment under `index/`; `pull` and `status` read only those. Do not delete them.
