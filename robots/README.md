# 机器人

**一台机器人一个目录，目录名就是站点名，在整个团队里唯一**（design.md 7.6）。录制里记的就是这个名字，定了就不改；重装、换主机也沿用。所有机器都在 `main` 上，各读各的目录，不按机器开分支。框架把站点文件叫 station，这里就是 `station.toml`。

```text
robots/<站点>/
  station.toml   站点文件：设备、控制组、按键、客户端、外参（字段见框架 docs/reference/station.md）；
                 它的 station = "<站点>" 必须和目录名一样
  machine.env    主机事实：主机名、相机和 CAN 所在的 USB 控制器和核（KEY=value，不写代码）
  can.toml       每条臂的 CAN 适配器序列号：<角色> = "<序列号>"
  tuned/         这台的两条跟随臂的调参
  machine.md     host/snapshot.sh 的输出：装机时的机器事实，重装后拿来对照
  LOG.md         在这台上做过的每件事
robots/_shared/  所有 YAM 站点共用的遥操作映射
robots/_archive/ 退役站点留下的东西，只作来源
```

站点文件的格式不允许额外的键，所以主机事实和 CAN 序列号放在旁边的文件里。

| 站点 | 机器 | 负责人 | 状态 |
| --- | --- | --- | --- |
| `yam_1` | 待定 | ningma | 还没有机器 |
| `yam_2` | `ubuntu2` | ningma | 相机、CAN 已登记；相机位置、CAN 对臂未定；未调参 |
| `yam_3` | `ubuntu3` | ningma | 相机、CAN 已登记；相机位置、CAN 对臂未定；未调参 |
| Tron2（名字由负责人定） | 待定 | 同事 | 由负责人自己建目录，这里不预填 |

## 机器怎么知道自己是哪个站点

`scripts/station_env.sh` 找 `machine.env` 里 `XPISODE_HOST` 等于本机 `hostname -s` 的那个目录；临时要换，设 `XPISODE_STATION=<站点>`。`scripts/run.sh`、`serve.sh`、`upload.sh`、`host/setup.sh`、`host/doctor.sh` 都经过它。

## CAN 通道名

通道名就是臂的角色：`can_follower_l`、`can_follower_r`、`can_leader_l`、`can_leader_r`，每台 YAM 机器都一样，所以站点文件里的设备和 `exclusive` 键各台一样，只有 `can.toml` 里的序列号不同。udev 规则由 `host/can_rules.py` 从 `can.toml` 生成（[map-can.md](../docs/runbooks/map-can.md)）。

## 标着 FILL 的

是要在那台机器上确认的事实（相机序列号对应哪个位置、CAN 序列号对应哪条臂）。留着 FILL：相机打不开、CAN 规则不生成，站点就录不了，不会录错。确认方法见 [add-machine.md](../docs/runbooks/add-machine.md) 第 7、8 步。

## 调参

每条跟随臂的摩擦和重力各不相同，调参只属于那一条臂：`robots/<站点>/tuned/can_follower_l.json`、`can_follower_r.json`（站点文件里 `"tuned": "tuned/can_follower_l.json"`，路径相对站点文件）。没测之前跟随臂打不开，这是有意的。`robots/_archive/yam_abc/tuned/` 是旧臂的，只作参考。

测一条臂：操作员在场、桌面清空、手臂静止、站点停着，会让手臂动约十分钟，只在约好的时间窗里做：

```bash
.venv/bin/python -m xpisode_i2rt.tune can_follower_l --plan          # 只说会怎么动，不动
.venv/bin/python -m xpisode_i2rt.tune can_follower_l --i-am-at-the-arm --out robots/yam_3/tuned/can_follower_l.json
```

需要先有实时优先级和钉住版本的控制器（`host/doctor.sh` 的 `rtprio`、`rtcore` 是 PASS）；没有实时优先级，调参程序会拒绝。`kp` 的选择仍是人的事（xpisode-i2rt README "Tune an arm"）。臂换了位置或换了臂就重测，旧文件删掉，不改名沿用。

## 本体约定

YAM 站点都遵守 `profiles/yam-bimanual-3cam.md`（草稿）：流名、单位、相机位置名一样，数据才能放进同一个数据集。站点文件现在没有声明约定的键，写在文件头的注释里。
