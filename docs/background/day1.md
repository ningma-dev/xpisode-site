# 第一天 · 2026-11-12

采集 3 个任务，每个 50 条；任务 1 传完就训练 ACT 和 π0.5，可比；之后盲评。这一页是当天唯一要看的。

## 0. 昨夜的状态

| 项 | 结果 |
| --- | --- |
| 正式检出 | 工作区、框架、RealSense、i2rt 都快进到 `dev`（已推进，未推远端） |
| 自动测试（正式检出） | 工作区 122、框架 891、前端 67、RealSense 43、i2rt 57，全过 |
| 相机（只开相机，正式代码） | 2 分钟 3 台各 3606 帧，丢 0；连跑 10 分钟每台丢 1 帧（在 librealsense 里，不在我们的代码里） |
| 面板后台任务 | 录制时不跑；录完约 20 s 能播；不占 CPU 4、5、12、13（相机的 USB 中断在这里） |
| 上传 | `scripts/upload.sh` 已在后台跑（nice 10，只用 CPU 0-3、8-11）；面板 `scripts/serve.sh` 也已在跑 |
| 演练（假设备、临时 OSS 前缀） | 计划录制、逐条判断、自动上传、建数据集、服务器拉取、ACT 训练、推回、站点取回、溯源检查、评测台：都通过；临时前缀已删 |
| 旧数据 | 342 条红外已转 FFV1，661 → 469 GB，抽查哈希一致；01:47 开始上传，约 20 MB/s，预计 08:00 左右传完；新录的排前面（每 10 分钟一轮先传新的）。日志 `data/local/logs/upload-20261112.log` |
| 旧方法的 checkpoint | 删了 42 个微调结果，234 GB；清单 `/home/ubuntu/archive/deleted-checkpoints-20261112.tsv`；预训练基模、RLinf、act、`checkpoints/` 没动 |
| 任务和计划 | `sort_legos` 新建；`throw_plastic_bottles_in_bin` 补了指令；3 个 50 行的计划：`day1_1_stack_bowls`（`20261111T163552Z-99f902fabcf9`）、`day1_2_throw_plastic_bottles_in_bin`（`…-21b08b98f2a2`）、`day1_3_sort_legos`（`…-f339d8e50dec`） |

## 1. 早上，开始采集前（你，约 40 分钟，在手臂旁）

```bash
cd ~/xpisode && .venv/bin/xpisode run stations/yam_abc.toml --who <你的名字>
```

面板（`scripts/serve.sh` 已在跑）：http://localhost:8043

| 做什么 | 应该看到 |
| --- | --- |
| 不选名字按录制 | 被拒绝，说明原因 |
| 录一条 30 秒，看站点页刚录完的卡片 | 三台相机帧数正常，没有 `gap` |
| 录制中拔一台相机 USB | 1 秒内中止，写明哪台；插回后要重启站点 |
| 杀一个手臂设备进程（手臂静止时） | 控制组收回，录制中止 |
| 按计划 1 录 3 条：要、重录、不判 | 重录那条被删；只有"要"那行算完成 |
| 判断页打开刚录的一条 | 约 20 s 内能播 |

有一项不过，就退回旧版本（第 6 节），先保证能采集。

## 2. 操作员

1. 顶栏选名字（第一次用：加一个）。
2. 站点页选计划 `day1_1_stack_bowls`，按开始录制；做完按停止。
3. 刚录完的卡片：
   - **要**：做成了；
   - **失败**：认真做了但没做成，也保留；
   - **重录**：录坏了、不算一次尝试，直接删掉，不上传。
4. 判断页可以回看；`K` 要、`F` 失败、`D` 重录、`N` 下一条。
5. 计划满 50 条要的，换下一个计划。
6. 录不了时，站点页和语音会说原因。

## 3. 上传（站点机，整天开着）

已经在后台跑（昨夜 01:47 起）。要重启时：

```bash
cd ~/xpisode && scripts/upload.sh
```

- 判过的录制几秒内开始传；新录的永远排在旧数据前面。
- 面板顶栏的数字是还没传的量。
- 停：`Ctrl-C`。重启接着传，不重复。
- 速度约 20 MB/s；一个任务 50 条约 65 GB，约 1 小时传完。

## 4. 任务 1 完成之后

### 4a. 建数据集（站点机）

计划页上 `day1_1_stack_bowls` 的卡片显示"已上传 n/m"，n = m 就传完了（`GET /api/v1/plans` 里是 `uploaded` 和 `kept`）。然后在卡片上按"建数据集"（只收要的、去掉失败的），或者：

