# 接入一种新机器人

给第一次用 xpisode 的同事。第一个例子是 Tron2（`robots/tron2_1/`）。读完这一页就知道要拿到什么、要写什么、按什么顺序做。

## 1. 你会得到什么

xpisode 管的是"录数据"这一整条路：站点实时读设备、录成 Episode（MCAP 文件），面板上判断"要 / 失败 / 重录"、建数据集，再上传到团队共用的 OSS。训练和评测从 OSS 读。

你不用改框架。你要做的只有三件：
1. **写一个设备适配器**，把 Tron2 的 SDK 接进 xpisode；
2. **在本仓库加一个机器人目录** `robots/tron2_1/`；
3. **写 Tron2 的本体约定**：流叫什么、单位、动作是什么意思。

## 2. 仓库和权限

| 仓库 | 是什么 | 你的权限 |
| --- | --- | --- |
| `ningma-dev/xpisode` | 框架 | 只读；要改框架就提 issue 或 PR，由负责人合 |
| `ningma-dev/xpisode-site`（本仓库） | 团队的机器人配置、脚本、文档 | 可写；`robots/tron2_*` 和 Tron2 的约定由你批，共用部分由负责人批（见 `.github/CODEOWNERS`） |
| `xpisode-tron2`（新建，你的） | Tron2 适配器 | 你的 |
| `ningma-dev/xpisode-realsense` | D405 相机驱动 | 只读；Tron2 用 D405 时才需要 |

OSS：你的机器领一把**只能上传、下载、列出**的钥匙，放在机器上的 `~/.ossutilconfig`，不进任何仓库。删除已上传的数据只有管理员能做。

## 3. 按顺序做

1. **先在 Mac 或任意电脑上跑通演示**：框架的 `docs/tutorials/first-recording.md`。不需要机器人，二十分钟就能看懂录制、Episode 和面板。
2. **写适配器**：照框架的 `docs/tutorials/device.md`，接口细节在 `docs/reference/device-protocol.md`。
   - 一个 `Device` 类：`open`、`describe`、`read`；要控制的话加 `submit`、`halt`。
   - 先只做"读"：关节状态、相机。能录下来、能在面板上看了，再做控制。
   - 适配器仓库里放一个协议检查测试（`xpisode_device.check`），写法在同一篇教程的结尾。
3. **装机**：照 `docs/runbooks/add-machine.md` 走，每一步都有"应该看到"，最后 `host/doctor.sh` 全部通过。
4. **加机器人目录**：参考 `robots/yam_3/` 的文件，在 `robots/tron2_1/` 里写自己的（`robots/README.md` 说明每个文件是什么）。
   - 目录名就是站点名，全团队唯一；
   - 序列号、数据目录、后台用哪些 CPU 核，写在这个目录里，不写进共用的脚本。
5. **写本体约定** `profiles/tron2.md`：流名、单位、相机位置名、动作的含义。参考 `profiles/yam-bimanual-3cam.md`。
6. **提 PR**。合进主线以后，机器上只按主线的版本部署，不在机器上改代码（`docs/runbooks/deploy.md`）。
7. **真机验收**：`docs/launch.md` 第 3 节那张表，在操作员在场时做。过了才开始正式采集。

## 4. 共用的规矩（不多，但每条都有代价才定下来）

- **数据和别人的放在一起，不分文件夹。** 每条录制自己记着是哪台、谁、什么任务、什么版本。
- **人名从共享名单里选，任务名先找已有的。** 同一件事别起两个名字。
- **录坏了在源头删**：站点上按"重录"，不上传。已上传的只能由管理员用 `oss.py delete` 删，不要在阿里云控制台删。
- **框架版本全团队一致**，跟着本仓库的 `pyproject.toml` 走；适配器的版本你自己定。
- **机器上不开发。** 代码在自己电脑上改，推到 GitHub，机器上装指定版本。
- **每次操作记到 `robots/tron2_1/LOG.md`**。手册哪一步不对，在同一次提交里把手册改掉。
- 用 Claude 开发的话，它会读本仓库的 `CLAUDE.md`，规矩是一样的。

## 5. 现在还不确定的（接入时一起定）

- Tron2 的 SDK 是什么形式（C++、Python、ROS 2），实时性要求多高。这决定适配器用 Python 写，还是要一个实时核心（YAM 那样）。
- 怎么遥操作 Tron2。框架负责录，遥操作的输入设备也要写成一个适配器。
- 相机用什么、装在哪。

## 6. 负责人要做的（交付前）

- [ ] 在 GitHub 上把同事加为 `xpisode`（Read）和 `xpisode-site`（Write）的协作者；需要的话加 `xpisode-realsense`（Read）。
- [ ] 把同事的 GitHub 用户名填进 `.github/CODEOWNERS` 的 Tron2 几行。
- [ ] 给 Tron2 的机器开一把只能新增的 OSS 子账号钥匙（RAM），交给同事本人。
- [ ] 把同事加进共享人名单。
- [ ] 把这一页的链接发给同事。
