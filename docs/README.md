# 文档

| 文件 | 是什么 |
| --- | --- |
| [design.md](design.md) | **唯一的设计文档**。原则、维护规则 M1–M9（第 3 节）、多台机器一个 OSS（7.6）、本体约定（8.4）。不另写 ADR |
| [requirements.md](requirements.md) | 穷举的需求 |
| [rules.md](rules.md) | 每种角色一张规则卡 |
| [launch.md](launch.md) | 上线清单和真机验收表（第 3 节） |
| [day1.md](day1.md) | 第一天的流程（11-12 计划，没录成） |
| [methods/](methods/) | 训练服务器、ACT / π0.5 笔记、评测计划模板 |
| [runbooks/add-machine.md](runbooks/add-machine.md) | 加一台机器、重装一台机器（按 ubuntu3 / yam_3 写好） |
| [runbooks/map-can.md](runbooks/map-can.md) | CAN 适配器对手臂 |
| [runbooks/deploy.md](runbooks/deploy.md) | 把钉住的版本装到机器上；回滚 |
| [onboarding/new-robot.md](onboarding/new-robot.md) | 接入一种新机器人（给 Tron2 的同事） |

前六项是 2026-11-13 从旧站点工作区交接包原样搬来的。里面的 `repos/xpisode`、`~/xpisode`、`/home/ubuntu/archive`、`deployments/`、`archive/` 指的是旧工作区和旧站点机，现在都不在了：代码看 GitHub 上的三个仓库（版本见 `pyproject.toml`），旧数据都在 OSS 上。字段、消息、路径的精确定义在框架仓库的 `docs/reference/`。

## 运维文档怎么积累

1. **手册在 `runbooks/`**。每一步写：确切的命令、谁来跑（操作员用 sudo / 操作员 / agent 经 ssh / Mac）、"应该看到"什么。写到没有背景的人也能照着做。
2. **能写成脚本的写成脚本，再配一个检查。** 检查是 `host/doctor.sh`（只读，每项 PASS / FAIL / TODO）；每一步的结尾是"跑 `host/doctor.sh`，某项应为 PASS"。
3. **每台机器人有 `robots/<站点>/LOG.md`**，每次操作都追加一条：日期、谁、哪个手册第几步、版本、结果、和文档不一样的地方。**某一步失败或文档写错了，就在记这条日志的同一个提交里把手册改对。**
4. **机器事实有快照**：`host/snapshot.sh`（只读）的输出存为 `robots/<站点>/machine.md`；重装后再跑一次和它对照。
