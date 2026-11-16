# 相机丢帧检查

会打开相机：只在和操作员约好的时间窗里跑，站点不在运行时跑。

- `rs_bare.py`：不经过 xpisode，直接用 librealsense 读这台机器的 D405，数丢帧。是对照组。序列号、分辨率、帧率、曝光、读哪几路画面都从站点文件里取，和站点打开相机的方式一致。用 `scripts/camera_baseline.sh` 运行它。
- `loss.py EPISODE.mcap`：一条录制里每台相机的帧数、丢帧、gap，以及 `device.diagnostics` 说丢在哪（librealsense 没交出来的 `skipped`、我们的队列 `replaced`、`queue_max`）。

```bash
scripts/camera_baseline.sh 600
.venv/bin/python tools/camera/loss.py <录制>.mcap
```

2026-11-12 在 yam_abc 上的结论：安静的机器上，站点丢帧不多于对照组（10 分钟每台 1 帧，在 librealsense 里）；后台任务跑在相机 USB 中断所在的核上时会丢帧，见 `scripts/station_env.sh`。
