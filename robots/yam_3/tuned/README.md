# yam_3 的调参和标定

- `can_follower_l.json`、`can_follower_r.json`：两条跟随臂的调参（[给手臂调参](../../../docs/guides/tune-arm.md)）。没有它们，跟随臂打不开。
- `gello_leader_l.json`：左边 GELLO 主臂的标定，`station_gello.toml` 里的 `leader_l` 读它（[装一台 GELLO 主臂](../../../docs/guides/gello.md)）。拆过舵盘、换过舵机后要重新标定。
