# 训练服务器：从“任务 1 数据集在 OSS 上”到“两个可比训练在跑”

2026-11-12 写。服务器 `ssh maning-dev-1-gpu`（root，共享开发容器，4× H20 96 GB）。

## 先看：还没通的一件事

**服务器的 ossutil 没有可用凭据。** `~/.ossutilconfig` 是 `mode=uri`，凭据地址 `localhost:7002` 上没有服务，`oss.py status/pull/push` 一调 ossutil 就报 `connection refused`。要有人给 ossutil 配凭据（自己的 AK 或 STS；不要复制别人的配置）。配好后先：

```bash
/root/xpisode-dev/.venv/bin/python /root/xpisode-dev/scripts/oss.py --root /mnt/data/LONG/ningma/xpisode-train/data status
```

- 看：最后几行是 OSS 上的统计，不是 `ossutil ls -r failed`。
- 凭据要放在别的文件里时：写一个包装脚本 `exec /usr/local/bin/ossutil -c <配置文件> "$@"`，然后 `export OSSUTIL=<包装脚本>`（oss.py 读 `OSSUTIL`）。
- 没配好之前：大文件可以从挂载读（`--link`），小文件（记录、sidecar）只能从挂载手工复制（见 2）；**push 做不了**，checkpoint 回不了站。

## 0. 位置

| 东西 | 位置 |
| --- | --- |
| 工作区（oss.py） | `/root/xpisode-dev`（dev 535f7b9，框架 `repos/xpisode` 7f0bc9d），`.venv` 只有 xpisode-format/device/env + zstandard（Python 3.12） |
| ACT | `/root/xpisode-methods/act`（tonight a40abe1），`.venv`：torch 2.11.0+cu130 |
| RLinf（开发机用） | `/root/xpisode-methods/RLinf`（tonight bc758fba） |
| H200 作业用的一切 | `/mnt/data/LONG/ningma/xpisode-train/`（CPFS；作业只挂 `/mnt/data`）：`RLinf/`、`act/`、`xpisode-dev/`、`envs/rlinf-tonight`、`assets/openpi-cache`、`data/`（数据根）、`jobs/` |
| π0.5 环境 | `envs/rlinf-tonight`：上次 insert_smt 训练用的 `xpisode-finetune-20261027-stack-bowls/envs/rlinf`（`install.sh embodied`，torch 2.7.1，ray 2.59）通过 `.pth` 复用，另装 xpisode-format/device/env（可编辑）、mcap、qoi、iceoryx2 |
| 基模 | `/mnt/data/Models/openpi/pi05_base_torch_robotwin` |
| OSS 挂载 | `/mnt/data_oss/xpisode_data`（只读对待；实际可写，不要写） |

```bash
W=/root/xpisode-dev
O="$W/.venv/bin/python $W/scripts/oss.py"
B=/mnt/data/LONG/ningma/xpisode-train
L=$B/data                      # 两个方法用同一个数据根
M=/mnt/data_oss/xpisode_data
DS=<任务 1 的数据集运行>
WHO=<你的名字>
```

代码更新（站点机上 `git bundle` + scp，服务器上 `git pull --ff-only <bundle> <分支>`，再 `git -C $B/RLinf pull --ff-only /root/xpisode-methods/RLinf tonight`）。`xpisode_submit_h200.sh` 要求 `$B/RLinf` 没有未提交改动。

**选卡：** `nvidia-smi`，只用 0 MiB 的卡。GPU 0、3 是别人的；别人的进程在容器里看不到（`No running processes found` 也可能有人在用）。

## 1. 拉数据集（链接到挂载，不复制录制）

```bash
$O --root $L pull --dataset $DS --link --mount $M
```

- 看：`linked or copied N files for run $DS`；`find $L/store/episodes -type l | wc -l` = Episode 数。
- 量（演练前缀，9 条小 Episode）：几秒。

## 2. 没有凭据时的替代（只读挂载）

```bash
cd $M/store && find . -type f ! -name '*.mcap' ! -name '*.hdf5' ! -path './index/*' -print0 \
  | xargs -0 -I{} cp -n --parents {} $L/store/
$O --root $L pull --dataset $DS --link --mount $M      # 小文件都在了，就不再调 ossutil
```

演练前缀上这样试过（`pull --dataset` 和 `pull --run` 都只建链接）。

## 3. ACT（开发机，一张 H20，约 15–25 min）

```bash
cd /root/xpisode-methods/act
.venv/bin/python xpisode_export.py run --root $L --dataset $DS --who $WHO      # CPU；用数据集自己的 train/val 部分
CUDA_VISIBLE_DEVICES=<空卡> WANDB_MODE=disabled .venv/bin/python xpisode_train.py --root $L --who $WHO \
  --config xpisode_adapter/configs/act.json --data <上一步 output>
$O --root $L push --run <ACT_TRAIN>
```

- 量（服务器 H20，300 步 smoke，batch 16）：0.084 s/步，显存峰值 2.6 GiB（进程 3.3 GB）。10000 步约 14 min，加 10 次验证约 20 min。
- 导出在服务器上没用真录制试过（演练前缀的 Episode 是合成的，没有 `follower_l/state`）；站点上 8 条 144 s。
- 运行目录约 1.3 GB（`best.pt` 0.34 GB，`latest.pt` 1.0 GB）。
- 看：`{"event": "recorded", "run": "<ACT_TRAIN>"}`；运行读 `run:<导出>/export/manifest.json`，导出读 `run:$DS`。

## 4. π0.5（H200 作业，约 2–2.5 h）

### 4a. 导出 + 准备（服务器 CPU）

