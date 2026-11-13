# 部署一个钉住的版本；回滚

机器人机器上只部署，不开发：不改 `pyproject.toml`，不跑 `uv lock`、`uv add`、`scripts/dev_link.sh`，不在机器上改代码库。开发在 Mac 上（`README.md` 最后一节）。

## 版本钉在哪里

| 东西 | 钉在 |
| --- | --- |
| 框架 `xpisode`（含 `xpisode-format/device/env`）、`xpisode-realsense`、`xpisode-i2rt` | `pyproject.toml` 的 `[tool.uv.sources]`，每个写死一个完整的提交号 |
| 它们的所有依赖 | `uv.lock`（只解析 Linux x86_64） |
| i2rt 的实时控制器（Rust）、面板前端 | git 依赖装不上，由 `scripts/build_pinned.sh` 在同一个提交上构建 |
| 站点文件、调参、脚本、主机设置 | 本仓库的提交 |

所以"一个版本" = 本仓库 `main` 上的一个提交。录制的头里记着站点文件和 `uv.lock` 的哈希、各仓库的提交，录的是哪个版本查得到。

**为什么要 `build_pinned.sh`：**
- i2rt 的控制器在 Python 包外面：包在自己源码目录的 `target/release/i2rt-rtcore` 找它，从 git 装的包没有这个目录。脚本在钉住的提交上 `cargo build --release --locked`，放到包找的位置（`.venv/lib/python3.12/target/release/`），旁边写 `.rev`。实时优先级来自用户的 limits（xpisode-i2rt 的 `setup_host.sh`），不靠二进制上的 capability，所以重建后不用 `setcap`。
- 框架的面板前端（`src/xpisode/web/static/`）被框架的 git 忽略，从 git 构建的包只有 API、没有网页。脚本在钉住的提交上 `npm run build`，拷进 `.venv`。**这是权宜之计**：更干净的是框架发布带前端的 wheel（框架 `docs/how-to/release.md`），本仓库改钉 wheel。由用户定。

## 升一个版本

谁：在 Mac 上开发的人；推送要先得到用户同意。

```bash
# 改 pyproject.toml 里的 rev（完整提交号），然后：
uv lock
git diff uv.lock                       # 只变了预期的包
git commit -am "Pin xpisode <sha>: <why>"
```

应该看到：`uv lock` 打印 `Resolved N packages`；`git diff` 里只有升了的包和它们带来的依赖。

## 装到一台机器上

谁：操作员，在约好的时间窗里（要先停站点、面板、上传）；不用 sudo，agent 也可以经 ssh 跑。

### 1. 取本仓库的新版本

```bash
cd ~/xpisode-site && git pull --ff-only && git log --oneline -1
```

应该看到：快进到要部署的提交。说 `Not possible to fast-forward`：机器上有本地改动或分叉，停下来问，不要强行覆盖。

### 2. 装钉住的版本

```bash
uv sync --locked
```

应该看到：最后几行是 `Installed ...` 或 `Audited ...`，没有 `error`。说 lock 和 pyproject 对不上：版本没在 Mac 上锁好，回到"升一个版本"。

### 3. 构建控制器和面板

```bash
scripts/build_pinned.sh
```

应该看到：`rtcore: .../target/release/i2rt-rtcore at xpisode-i2rt <sha>` 和 `panel: .../xpisode/web/static at xpisode <sha>`，sha 和 `pyproject.toml` 里的一样。第一次要编译 Rust 和前端，十几分钟。

### 4. 检查

```bash
host/doctor.sh
```

应该看到：`venv`、`rtcore`、`panel` 三项 PASS。然后重启面板和上传（`scripts/serve.sh`、`scripts/upload.sh`）；站点由操作员起（`scripts/run.sh --who <名字>`）。

### 5. 记下来

`robots/<站点>/LOG.md` 追加：日期、谁、`deploy`、本仓库提交号、各代码库的提交号、doctor 结果、和本文不一样的地方。

## 回滚

谁：同上。

```bash
cd ~/xpisode-site
git log --oneline -5                   # 找上一个好的提交
git checkout <提交>                     # 临时回去；要长期回退，在 Mac 上 git revert 再推
uv sync --locked && scripts/build_pinned.sh && host/doctor.sh
```

应该看到：同上第 2–4 步；`venv`、`rtcore`、`panel` 是 PASS，sha 是旧版本的。回到 `main`：`git checkout main` 再走一遍第 2–4 步。数据不受影响：录制、判断、运行都在 `~/xpisode-data`，不在仓库里；各条录制自己记着录它时的版本。

## 机器怎么拿到代码（待定）

- 本仓库还没有 GitHub 远端；建之前（要用户同意），由 Mac 那边用 git bundle 拷过去（`add-machine.md` 第 4 步）。
- 三个代码库是私有的，`pyproject.toml` 用 SSH 地址 `git@github.com:ningma-dev/...`。每台机器要一把能读这三个库的 SSH key（`add-machine.md` 第 3 步）。
