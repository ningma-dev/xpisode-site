# xpisode-site

团队里每台机器人怎么部署、用哪个版本，都写在这里。

- **代码**不在这里，在 GitHub 的 `xpisode`、`xpisode-realsense`、`xpisode-i2rt`；
- **数据**不在这里，在每台机器的 `~/xpisode-data` 和团队共用的 OSS；
- **这里只放**：每台机器人的配置、共用的脚本、操作文档。

```text
robots/<站点>/   一台机器人的一切：配置、序列号、调参、日志
profiles/        每种机器人的本体约定（数据流叫什么、单位）
host/            系统设置和体检脚本
scripts/         启动站点、面板、上传，以及其他工具
docs/            操作文档和背景资料
```

**从 [docs/README.md](docs/README.md) 开始**：按"我要做什么"找到对应的文档。

用 Claude 的话，它会先读 [CLAUDE.md](CLAUDE.md)。
