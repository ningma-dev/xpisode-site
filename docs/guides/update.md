# 更新到新版本，或者回退

**一个版本，就是本仓库 `main` 上的一次提交。** 它写定了框架、相机驱动、手臂驱动各用哪个版本，所以每台机器装出来都一样。每条录制也会记下录它时用的是哪个版本。

机器人电脑上**只装版本，不改代码**。代码在自己的电脑上改，改好推到 GitHub。

## 更新一台机器

先停掉站点、面板和上传，然后：

```bash
cd ~/xpisode-site
git pull --ff-only
uv sync --locked
scripts/build_pinned.sh
host/doctor.sh
```

**成功的样子：** 最后一行 `0 FAIL`。然后重新打开面板和上传（`scripts/serve.sh`、`scripts/upload.sh`），站点由操作员来启动。

如果 `git pull` 提示 `Not possible to fast-forward`，说明这台机器上有人改过文件。停下来问负责人，不要强行覆盖。

做完在 `robots/<站点>/LOG.md` 里记一条。

## 回退到上一个版本

```bash
cd ~/xpisode-site
git log --oneline -5          # 找到上一个能用的版本
git checkout <版本号>
uv sync --locked
scripts/build_pinned.sh
host/doctor.sh
```

数据不受影响：录制都在 `~/xpisode-data` 里，不在本仓库里。

要回到最新版本：先 `git checkout main`，再从 `uv sync --locked` 开始做一遍。

如果需要长期回退，在自己的电脑上撤销那次改动，再推送。不要让机器一直停在旧版本上。

## 发布一个新版本（开发的人做）

在自己的电脑上：

1. 改 `pyproject.toml` 里要升级的仓库的提交号；
2. 运行 `uv lock`，然后用 `git diff uv.lock` 看一眼，确认只有预期的包变了；
3. 提交，推送前问负责人。
