# xpisode-site

团队的部署仓库：每台机器人机器的站点配置、每条臂的调参、共用脚本、主机设置、部署步骤、团队文档，以及三个代码库钉住的版本。

这里**不开发代码、不放代码库、不放数据**。代码在 GitHub 的 `xpisode`、`xpisode-realsense`、`xpisode-i2rt`；数据在每台机器的 `~/xpisode-data` 和中心 OSS。只有一个 `main` 分支，一台机器一个站点文件，不按机器开分支。

```text
pyproject.toml  uv.lock   钉住的版本（完整提交号）和全部依赖（只解析 Linux x86_64）
robots/<站点>/            一台机器人的一切：station.toml、machine.env、can.toml、tuned/、machine.md、LOG.md
robots/_shared/           YAM 共用的遥操作映射；robots/_archive/ 退役站点
profiles/                 本体约定（流名、单位、相机位置名）
scripts/                  run.sh serve.sh upload.sh、oss.py 同步、build_pinned.sh、dev_link.sh 及检查工具
host/                     setup.sh 主机设置、doctor.sh 检查、snapshot.sh 快照、CAN 和 USB 的只读列表
tools/camera/             相机丢帧检查
docs/                     设计（唯一一份）、需求、规则卡、上线清单；runbooks/ 运维手册
```

## 加一台机器

照 [docs/runbooks/add-machine.md](docs/runbooks/add-machine.md) 一步一步做（按 ubuntu3 / yam_3 写好了）：基础包 → uv、Rust → GitHub key → 取本仓库 → `sudo host/setup.sh` → CAN 对臂（[map-can.md](docs/runbooks/map-can.md)）→ 相机对位置 → 部署 → 数据根 → OSS key → `host/doctor.sh` 全 PASS。到这里停；调参和真机验收在约好的时间窗里另做。

## 部署一个版本

```bash
git pull --ff-only && uv sync --locked && scripts/build_pinned.sh
```

升版本、回滚见 [docs/runbooks/deploy.md](docs/runbooks/deploy.md)。

## 拿数据

```bash
.venv/bin/python scripts/oss.py status                                   # 没传的、等着的、OSS 上有的
.venv/bin/python scripts/oss.py pull --dataset <id>                      # 一个数据集
.venv/bin/python scripts/oss.py pull --run <id> --only <路径>            # 一个 checkpoint
```

凭据只在 `~/.ossutilconfig`。规则见 [docs/rules.md](docs/rules.md)。

## 在 Mac 上跨仓库开发

把三个代码库克隆在本仓库旁边（`../xpisode` 等），然后 `scripts/dev_link.sh`：本仓库的 `.venv` 改用这些源码目录（editable），`pyproject.toml` 和 `uv.lock` 不动。之后用 `.venv/bin/...` 或 `uv run --no-sync`；`scripts/dev_link.sh --status` 看现在用的是什么。Mac 上 RealSense 只能编辑不能运行（`pyrealsense2` 没有 macOS 版）。

给 agent 的规矩：[CLAUDE.md](CLAUDE.md)。
