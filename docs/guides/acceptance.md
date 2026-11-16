# 真机验收

开始正式采集前，确认这台机器人在真机上工作正常。**有一项不通过，就不开始采集。**

**前提：** [准备一台机器人电脑](setup-robot-pc.md)已经做完，两条跟随臂已经[调过参](tune-arm.md)。

**要求：** 操作员在场，桌面清空。手臂会动。

## 启动

站点和面板都在前台运行，各开一个终端（或者用 `tmux`），按 Ctrl+C 停止。

终端 1，启动站点：

```bash
cd ~/xpisode-site && scripts/run.sh --who <你的名字>
```

站点起来以后，在终端 2 启动面板：

```bash
cd ~/xpisode-site && scripts/serve.sh
```

面板要在站点之后启动，否则它不知道有这个站点。从自己的电脑上看面板：先运行 `ssh -L 8043:localhost:8043 <电脑>`，再在浏览器打开 http://localhost:8043。

## 逐项确认

| 做什么 | 应该看到 |
| --- | --- |
| 三台相机连续跑 10 分钟 | 面板的设备诊断里重置次数为 0，几乎没有丢帧 |
| 录制中拔掉一台相机 | 1 秒内停止录制，原因里写明是哪台相机 |
| 录制时，后台在生成代理视频（面板回看用的小视频） | 控制节拍的抖动和不生成时一样 |
| 杀掉一条手臂的设备进程：用 `pgrep -af 'name follower_l'` 找到它的进程号，再 `kill <进程号>` | 控制权被收回，录制停止 |
| 手臂静止时让站点崩溃：用 `pgrep -af 'xpisode run'` 找到站点的进程号，再 `kill -9 <进程号>` | 手臂停稳；重新启动站点后，没录完的那条标为"中断" |
| 录一条，在面板上判为"要"，然后打开上传（见下） | 10 分钟内，`scripts/oss.sh status` 显示它已上传 |

每一项的结果都记进 `robots/<站点>/LOG.md`。

## 打开同步

同步做两件事：把判过的录制传到 OSS；每分钟从 OSS 拉一次团队的任务、计划、人名和判断（在服务器或飞书上建的计划，一两分钟后出现在这台的面板上）。别的机器人的录制不会拉下来。

把它装成这台电脑的常驻服务，开机就运行，出错自动重启：

```bash
cd ~/xpisode-site
mkdir -p ~/.config/systemd/user && cp host/xpisode-sync.service ~/.config/systemd/user/
systemctl --user daemon-reload && systemctl --user enable --now xpisode-sync
sudo loginctl enable-linger "$USER"   # 只做一次：没人登录时也运行
```

看它在做什么：`journalctl --user -u xpisode-sync -f`；还有什么没传：`scripts/oss.sh status`。
