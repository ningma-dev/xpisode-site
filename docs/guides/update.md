# 更新和回退

**一个版本就是本仓库 `main` 上的一次提交**，它写定了 xpisode、相机驱动、手臂驱动各用哪个版本。机器人电脑上只装版本，不改代码。

## 更新一台电脑

1. 在各自的终端里按 Ctrl+C，停掉站点、面板和上传。
2. 取新版本：

   ```bash
   cd ~/xpisode-site
   git pull --ff-only
   git diff --stat HEAD@{1} -- host robots/$(cat .station)
   ```

   如果 `git pull` 报 `Not possible to fast-forward`，说明本机有没推送的提交，和 GitHub 上的分叉了。停下来问负责人。

3. 如果上一步列出了文件，说明系统设置或本机配置变了，先运行 `sudo host/setup.sh`。它提示要重启，就重启。
4. 装上新版本：

   ```bash
   uv sync --locked
   scripts/build_pinned.sh
   host/doctor.sh
   ```

**成功的样子：** 最后一行是 `0 FAIL`。然后照[真机验收](acceptance.md#启动)的顺序，重新启动站点、面板和上传。

做完在 `robots/<站点>/LOG.md` 里记一条。

## 回退到上一个版本

```bash
cd ~/xpisode-site
git log --oneline -5          # 找到上一个能用的版本
git checkout <版本号>
uv sync --locked && scripts/build_pinned.sh && host/doctor.sh
```

回退期间不要提交，日志等回到 `main` 后再记。数据不受影响，因为录制在 `~/xpisode-data` 里，不在本仓库里。

回到最新版本：先 `git checkout main`，再运行一遍上面最后一行。

## 发布一个新版本（开发的人做）

在自己的电脑上：

1. 改 `pyproject.toml` 里 `[tool.uv.sources]` 的 `rev`。xpisode 有四行（`xpisode`、`xpisode-format`、`xpisode-device`、`xpisode-env`），要一起改成同一个提交号。
2. 运行 `uv lock`，再用 `git diff uv.lock` 确认只有预期的包变了。
3. 提交，推送前先问负责人。
