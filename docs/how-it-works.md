# 本仓库是怎么组织的

只想照着操作的话，不用读这篇。

## 只放部署

任何一台电脑坏了，配置能从 GitHub 恢复，已上传的数据能从 OSS 恢复。没上传的数据和本机的钥匙恢复不了。

## 一台机器人一个目录

```text
robots/<站点>/
  station.toml   这台机器人有哪些设备、怎么录
  machine.env    这台电脑的情况：相机和 CAN 插在哪个 USB 控制器上
  can.toml       每条手臂用的 CAN 适配器序列号
  tuned/         每条跟随臂的调参结果
  machine.md     装机时的硬件快照，以后拿来对比
  LOG.md         在这台电脑上做过的事
```

- 只有一个 `main` 分支。每台电脑都拉同一份，各自读自己的目录。
- 每台电脑在仓库里有一个不提交的 `.station` 文件，写着自己是哪个站点。
- `robots/_template/` 是新建站点用的模板。
- 目录名就是站点名，每条录制都会记下它，定下来就不改。

## 三个容易问的为什么

**CAN 适配器为什么按序列号起名？** 这样适配器插在哪个口都不影响名字。四个名字在每台 YAM 上都一样（`can_follower_l` 等），所以各台的站点文件只有序列号不同。

**后台任务为什么要避开某些 CPU 核？** 每个 USB 控制器由一个固定的核处理。上传、生成视频这些后台任务如果跑在相机用的核上，相机会丢帧。`machine.env` 记下相机和 CAN 用的核，脚本把后台任务放到其他核上。

**为什么关掉相机的 USB 省电？** 相机闲置两秒就会被系统挂起。下次启动站点时要先把它唤醒，唤醒失败，相机就打不开。

## 脚本

| 脚本 | 做什么 | 会改系统吗 |
| --- | --- | --- |
| `host/setup.sh` | 系统设置 | 会，要 sudo |
| `host/can_rules.py` | 生成 CAN 命名规则 | 加 `--check` 只预览；不加要 sudo |
| `host/doctor.sh`、`host/snapshot.sh`、`host/can_list.sh`、`host/xhci_list.sh`、`host/camera_serials.py` | 体检、快照、列出设备 | 不会，也不让相机开始拍摄 |
| `scripts/run.sh` | 启动站点 | 手臂会动，要有人在场 |
| `scripts/serve.sh`、`scripts/upload.sh` | 网页面板、自动上传 | 不会 |

## 在 Mac 上同时改几个仓库

把代码仓库克隆到本仓库旁边，运行 `scripts/dev_link.sh`，本仓库的环境就会用你改过的源码。`uv sync --locked` 恢复到写定的版本。
