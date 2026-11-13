# 给手臂调参

每条跟随臂的摩擦和重力都不一样。调参就是让控制程序把这条手臂测一遍，结果存成一个文件，以后控制它时就读这个文件。**没调过参的跟随臂打不开。**

**什么时候做：** 新手臂、换了手臂，或者手臂换了位置。示教手柄不用调。

**前提：** [准备一台机器人电脑](setup-robot-pc.md)已经做完，`host/doctor.sh` 里 `can-links` 是 PASS。

## 安全

调参时手臂会自己动，大约十分钟。开始前确认：

- 有人守在手臂旁边，随时能按急停；
- 桌面已清空；
- 手臂静止在休息位置；
- 站点没有在运行。

## 步骤

命令都在 `~/xpisode-site` 里运行。下面以左跟随臂为例；右臂把 `follower_l` 换成 `follower_r`。

1. 先看它打算怎么动。这一步手臂不动：

   ```bash
   .venv/bin/python -m xpisode_i2rt.tune can_follower_l --plan
   ```

   **成功的样子：** 打印出要走的动作，手臂不动。

2. 开始调参：

   ```bash
   .venv/bin/python -m xpisode_i2rt.tune can_follower_l --i-am-at-the-arm \
     --out robots/<站点>/tuned/can_follower_l.json
   ```

   **成功的样子：** 生成了 `robots/<站点>/tuned/can_follower_l.json`。

3. 两条跟随臂都调完后，运行 `host/doctor.sh`。

   **成功的样子：** `tuned` 是 PASS。

4. 在 `LOG.md` 里记一条，然后提交、推送。

调参测不出接触和声音，刚度怎么选还要靠人判断。见 xpisode-i2rt 仓库 README 的 "Tune an arm" 一节。
