# amzleak · 亚马逊报表本地漏损审计 CLI

把结算、库存龄期、搜索词三份亚马逊报表在本地合成一张总账：钱漏在哪、损失多大、今天先做哪 3 件事，全程零账号、零上传。

[![CI](https://img.shields.io/github/actions/workflow/status/rinch-wu/amz-leak-audit/ci.yml?label=CI)](https://github.com/rinch-wu/amz-leak-audit/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.10%20%7C%203.12-blue.svg)](https://github.com/rinch-wu/amz-leak-audit)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](https://github.com/rinch-wu/amz-leak-audit/blob/main/LICENSE)

## 30 秒讲清

卖家手里通常有三份亚马逊报表，但每一份都是一堆看不懂的扣费行：结算报表里有重复扣费、异常佣金和退款；库存报表里有越来越贵的超龄库存；广告报表里有持续空烧的搜索词。真正的问题是：**钱漏在哪、损失有多大、今天先做哪件事？**

`amzleak` 在本地解析这三份报表，产出一张总账、今天最该做的 3 件事，以及可直接回传后台的动作文件。与 Helium10 等需要账号或授权的工具不同，本工具**零账号、零上传、零第三方运行时依赖**；只有读取 `.xlsx` 时才需要可选的 `openpyxl`。

## 3 步上手

1. **安装**：首选稳定 wheel（Release 直链）。

   ```bash
   pip install "https://github.com/rinch-wu/amz-leak-audit/releases/download/v0.1.0/amzleak-0.1.0-py3-none-any.whl"
   ```

   需要直接读取 `.xlsx` 时安装扩展：

   ```bash
   pip install "amzleak[xlsx] @ https://github.com/rinch-wu/amz-leak-audit/releases/download/v0.1.0/amzleak-0.1.0-py3-none-any.whl"
   ```

   想跟进 `main` 分支的开发版：

   ```bash
   pip install git+https://github.com/rinch-wu/amz-leak-audit.git
   ```

2. **生成样例并跑通**：

   ```bash
   amzleak sample -o demo
   amzleak ledger --settlement demo/settlement-sample.tsv --inventory demo/inventory-age-sample.csv --ppc demo/search-term-sample.csv
   ```

3. **换成自己的报表**：从 Seller Central 下载近期结算报表（对应 `--settlement`）、库存龄期报表（对应 `--inventory`）和搜索词报表（对应 `--ppc`），再把路径替换成自己的文件。

   ```bash
   amzleak ledger --settlement "path/to/settlement.tsv" --inventory "path/to/inventory-age.csv" --ppc "path/to/search-term.csv"
   ```

## 一份报表也能单独查

- 结算报表：

  ```bash
  amzleak settlement demo/settlement-sample.tsv --json
  amzleak settlement demo/settlement-sample.tsv --out settlement-actions.csv
  ```

- 库存龄期报表：

  ```bash
  amzleak inventory demo/inventory-age-sample.csv --json
  amzleak inventory demo/inventory-age-sample.csv --out inventory-actions.csv
  ```

- 搜索词报表：

  ```bash
  amzleak ppc demo/search-term-sample.csv --json
  amzleak ppc demo/search-term-sample.csv --out negatives.tsv
  ```

所有子命令都支持 `--json`；`settlement`、`inventory`、`ppc` 还支持 `--out` 写出可继续处理的 CSV / TSV 文件。

## 真实运行输出

以下输出来自本机真实执行 `amzleak sample -o demo` 与三表 `ledger` 命令，使用的是仓库内合成样例，不代表任何真实店铺表现。

```text
amzleak · 已生成合成样例报表
  settlement  demo/settlement-sample.tsv
  inventory   demo/inventory-age-sample.csv
  ppc         demo/search-term-sample.csv
amzleak · 亚马逊报表本地漏损审计（数据不出本机）

可追回现金   $860.26        ← 本周期白名单异常 + 广告保守可回收
被压死资金   $10,920.00     ← 存量，不可与上一行相加
已流失难追回 $1,503.50

今天最该做的 3 件事
 1. [高] 疑似重复扣费 $120.00 —— 按异常明细回 Seller Central 对账，确认后向平台开 case 或修正定价。
 2. [高] 零转化搜索词空烧 $104.31 —— 下载否定关键词清单，先把高花费零转化词加 Negative Exact。
 3. [中] SKU 佣金率偏高 $435.00 —— 按异常明细回 Seller Central 对账，确认后向平台开 case 或修正定价。

📄 完整三份报表合并视图（浏览器里看，免注册）：https://data-copilot.whfc.cc/leak-ledger
```

## 子命令速查

| 命令 | 输入 | 产出 | 可否 `--json` |
|---|---|---|---|
| `amzleak sample -o demo` | 无，内置合成报表 | `settlement-sample.tsv`、`inventory-age-sample.csv`、`search-term-sample.csv` | 是 |
| `amzleak settlement FILE` | 亚马逊结算报表 CSV / TSV / XLSX | 净利率、评级、三类最大漏损；可选动作 CSV | 是 |
| `amzleak inventory FILE` | 库存龄期报表 CSV / TSV / XLSX | 压死资金、仓储费敞口、清货/移除建议；可选动作 CSV | 是 |
| `amzleak ppc FILE` | 搜索词报表 CSV / TSV / XLSX | 零转化空耗、倒挂亏损、保守可回收；可选否定词 TSV | 是 |
| `amzleak ledger` | 上述三类报表中的任意一份或多份 | 统一漏损总账、覆盖提示、今天最该做的 3 件事 | 是 |

## 口径与假设

| 指标 | 含义 | 使用方式 |
|---|---|---|
| 可追回现金 | 本周期可追回现金，主要由结算白名单异常金额与广告保守可回收金额组成 | 流量口径，可作为本期可行动金额 |
| 被压死资金 | 超过 90 天的库存按单件成本计算出的资金占用 | **存量口径，不可与可追回现金相加** |
| 已流失难追回 | 本周期退款金额与广告倒挂亏损等已发生损失 | 不代表还能追回 |

其他固定假设：

- 广告保守可回收：零转化空耗保留 10% 品牌防守词，按 90% 估算可追回；ACOS 倒挂部分按降价后 70% 可追回。
- 库存单价与预期售价是**由你自己传入的假设**；默认 `--unit-cost 8`、`--price 29.9`，不代表真实成本。
- 不同币种不做汇率换算；只有同币种金额会并入总账，混合币种会在结果中明确提示。
- 库存 90 天以上为压死资金；365+ 库存会给出售卖与移除建议。

> **口径警示：被压死资金是存量，不可与可追回现金相加，也不代表本期能够立即变现。**

## 不想装环境？

直接打开免注册的托管版网页：

- GitHub Pages 落地页：<https://rinch-wu.github.io/amz-leak-audit/>
- 三份报表合并总账：<https://data-copilot.whfc.cc/leak-ledger>
- 结算利润审计：<https://data-copilot.whfc.cc/tools/settlement-profit-audit>
- 库存龄期审计：<https://data-copilot.whfc.cc/tools/inventory-age-audit>
- 搜索词空耗审计：<https://data-copilot.whfc.cc/tools/ppc-search-term-audit>
- 使用指南：<https://data-copilot.whfc.cc/guide>

CLI 适合不想上传报表、需要脚本化或批量检查的场景；网页版适合免安装快速查看。

## 隐私声明

`amzleak` 全程在本机解析报表：不上传、不联网、不埋点、不读取环境变量中的业务数据。除显式传入 `--out` 或执行 `sample -o DIR` 写出的文件外，不会创建或修改任何文件。

## 已知限制

- `.xlsx` 读取需要安装可选依赖：`pip install "amzleak[xlsx]"`。
- 内置样例是合成数据，只用于验证命令和口径，不代表任何真实店铺表现。
- 仅支持亚马逊后台常见报表表头；被二次编辑破坏表头的文件可能解析失败。
- 当前只处理 CSV / TSV / TXT / XLSX，不处理 PDF、图片或扫描件。
- 不提供汇率换算，也不把不同币种强行相加。

## English summary

- `amzleak` is a local-first Python CLI for auditing Amazon settlement, inventory-age, and search-term reports.
- It combines the three reports into one leak ledger, ranks the next three actions, and writes import-ready action files.
- Reports stay on your machine: no account, no upload, no telemetry, and no network calls.
- The runtime has zero third-party dependencies; `openpyxl` is optional only for reading `.xlsx` files.
- Install the stable `v0.1.0` wheel directly from GitHub Releases, or use the GitHub Pages guide for a quick start.
- It reports traceable cash recovery, locked inventory capital, and already-lost cash separately instead of inventing a single "extra profit" number.
- The project is MIT-licensed and welcomes issues and reproducible report-format examples.

## License

MIT License. See [LICENSE](LICENSE).

Copyright (c) 2026 amzleak contributors

本工具只做报表口径核算，不构成税务、法律或投资建议；具体费率以亚马逊后台为准。