```bash
cd $B/RLinf
PY=$B/envs/rlinf-tonight/bin/python
export PYTHONPATH=$B/RLinf
$PY toolkits/lerobot/xpisode_lerobot.py run --root $L --dataset $DS --who $WHO --part train &
$PY toolkits/lerobot/xpisode_lerobot.py run --root $L --dataset $DS --who $WHO --part val; wait
# 两行最后各一个 {"done": true, "run": ...}：<TRAIN_EXPORT>、<VAL_EXPORT>
ls $L/store/runs/*/*/<TRAIN_EXPORT>/export/lerobot/        # <repo id>，如 xpisode/<任务名>
$PY toolkits/lerobot/xpisode_prepare_pi05.py --root $L --who $WHO \
  --source run:<TRAIN_EXPORT>/export/lerobot/<repo id> --val-source run:<VAL_EXPORT>/export/lerobot/<repo id>
```

- 和 ACT 一样用数据集的 train/val 部分 → 可比。
- 没在服务器上跑过：环境里是 av 18.1（锁文件是 16.1），解 FFV1 录制可能有差别。不行就在站点导出 + 准备，`push --run <PREP>`，服务器 `pull --run <PREP> --link --mount $M`。
- 时间：站点上约 50 s 一段；50 条每部分并行约 30–45 min（估计）。

### 4b. 提交

```bash
$B/RLinf/xpisode_submit_h200.sh submit --prep <PREP> --who $WHO --dry-run    # 先看请求和 job.sh
$B/RLinf/xpisode_submit_h200.sh submit --prep <PREP> --who $WHO              # 500 步，batch 128，warm-up 25
$B/RLinf/xpisode_submit_h200.sh status <NAME>
$B/RLinf/xpisode_submit_h200.sh finish <NAME>      # 等作业结束，从 train.log 取 run id，oss.py push --run
```

- 机制同上次（`xpisode-finetune-20261027-stack-bowls/configs/rlinf-pi05-smt/pai_h200.py`）：`aliyun pai-dlc CreateJob`（CLI 在 `.../runtime/aliyun-cli/aliyun`，用 `~/.aliyun` 当前账号；`PAI_PROFILE` 换账号），workspace 1451179，配额 `quota1llb2161njn`（叫 H200，实际是 8× NVIDIA L20X 143 GB），镜像 `pai_xr_alg:kesen-worldarena2-evaluation`，CPFS 挂 `/mnt/data/`，1 节点 8 卡 128 CPU 1024 GiB，不重启。
- 作业看不到 `/mnt/data_oss`：提交前把准备运行里指向挂载的链接换成文件。作业也没有 OSS 凭据：运行写在 `$L`（CPFS），`finish` 在开发机上推。
- 只在最后一步存 checkpoint（`save_interval=steps`）：一个 checkpoint 约 50 GB（含优化器），`full_weights.pt` 13.4 GB。
- 作业文件：`$B/jobs/<NAME>/{request.json,job.sh,submit.*,job_id,gpus.txt,train.log,exit.txt,run}`。
- 量（上次同配方，2026-11-10，8× L20X）：00:48 提交，00:50 开训，500 步 1 h 50 min（训练 12.3 s/步，存盘步约 45 s）。另有人在同一配额上跑 3 节点任务，排队可能更久。
- 本次 dry-run 过；**没提交过**。

### 开发机上跑 RLinf（只做试验）

Ray 2.59 不看 `CUDA_VISIBLE_DEVICES`，数出 4 张卡，`actor: all` 会把 worker 放到别人的卡上。一定要钉住：

```bash
CUDA_VISIBLE_DEVICES=<空卡> ... xpisode_train_vla_sft.py ... cluster.component_placement.actor=<空卡>
```

bs128 在一张 H20 上是 128 次累积：按 L20X 每卡每微批约 0.8 s 估，约 100–150 s/步，500 步约 14–20 h；正式训练用 H200。

## 5. 回站（站点机）

```bash
OSS="/home/ubuntu/xpisode-dev/.venv/bin/python /home/ubuntu/xpisode-dev/scripts/oss.py --root <站点数据根>"
$OSS pull --run <ACT_TRAIN>     # 约 1.3 GB，约 2 min
$OSS pull --run <PI05_TRAIN>    # 运行的全部文件，约 50 GB（含优化器），15 MB/s 约 1 h
```

- checkpoint：`run:<ACT_TRAIN>/train/best.pt`；`run:<PI05_TRAIN>/xpisode_yam_pi05_sft/checkpoints/global_step_500/actor/model_state_dict/full_weights.pt`。
- 之后同 TONIGHT.md 第 3、6 步（lineage、回放检查）。

## 6. 之后的 3 任务微调

同一流程，数据集换成 3 任务的数据集运行；π0.5 `--steps` 按训练帧数调（上次 128 × 500 = 64000 个窗口 ≈ 1.1 遍 58486 帧）。

## 验证过的、没验证的

| 项 | 结果 |
| --- | --- |
| `oss.py status`（服务器 ossutil 2.2.0） | 失败：凭据服务 localhost:7002 不在 |
| `pull --dataset/--run --link --mount`（演练前缀） | 小文件手工复制后通过，只建链接 |
| ACT 训练（服务器 H20） | 300 步通过，0.084 s/步，2.6 GiB；记录为运行 |
| ACT 导出（服务器） | 没过：演练 Episode 是合成的 |
| `push --run` 到临时前缀 | 没做（无凭据）；临时前缀没建 |
| π0.5 一张 H20 smoke | 没测出：worker 起来、钉在 GPU 2 上；16:03 UTC 起 GPU 1、2 被别的容器占用（先 327 MiB，后各 7.5 GB），按规则停了。目前开发机没有空卡 |
| `xpisode_submit_h200.sh` | dry-run 过；没提交 |
| π0.5 导出/准备在服务器 | 没跑 |
