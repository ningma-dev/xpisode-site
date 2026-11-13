# 本体约定 `yam-bimanual-3cam/1`（草稿，待用户定）

每台 YAM 站点都声明遵守它（xpisode 的 design/design.md 8.4）。遵守同一约定的站点，数据能放进同一个数据集，checkpoint 能换一台站点跑。下面照 yam_abc 实际录的写（站点文件 2026-11-13，xpisode-i2rt `c458074`、xpisode-realsense `223c00d`）；核心现在还不检查约定，站点文件里只能写成注释。

## 设备名

| 名字 | 是什么 |
| --- | --- |
| `follower_l`、`follower_r` | 左、右跟随臂（YAM，6 关节 + 夹爪），CAN 通道 `can_follower_l/r` |
| `leader_l`、`leader_r` | 左、右示教手柄（YAM 主臂），CAN 通道 `can_leader_l/r` |
| `cam_head` | 头顶相机（D405） |
| `cam_left_wrist`、`cam_right_wrist` | 左、右腕部相机（D405），装在对应跟随臂的夹爪上 |

左右按操作员面对工作台时的左右。

## 流

| 流 | 字段 | 单位 | 标称频率 |
| --- | --- | --- | --- |
| `/follower_{l,r}/state` | `joint_pos` [6]、`joint_vel` [6]、`joint_eff` [6]、`gripper_pos`、`gripper_vel`、`gripper_eff` | rad、rad/s、N·m；`gripper_pos` 是归一化开度，0 闭合、1 张开（1）；`gripper_vel` 1/s；`gripper_eff` N·m | 200 Hz |
| `/leader_{l,r}/input` | `joint_pos` [6]、`trigger`、`buttons` [2]、`handle_age` | rad；`trigger` 0 松开、1 按到底（1）；`buttons` 按下为真；`handle_age` s | 200 Hz |
| `/cam_*/frame` | `color`、`infrared_left`、`infrared_right` | 640×480；彩色 rgb8，红外 y8（录为 FFV1 灰度，无损）；不录深度；曝光固定 32 ms | 30 Hz |

录制必须有上面七条流（站点文件的 `required_streams`）。

## 动作

- 端点 `follower_{l,r}/joint_position`：字段 `joint_pos` [6]（rad）和 `gripper_pos`（0 闭合、1 张开），是关节位置目标，控制器按限位裁剪。
- 遥操作：关节一一对应；夹爪开度 = 1 − `trigger`（`robots/_shared/teleop_*.toml`）。
- 控制节拍 100 Hz（`control_hz`）。

## 按键

| 手柄 | 键 | 做什么 |
| --- | --- | --- |
| 任一 | 0 | 两条臂在遥操作和之前的持有者（策略）之间切换 |
| 左 | 1 | 开始 / 结束录制 |
| 右 | 1 | 两条臂回到 `ready` 姿态 |

## 坐标

外参取自 i2rt v1.3.6 的工作台 CAD `yam_station_linear_4310_d405`：父坐标系 `follower_l/base`；右臂底座在它 y −0.61 m；相机是光学坐标系（+z 朝前）。站点的实际搭建要和这份 CAD 一致，否则要在那台站点的文件里改外参并写明来源。

## 待定

- 版本号怎么升、放在哪里（设计说放生态包）；
- 曝光、分辨率要不要算约定的一部分，还是站点自己的设置；
- 相机位置名是否就用 `cam_head` / `cam_left_wrist` / `cam_right_wrist`。