```bash
curl -s -X POST localhost:8043/api/v1/datasets -H 'content-type: application/json' -H 'X-Xpisode-Via: api' \
  -d '{"created_by": "<你的名字>", "name": "stack_bowls_day1", "filters": {"plan": "<计划 id>", "kept": "true", "failed": "false"}, "parts": {"train": 0.9, "val": 0.1}, "seed": 42}'
```

`failed: false` 去掉判为失败的。返回的 `dataset` 就是 `DS`。ACT 和 π0.5 都用它和它的 train/val 部分，所以可比。数据集记录由上传服务自动推上去。

### 4b. 训练（服务器 `maning-dev-1-gpu`）

详细命令见 `docs/methods/training-server.md`。要点：

| 方法 | 在哪 | 时间 |
| --- | --- | --- |
| ACT | 开发机一张空的 H20 | 导出约 30 分钟（估计），训练 10000 步约 15–20 分钟 |
| π0.5 | 你提交到 H200 队列（`xpisode_submit_h200.sh submit`；先 `--dry-run`） | 导出+准备约 30–45 分钟（估计），训练 500 步约 2 小时（上次 1 h 50 min），排队另算 |

- 训练是一个运行，读 `run:<DS>`；推回：`oss.py push --run <TRAIN>`。
- **服务器的 ossutil 现在没有凭据**（见第 7 节）。没配好之前：小文件从挂载手工复制（SERVER.md 第 2 节），推不回去。

### 4c. checkpoint 回到站点（站点机）

```bash
cd ~/xpisode
.venv/bin/python scripts/oss.py pull --run <ACT_TRAIN> --only train/best.pt
.venv/bin/python scripts/oss.py pull --run <PI05_TRAIN> --only xpisode_yam_pi05_sft/checkpoints/global_step_500/actor/model_state_dict/full_weights.pt
.venv/bin/python scripts/lineage.py --check run:<ACT_TRAIN>/train/best.pt
```

- `--only` 只拿那个 checkpoint，不拿优化器状态（π0.5 13 GB，约 15 分钟；不加是 50 GB）。
- 远程推理的方法不用拉，客户端声明它用的 checkpoint 就行。

### 4d. 盲评

站点空的时候（任务 2 和 3 之间，或之后）：计划页新建评测计划，两个候选用中性名字（站点页会显示客户端名），每个 20 次，打开评测台。模板：`docs/methods/eval-plan.template.json`。

## 5. 一天的节奏（估计）

| 时间 | 站点（操作员） | 服务器、传输 |
| --- | --- | --- |
| 9:00–11:00 | 任务 1，50 条 | 边录边传 |
| 11:00 | 任务 2 开始 | 建数据集；ACT 和 π0.5 开训 |
| 约 13:00 | 任务 2 结束；任务 1 盲评（约 1.5 小时） | ACT 早就好了；π0.5 看排队 |
| 下午 | 任务 3 | 任务 2 传完 |
| 任务 3 传完 | — | 三任务数据集，三任务训练 |

站点是瓶颈：采集和评测用同一套手臂，不能同时做。

## 6. 退回旧版本（只在第 1 节有一项不过时）

昨晚打过补丁、能采集的版本：框架 `a9dfcba`、RealSense `3a957e6`、i2rt `5944b0e`；工作区保持现在的（它的站点文件已经让相机用工作区 `.venv`）。退回要重建环境和面板，我来做，或照这个做：

```bash
cd ~/xpisode && git -C repos/xpisode checkout a9dfcba && git -C repos/xpisode-realsense checkout 3a957e6 \
  && git -C repos/xpisode-i2rt checkout 5944b0e && uv sync --locked --reinstall-package xpisode \
  && (cd repos/xpisode/frontend && uv run npm run build)
```

## 7. 没解决的、要你定的

1. **服务器 OSS 凭据**：服务器的 ossutil 指向一个不在的凭据服务。要一把只能上传/下载/列出的 key（RAM 子账号）给服务器；站点机现在的 key 能删东西，也该换成这种。
2. **π0.5 在服务器上没跑过一步**：昨夜空卡被别人占了。提交前先在一张空卡上跑几步，或直接提交并看前几步日志。
3. **π0.5 导出和准备没在服务器上跑过**（那里 av 版本不同）。不行就在站点机导出+准备，`push --run <PREP>`。
4. **USB 中断的核**：现在靠"后台不用 4、5、12、13"。更稳的做法是用 root 把 xHCI 中断固定在这几个核上（`/proc/irq/45`、`53`），否则 irqbalance 可能把它挪走。
5. **`archive/xpisode-data-20261109`（225 GB）**：11-09 修复前的旧格式数据目录。里面 56 条录制（10-26 起）现在的 store 里都没有，是唯一一份，留着；要不要转成现在的格式入库，你定。
6. 旧数据的转换前原文件在 `/home/ubuntu/archive/ir-zstd-20261111`，转换后的推上 OSS 之后再删（你同意过）。
