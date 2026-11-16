# 给 agent 的硬规矩

- **和用户一律用中文。** 先给结论，说人话。代码注释和提交信息用英文。
- **不在没约好的时间窗里起站点、让手臂动、打开相机**，假设备也一样。`scripts/run.sh`、`scripts/tune.sh`、`scripts/camera_baseline.sh` 都算。只读的 `host/doctor.sh`、`snapshot.sh`、`devices.sh` 不算。
- **要 sudo 的步骤由用户自己跑**；agent 不 ssh 到机器，除非用户同意。
- **不提交训练任务。** 用户自己提交。
- **推 GitHub、写 OSS、删数据，先得到用户同意。** 不建远端仓库、不在 OSS 上建东西。
- **设计文档在 xpisode 框架仓库的 `design/design.md`**，跟着代码改，不写 ADR。本仓库只讲部署。
- **机器人机器上不开发**：只 `git pull --ff-only`、`uv sync --locked`、`scripts/build_pinned.sh`（`docs/guides/update.md`）。开发在 Mac 上，用 `scripts/dev_link.sh`。
- **一台机器人一个目录** `robots/<站点>/`（站点文件、CAN 序列号、调参、快照、日志），都在 `main` 上；不按机器开分支。
- **机器事实不猜**：相机位置、CAN 对哪条臂、中断在哪个核，没确认就留 `FILL`。
- **密钥只在本地文件里**（`~/.ossutilconfig`、`~/.ssh/`），绝不进仓库、日志、记忆。
- 数据根不在仓库里（`~/xpisode-data`）。
- **每件事一个入口**：机器人电脑上只用 `scripts/` 里的 `.sh`（起站点、面板、OSS、调参、存姿态、相机对照），不直接跑 `.venv/bin/xpisode`、`scripts/oss.py`、`xpisode_i2rt.tune`、`xpisode_env.move`、`tools/camera/rs_bare.py`；站点只从 `.station` 来，站点的事实（序列号、控制组、输出路径）只从站点文件和 `robots/<站点>/` 来，不手抄。新加一种做法时，替换旧的，不并存。
- **在机器上做的每件事记进 `robots/<站点>/LOG.md`**：日期、谁、哪篇文档第几步、版本、结果、和文档不一样的地方。**某一步失败或文档写错了，在记日志的同一个提交里把文档改对。**
- **写文档前先读 `docs/README.md` 最后一节"写文档的规矩"**：通用、不写具体机器的数值、说人话、一篇一件事。操作文档在 `docs/guides/`。
