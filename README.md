# amzleak · 亚马逊报表本地漏损审计 CLI

把亚马逊后台导出的结算、库存龄期、搜索词三份报表，在**你自己的电脑上**合成一张漏钱总账：钱漏在哪、今天先做哪 3 件事、哪些动作可以直接回传后台。

[![CI](https://img.shields.io/github/actions/workflow/status/HongfeiWu001/amz-leak-audit/ci.yml?label=CI)](https://github.com/HongfeiWu001/amz-leak-audit/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.10%20%7C%203.12-blue.svg)](https://github.com/HongfeiWu001/amz-leak-audit)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

English summary: `amzleak` is a zero-runtime-dependency Python CLI that audits Amazon settlement, inventory-age, and search-term reports entirely on the local machine. It produces a combined leak ledger, a ranked three-action list, and import-ready action files without network access or telemetry.

## 30 秒讲清

卖家手里通常有三份亚马逊报表，但每一份都是一堆看不懂的扣费行：结算报表里有重复扣费、异常佣金和退款；库存报表里有越来越贵的超龄库存；广告报表里有持续空烧的搜索词。真正的问题是：**钱到底在哪、损失有多大、今天先做哪件事？**

`amzleak` 在本地解析这三份报表，产出一张总账、今天最该做的 3 件事，以及可直接回传后台的动作文件：

- 结算漏损 → 失血明细动作 CSV
- 超龄库存 → 清货/移除动作 CSV
- 搜索词空耗 → Negative Exact 批量否定词 TSV

全过程不联网、不上传、不埋点。运行时零第三方依赖；只有读取 `.xlsx` 时才需要可选的 `openpyxl`。

## 3 步快速开始

```bash
pip install git+https://github.com/HongfeiWu001/amz-leak-audit.git
amzleak sample -o demo && ls demo
amzleak ledger --settlement demo/settlement-sample.tsv --inventory demo/inventory-age-sample.csv --ppc demo/search-term-sample.csv
```

本机真实运行输出：

```text
amzleak · 已生成合成样例报表
  settlement  demo/settlement-sample.tsv
  inventory   demo/inventory-age-sample.csv
  ppc         demo/search-term-sample.csv
inventory-age-sample.csv
search-term-sample.csv
settlement-sample.tsv

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

只看一份报表也可以：

```bash
amzleak settlement demo/settlement-sample.tsv --json
amzleak inventory demo/inventory-age-sample.csv --out clear.csv
amzleak ppc demo/search-term-sample.csv --out negatives.tsv
```

需要读 `.xlsx` 时：

```bash
pip install "amzleak[xlsx]"
```

## 不想装环境？

直接打开免注册的托管版网页：

- 三份报表合并总账：<https://data-copilot.whfc.cc/leak-ledger>
- 结算利润审计：<https://data-copilot.whfc.cc/tools/settlement-profit-audit>
- 库存龄期审计：<https://data-copilot.whfc.cc/tools/inventory-age-audit>
- 搜索词空耗审计：<https://data-copilot.whfc.cc/tools/ppc-search-term-audit>
- 使用指南：<https://data-copilot.whfc.cc/guide>

CLI 适合不想上传报表、需要脚本化或批量检查的场景；网页版适合免安装快速查看。

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

## 隐私声明

`amzleak` 全程在本机解析报表：不上传、不联网、不埋点、不读取环境变量中的业务数据。除显式传入 `--out` 或执行 `sample -o DIR` 写出的文件外，不会创建或修改任何文件。

## 已知限制

- `.xlsx` 读取需要安装可选依赖：`pip install "amzleak[xlsx]"`。
- 内置样例是合成数据，只用于验证命令和口径，不代表任何真实店铺表现。
- 仅支持亚马逊后台常见报表表头；被二次编辑破坏表头的文件可能解析失败。
- 当前只处理 CSV / TSV / TXT / XLSX，不处理 PDF、图片或扫描件。
- 不提供汇率换算，也不把不同币种强行相加。

## 目录结构

```text
amz-leak-audit/
├── src/amzleak/
│   ├── __init__.py
│   ├── __main__.py
│   ├── cli.py
│   ├── search_terms.py
│   ├── settlement.py
│   ├── inventory.py
│   └── ledger.py
├── tests/
│   ├── test_cli.py
│   └── test_ledger.py
├── .github/workflows/ci.yml
├── pyproject.toml
├── LICENSE
└── README.md
```

## License

MIT License. See [LICENSE](LICENSE).

Copyright (c) 2026 amzleak contributors
