# 在中心服务器上跑团队服务

在一台能挂载 OSS 的服务器上，跑三样东西：给大家看全部数据的只读面板、中心数据目录（在这里建任务、计划、数据集，再同步到 OSS）、只读的飞书机器人。换服务器或重装时照这篇做。

服务器只需要 CPU，不需要 GPU。和 OSS 在同一个地域，挂载用内网地址。

## 三样东西各是什么

| 服务 | 做什么 | 端口 |
| --- | --- | --- |
| 只读面板 | 直接读 OSS 挂载，大家通过 ssh 隧道看全部录制、判断、计划；不能改任何东西 | 8090 |
| 中心数据目录 | 一个能写的面板：在这里建任务、计划、数据集。小记录放在本地，录制只链接到挂载上，不复制。每分钟和 OSS 同步一次 | 8091 |
| 飞书机器人 | 日报、进度、统计、任务说明，回答问题。只看不改，要写的事让人去面板 | 不占端口，主动连飞书 |

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

为什么：在中心建任务、计划、数据集的地方，写了再推到 OSS，各站点一分钟内拉到。

```bash
XPISODE_VENV=~/xpisode-site/.venv setsid nohup services/hub/run.sh \
  >> ~/.local/state/hub.log 2>&1 < /dev/null &
```

它先拉下 OSS 上所有的小记录，并把每条录制链接到挂载上，然后每分钟同步一次。成功的样子：日志里有 `pulled ... small files` 和 `linked ... recordings`；`curl -s localhost:8091/api/v1/tasks` 能列出任务。

要建东西的人运行 `ssh -N -L 8091:localhost:8091 <服务器>`，浏览器打开 http://localhost:8091。

## 5. 配置并启动飞书机器人

为什么：团队在飞书里查进度、建东西。

```bash
mkdir -p ~/.config/xirra && cp services/feishu/deploy/env.example ~/.config/xirra/env
chmod 600 ~/.config/xirra/env
```

由负责人在 `~/.config/xirra/env` 里填飞书应用的 ID 和 Secret、大模型、日报群。然后：

```bash
services/feishu/.venv/bin/xirra check
setsid nohup services/feishu/deploy/run.sh >> ~/.local/state/xirra/bot.log 2>&1 < /dev/null &
```

有 systemd 的服务器，用 `services/feishu/deploy/xirra.service` 代替最后一行。成功的样子：`check` 每一项都不是 MISSING；日志里有 `connected to wss://...feishu.cn`；在飞书里给机器人发"进度"有回复。

## 把 OSS 上的录制转成统一的存法

站点从 2026-11-17 起录制时就把图像存成 AV1（设计第 7.4 节）。之前的录制，在中心服务器上转换：从挂载点读出，转换、完整校验，再覆盖 OSS 上的原文件，每 20 条写一条转换记录、发一个索引段。原件不保留。站点收到转换记录后，自己会删掉本机已上传过的旧副本。

服务器上没有 GitHub，也没有能编 AV1 的显卡：把 `xpisode-format` 和 `scripts/oss.py` 拷过去，单独建一个环境，用 SVT-AV1 在 CPU 上编。

```bash
cd /root/xpisode-convert
venv/bin/python oss.py --root root convert --admin-config /root/.ossutilconfig --who <名字> --workers 32
```

- `--root` 是服务器本地放临时文件、记录和索引缓存的地方，不能是挂载点。
- 已经转过的会跳过，所以中断了再跑一遍就行；先用 `--limit 3` 试几条。
- 要用管理员的 ossutil 配置：覆盖原文件需要它。

## 机器人知道的团队情况从哪来

没有要手写的说明文件。机器人每十分钟重新读一遍：

| 知道什么 | 从哪来 |
| --- | --- |
| 有哪些站点、每台是什么 | 本仓库的 `robots/<站点>/`，`station.toml` 第一行注释；`robots/_archive/` 里的是已退役的 |
| 任务大家怎么叫 | 每个任务说明的中文概括（第一次用到时机器翻译，按版本存着），模型拿它对应「测温棒」「SMT」这类叫法 |
| 谁是谁、最近在做什么 | 人员名单，加上近 30 天谁采集、谁判断 |

所以加站点、退役站点、建任务、加人，都在原来的地方做，机器人自己会知道。服务器上的仓库要跟着 `git pull`，机器人才看得到新站点。

## 重启和查看

| 要做的 | 命令 |
| --- | --- |
| 看机器人在做什么 | `tail -f ~/.local/state/xirra/bot.log` |
| 重启机器人 | 用 `ps -eo pid,cmd \| grep "xirra bot"` 找到进程号，`kill <进程号>`，它 10 秒后自己起来 |
| 看中心数据目录的同步 | `tail -f ~/.local/state/hub.log` |
| 大家问了机器人什么 | `~/.local/state/xirra/questions.jsonl`，一问一答一行 |
