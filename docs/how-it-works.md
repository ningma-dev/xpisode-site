# 本仓库是怎么组织的

只想照着操作的话，不用读这篇。

## 只放部署

代码在 xpisode、xpisode-realsense、xpisode-i2rt 三个仓库里，数据在每台机器的 `~/xpisode-data` 和团队共用的 OSS 上。本仓库只记录"我们有哪些机器人、用哪个版本、怎么装"。任何一台机器坏了，都能从 GitHub 和 OSS 完整恢复。

## 一台机器人一个目录

```text
robots/<站点>/
  station.toml   这台机器人有哪些设备、怎么录
  machine.env    这台电脑的情况：相机和 CAN 插在哪个 USB 控制器上
  can.toml       每条手臂用的 CAN 适配器序列号
  tuned/         每条跟随臂的调参结果
  machine.md     装机时的硬件快照，以后拿来对比
  LOG.md         在这台机器上做过的事
```

- 只有一个 `main` 分支。每台机器都拉同一份，各自读自己的目录。
- 每台电脑在仓库里有一个不提交的 `.station` 文件，写着自己是哪个站点。
- 目录名就是站点名，每条录制都会记下它，定下来就不改。

## 版本

`pyproject.toml` 和 `uv.lock` 写定三个代码仓库和所有依赖的版本，所以本仓库的一次提交就是一个版本。

## 三个容易问的为什么

**CAN 适配器为什么按序列号起名？** 这样适配器插在哪个口都不影响名字。四个名字在每台 YAM 上都一样（`can_follower_l` 等），所以各台的站点文件只有序列号不同。

**后台任务为什么要避开某些 CPU 核？** 每个 USB 控制器由一个固定的核处理。上传、生成视频这些后台任务如果跑在相机用的核上，相机会丢帧。`machine.env` 记下相机和 CAN 用的核，脚本把后台任务放到其他核上。

**为什么关掉相机的 USB 省电？** 相机闲置两秒就会被系统挂起，下次启动站点时要先唤醒，唤醒失败就打不开。Linux 的摄像头驱动会自动打开省电，所以规则里用的是"永不挂起"这个设置。

## 脚本

| 脚本 | 做什么 | 会改系统吗 |
| --- | --- | --- |
| `host/setup.sh` | 系统设置 | 会，要 sudo |
| `host/can_rules.py` | 生成 CAN 命名规则 | 加 `--check` 只预览；不加要 sudo |
| `host/doctor.sh`、`host/snapshot.sh`、`host/can_list.sh`、`host/xhci_list.sh` | 体检、快照、列出设备 | 不会，也不打开设备 |
| `scripts/run.sh` | 启动站点 | 手臂会动，要有人在场 |
| `scripts/serve.sh`、`scripts/upload.sh` | 网页面板、自动上传 | 不会 |

## 在 Mac 上同时改几个仓库

把代码仓库克隆到本仓库旁边，运行 `scripts/dev_link.sh`，本仓库的环境就会用你改过的源码。`uv sync --locked` 恢复到写定的版本。
