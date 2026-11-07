# Changelog

## [0.1.0] - 2026-11-08

- 首个公开发布：提供可直接从 GitHub Release 安装的 `amzleak` 0.1.0 wheel。
- 三表合并总账：把结算、库存龄期和搜索词报表合成为一份漏损总账，并排序今天最该做的 3 件事。
- 5 个子命令：`sample`、`settlement`、`inventory`、`ppc`、`ledger`，同时支持人类可读与 `--json` 输出。
- 纯标准库零依赖：运行时 `dependencies = []`，只有读取 `.xlsx` 时才需要可选的 `openpyxl`。
- MIT 开源 + GitHub Pages 落地页：提供单文件离线可看的产品说明、安装指引和口径解释。
