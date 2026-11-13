# 给手臂调参

每条跟随臂的摩擦和重力都不一样。调参就是让控制程序先测一遍这条手臂，存成一个文件，以后就用它来控制。**没调过参的跟随臂打不开**，这是有意的。

**什么时候做：** 新手臂；换了手臂；手臂换了位置。示教手柄不用调。

## 安全

调参时手臂会自己动，大约十分钟。开始前确认：

- 有人在手臂旁边，随时能按急停；
- 桌面已经清空，手臂周围没有东西；
- 手臂静止在休息位置；
- 站点没有在运行。

## 步骤

所有命令都在 `~/xpisode-site` 里运行。下面以左跟随臂为例，右臂把 `follower_l` 换成 `follower_r`。

1. 先看看它会怎么动。这一步不会动手臂：

   ```bash
   .venv/bin/python -m xpisode_i2rt.tune can_follower_l --plan
   ```

2. 确认安全后，开始调参：

   ```bash
   .venv/bin/python -m xpisode_i2rt.tune can_follower_l --i-am-at-the-arm \
     --out robots/<站点>/tuned/can_follower_l.json
   ```

3. 两条跟随臂都做完后：

   ```bash
   host/doctor.sh
   ```

   **成功的样子：** `tuned` 一项显示 PASS。

4. 在 `LOG.md` 里记一条，然后提交、推送。

结果里有几个参数要人来拍板，主要是刚度（`kp`），判断依据是遥操作时的手感和声音。具体见 xpisode-i2rt 仓库 README 里的 "Tune an arm" 一节。
