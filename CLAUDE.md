# 给 agent 的硬规矩

- **和用户一律用中文。** 先给结论，说人话。代码注释和提交信息用英文。
- **不在没约好的时间窗里起站点、让手臂动、打开相机**，假设备也一样。`scripts/run.sh`、`tools/camera/`、`xpisode_i2rt.tune` 都算。只读的 `host/doctor.sh`、`snapshot.sh`、`can_list.sh`、`xhci_list.sh` 不算。
- **要 sudo 的步骤由用户自己跑**；agent 不 ssh 到机器，除非用户同意。
- **不提交训练任务。** 用户自己提交。
- **推 GitHub、写 OSS、删数据，先得到用户同意。** 不建远端仓库、不在 OSS 上建东西。
- **面板文字要短**：一个词的标签，不写解释句。
- **核心只记事实**；排名、质量判断、规则检查放在核心外面。
- **一份设计文档**：`docs/design.md`。不写 ADR。
- **机器人机器上不开发**：只 `git pull --ff-only`、`uv sync --locked`、`scripts/build_pinned.sh`（`docs/runbooks/deploy.md`）。开发在 Mac 上，用 `scripts/dev_link.sh`。
- **一台机器人一个目录** `robots/<站点>/`（站点文件、主机事实、CAN 序列号、调参、快照、日志），都在 `main` 上；不按机器开分支。
- **机器事实不猜**：相机位置、CAN 对哪条臂、中断在哪个核，没确认就留 `FILL`。
- **密钥只在本地文件里**（`~/.ossutilconfig`、`~/.ssh/`），绝不进仓库、日志、记忆。
- 数据根不在仓库里（`~/xpisode-data`）。
- **在机器上做的每件事记进 `robots/<站点>/LOG.md`**：日期、谁、哪个 runbook 第几步、版本、结果、和文档不一样的地方。**某一步失败或文档写错了，在记日志的同一个提交里把 runbook 改对。**
- 运维步骤写在 `docs/runbooks/`：每步有确切命令、谁来跑、"应该看到"；能写成脚本的写成脚本，再由 `host/doctor.sh` 检查。
