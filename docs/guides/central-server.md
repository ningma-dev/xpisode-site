# 在中心服务器上跑团队服务

在一台能挂载 OSS 的服务器上，跑三样东西：给大家看全部数据的只读面板、中心数据目录（在这里建任务、计划、数据集，再同步到 OSS）、飞书机器人。换服务器或重装时照这篇做。

服务器只需要 CPU，不需要 GPU。和 OSS 在同一个地域，挂载用内网地址。

## 三样东西各是什么

| 服务 | 做什么 | 端口 |
| --- | --- | --- |
| 只读面板 | 直接读 OSS 挂载，大家通过 ssh 隧道看全部录制、判断、计划；不能改任何东西 | 8090 |
| 中心数据目录 | 任务、计划、判断、数据集这些小记录放在本地，可以写；录制只链接到挂载上，不复制。每分钟和 OSS 同步一次 | 8091（只给机器人用） |
| 飞书机器人 | 回答进度、统计、任务；起草任务、计划、数据集，提议人确认后通过中心数据目录写入 | 不占端口，主动连飞书 |

站点那边的同步服务每分钟拉一次团队的任务和计划，所以这里建的计划，一两分钟后出现在站点面板上。

## 1. 准备服务器

为什么：后面几步需要挂载、钥匙和工具。

- OSS 桶挂载在 `/mnt/data_oss`，数据根是 `/mnt/data_oss/xpisode_data`。**这个挂载只读，任何程序都不往里写。**
- `~/.ossutilconfig`（mode 600）放一把能上传的钥匙，由负责人放，不经过聊天和文档。
- 装好 `git`、`uv`；编译 xpisode 需要 Rust（`rustup`）。

成功的样子：`ls /mnt/data_oss/xpisode_data/store` 能看到 `episodes`、`tasks`、`plans`；`ossutil ls oss://<桶>/xpisode_data/store/tasks/` 能列出任务。

## 2. 装本仓库和 xpisode

为什么：只读面板、中心数据目录和站点用同一版 xpisode。

```bash
git clone <本仓库地址> ~/xpisode-site && cd ~/xpisode-site
uv sync --locked
scripts/build_pinned.sh panel
cd services/feishu && uv sync && cd -
```

成功的样子：`.venv/bin/xpisode --help` 和 `services/feishu/.venv/bin/xirra --help` 都能运行。

## 3. 开只读面板

为什么：大家看全部数据的入口。

```bash
setsid nohup .venv/bin/xpisode serve --read-only /mnt/data_oss/xpisode_data \
  --local ~/xpisode-view --port 8090 >> ~/xpisode-view.log 2>&1 < /dev/null &
```

第一次要把每条录制读一遍来建索引，要等一阵。成功的样子：`curl -s localhost:8090/api/panel/overview` 里有 `"read_only":true`。

别人在自己电脑上运行 `ssh -N -L 8090:localhost:8090 <服务器>`，浏览器打开 http://localhost:8090。

## 4. 开中心数据目录

为什么：机器人写入任务、计划、数据集的地方，写了再推到 OSS。

```bash
mkdir -p ~/.local/state/xirra
XPISODE_VENV=~/xpisode-site/.venv setsid nohup services/feishu/deploy/hub.sh \
  >> ~/.local/state/xirra/hub.log 2>&1 < /dev/null &
```

它先拉下 OSS 上所有的小记录，并把每条录制链接到挂载上，然后每分钟同步一次。成功的样子：日志里有 `pulled ... small files` 和 `linked ... recordings`；`curl -s localhost:8091/api/v1/tasks` 能列出任务。

## 5. 配置并启动飞书机器人

为什么：团队在飞书里查进度、建东西。

```bash
mkdir -p ~/.config/xirra && cp services/feishu/deploy/env.example ~/.config/xirra/env
chmod 600 ~/.config/xirra/env
```

由负责人在 `~/.config/xirra/env` 里填飞书应用的 ID 和 Secret、大模型、日报群，以及 `WRITE_PANEL_URL=http://127.0.0.1:8091`。然后：

```bash
services/feishu/.venv/bin/xirra check
setsid nohup services/feishu/deploy/run.sh >> ~/.local/state/xirra/bot.log 2>&1 < /dev/null &
```

有 systemd 的服务器，用 `services/feishu/deploy/xirra.service` 代替最后一行。成功的样子：`check` 每一项都不是 MISSING；日志里有 `connected to wss://...feishu.cn`；在飞书里给机器人发"进度"有回复。

## 6. 对应人员

为什么：机器人要知道发消息的人在人员名单里叫什么，"我的采集"和写入才能用。

新同事给机器人发一条消息后，他的飞书编号会记在 `~/.local/state/xirra/unknown_people.jsonl`。把它加进 `~/.local/state/xirra/people.toml`：

```toml
"ou_xxxxxxxx" = "<人员名单里的名字>"
```

不用重启。成功的样子：他发"我"，回答的是他自己的采集时长。

## 重启和查看

| 要做的 | 命令 |
| --- | --- |
| 看机器人在做什么 | `tail -f ~/.local/state/xirra/bot.log` |
| 重启机器人 | 用 `ps -eo pid,cmd \| grep "xirra bot"` 找到进程号，`kill <进程号>`，它 10 秒后自己起来 |
| 看中心数据目录的同步 | `tail -f ~/.local/state/xirra/hub.log` |
| 机器人写过什么 | `~/.local/state/xirra/proposals.jsonl`，每条提议和结果一行 |
