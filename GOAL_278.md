# GOAL_278 · amzleak 公开分发闭环：GitHub 仓库 / Release 安装包 / GitHub Pages 落地页

## 背景（为什么做这个）

`amzleak` 0.1.0 已完成（本地仓库 `/root/code/amz-leak-audit`，commit `e8edc98`，wheel 47,973 字节）。
唯一缺口不是功能，而是**分发**：仓库此前是私有空壳，README 徽章指向一个不存在的账号 `HongfeiWu001`，安装命令依赖 `git+https`（新手看到就关掉），也没有任何可被搜索引擎抓取的公开落地页。

本轮把它做成**完全自解释、可匿名安装、可被搜索收录**的公开分发资产。硬约束：

- 仓库会公开为 `https://github.com/rinch-wu/amz-leak-audit`（公开，MIT）。
- Release 资产 wheel 的**稳定下载地址**是：
  `https://github.com/rinch-wu/amz-leak-audit/releases/download/v0.1.0/amzleak-0.1.0-py3-none-any.whl`
- GitHub Pages 落地页地址是：`https://rinch-wu.github.io/amz-leak-audit/`（从 `main` 分支 `/docs` 目录发布）。

**不要执行任何 git push / gh release / Pages 设置**——这些由主控完成。你只改文件并本地提交前的验证。

## 禁止事项

1. **禁止使用任何 goal 管理工具**（如 `create_goal` / `update_goal` / token budget 之类），会直接打断任务。
2. 禁止改 `src/amzleak/` 下的业务逻辑（这一轮是分发与文档，不是算法）。若发现测试失败，只允许修复测试层面的问题并在报告里说明。
3. 禁止引入任何第三方运行时依赖（`dependencies` 必须保持 `[]`）。
4. 禁止在文档/页面里出现 `HongfeiWu001`、`amz-leak-audit`（旧名）等错误仓库标识。
5. 禁止使用外部 CDN / 外链 JS / 外链 CSS / 外链字体 / 图片外链；`docs/index.html` 必须**单文件自包含、离线可看**，且页面内 `http://` 出现次数必须为 0（全部用 `https://`）。
6. 禁止伪造数字：所有展示的金额、命令输出必须来自你**真实运行** `amzleak` 的结果（可用 `demo/` 里的合成样例）。

## 交付物（全部必须落地）

### 1. `README.md` 重写（中文为主 + 一段英文摘要）

必须包含，顺序如下：

1. 唯一 H1（保持 `# amzleak · 亚马逊报表本地漏损审计 CLI`）。
2. 一句话价值 + 徽章，**徽章地址全部改成 `rinch-wu/amz-leak-audit`**（CI badge、Python、License）。
3. `## 30 秒讲清`：这是什么、解决什么痛点（钱漏在哪/多大/今天先做哪件事）、和谁不一样（Helium10 等要账号或授权，本工具零账号、零上传）。
4. `## 3 步上手`（**必须恰好 3 步，用有序列表 + 代码块**）：
   - 第 1 步：安装 —— 首选稳定 wheel（Release 直链）：
     ```bash
     pip install "https://github.com/rinch-wu/amz-leak-audit/releases/download/v0.1.0/amzleak-0.1.0-py3-none-any.whl"
     ```
     并给出备选 `pip install "amzleak[xlsx] @ https://github.com/rinch-wu/amz-leak-audit/releases/download/v0.1.0/amzleak-0.1.0-py3-none-any.whl"`（读 xlsx）与开发者安装 `pip install git+https://github.com/rinch-wu/amz-leak-audit.git`。
   - 第 2 步：生成样例并跑通（`amzleak sample -o demo` + `amzleak ledger ...`）。
   - 第 3 步：换成自己的报表（说明从 Seller Central 下载哪三份报表、分别对应哪个子命令）。
5. `## 一份报表也能单独查`：`settlement` / `inventory` / `ppc` 三条命令 + `--json` / `--out`。
6. `## 真实运行输出`：贴你**真实执行**得到的输出（含 `可追回现金 $860.26 / 被压死资金 $10,920.00 / 已流失难追回 $1,503.50` 与 3 件动作），并注明这是合成样例。
7. `## 子命令速查`（表格 5 行：`sample` / `settlement` / `inventory` / `ppc` / `ledger`，列：命令、输入、产出、可否 `--json`）。
8. `## 口径与假设`（保留现有表格与 4 条假设，明确「被压死资金不可与可追回现金相加」）。
9. `## 不想装环境？`：网页版 5 条链接（保留现有），并新增 GitHub Pages 落地页链接 `https://rinch-wu.github.io/amz-leak-audit/`。
10. `## 隐私声明`（保留）。
11. `## 已知限制`（保留）。
12. `## English summary`（5~8 行）。
13. `## License`（保留 MIT）。
14. 末尾一行免责声明：「本工具只做报表口径核算，不构成税务、法律或投资建议；具体费率以亚马逊后台为准。」

### 2. `docs/index.html`（GitHub Pages 落地页，单文件自包含）

要求：

- `lang="zh-CN"`，`<title>` 与 `<meta name="description">` 完整，`<link rel="canonical" href="https://rinch-wu.github.io/amz-leak-audit/">`，OG 四件套（`og:title` / `og:description` / `og:url` / `og:type=website`）。
- **唯一 H1**：`在你自己电脑上，查出亚马逊三份报表里漏掉的钱`。
- 首屏 3 张价值卡（可追回现金 / 被压死资金 / 已流失难追回，并标注口径差异）。
- 常驻模块 `id="tool-guide"`，标题含「💡 功能介绍与极速使用指南」，内含**恰好 3 个 `class="tg-step"`** 的步骤卡（安装 → 样例跑通 → 换成自己的报表），每步带可复制命令（`<pre><code>` + 一个 `class="copy-btn"` 按钮，用原生 `navigator.clipboard`，失败则 `document.execCommand('copy')` 兜底）。
- 「一份报表也能查」三列卡片（结算 / 库存龄期 / 搜索词），每列给出对应子命令与产出文件名。
- 「口径与假设」表格（HTML `<table>`，含被压死资金不可相加的警示行）。
- 「下载安装包」主按钮指向 Release wheel 直链；旁边一行给出 `sha256` 校验命令示例（不要编造哈希值之外的说明，哈希用下面这个真实值）：
  `acd226d340129d15ead285bcd6aa16bbedfdfb96b1d76a6f6446bf0bc1d4d8ca`
- 6 条 FAQ（`<details>` 折叠）：数据会不会上传？/ 要不要注册？/ 支持 xlsx 吗？/ 币种怎么处理？/ 和网页版什么关系？/ 为什么不给我"预计能多赚钱"的数字？
- 页脚：链接到 `https://data-copilot.whfc.cc/leak-ledger` 与 `https://data-copilot.whfc.cc/cli`，并写「本页与 CLI 均为 MIT 开源；报表全程本地解析，不上传」。
- 纯内联 `<style>`，浅色清新风格（白底、绿/深灰主色、卡片圆角 14px、移动端 `@media (max-width: 720px)` 单列），无任何外部资源请求。
- 代码块里真实命令与 README 完全一致（同一份文本）。

### 3. `docs/.nojekyll`（空文件）、`docs/robots.txt`（`User-agent: *` + `Allow: /` + `Sitemap: https://rinch-wu.github.io/amz-leak-audit/sitemap.xml`）、`docs/sitemap.xml`（1 条 URL，即 Pages 首页）。

### 4. `pyproject.toml` 补元数据（不动依赖）

新增：
```toml
[project.urls]
Homepage = "https://rinch-wu.github.io/amz-leak-audit/"
Repository = "https://github.com/rinch-wu/amz-leak-audit"
Issues = "https://github.com/rinch-wu/amz-leak-audit/issues"
Changelog = "https://github.com/rinch-wu/amz-leak-audit/blob/main/CHANGELOG.md"
```
并把 `description` 改成一行更准确的中文短句；`keywords` 追加 `"amazon-fba"`、`"leak-audit"`；`classifiers` 追加 `"Typing :: Typed"` **不要加**（本项目未全面注解），改为保持原样即可。

### 5. `.github/workflows/ci.yml` 增强（不改测试逻辑）

- 保留 matrix `["3.10", "3.12"]` 与 `pytest -q`。
- 追加一个 `dist` job：`needs: test`，`python-version: 3.12`，`pip install build` → `python -m build` → `actions/upload-artifact@v4`（name `dist`，path `dist/*`）。

### 6. `CHANGELOG.md`

`## [0.1.0] - 2026-11-08` 段落，列 5 条：首个公开发布 / 三表合并总账 / 5 个子命令 / 纯标准库零依赖 / MIT 开源 + Pages 落地页。

### 7. 新增测试 `tests/test_docs_site.py`（至少 8 项，纯标准库 + pytest，不联网）

必须覆盖（用 `pathlib` 读仓库文件，路径基于 `Path(__file__).resolve().parents[1]`）：

1. `docs/index.html` 存在且 `<h1` 出现次数恰为 1。
2. `docs/index.html` 内 `class="tg-step"` 出现次数恰为 3，且含 `id="tool-guide"`。
3. `docs/index.html` 内 `http://` 出现次数为 0（防混合内容/外链）。
4. `docs/index.html` 内**不含** `HongfeiWu001`，含 `rinch-wu.github.io/amz-leak-audit`。
5. `docs/index.html` 含 Release wheel 直链与真实 sha256 字符串。
6. `README.md` 不含 `HongfeiWu001`；含 `rinch-wu/amz-leak-audit`；含 `被压死资金` 与 `不可` 的警示语。
7. README 中 `pip install "https://github.com/rinch-wu/amz-leak-audit/releases/download/v0.1.0/amzleak-0.1.0-py3-none-any.whl"` 与 `docs/index.html` 中出现的完全一致（同一字符串出现在两处）。
8. `docs/sitemap.xml` 可被 `xml.etree.ElementTree` 解析且 loc 数 ≥ 1；`docs/robots.txt` 含 `Sitemap:` 行。
9. `pyproject.toml` 含 `Homepage = "https://rinch-wu.github.io/amz-leak-audit/"` 且 `dependencies = []` 仍在。

### 8. `REPORT_278.md`

记录：改动的文件清单、真实执行的命令与真实输出（含 `pytest -q` 结果、`python -m build` 产物字节数）、以及"未做部分"（推送/Release/Pages 由主控执行）。

## 验收（你必须自己跑完并贴真实输出）

```bash
cd /root/code/amz-leak-audit
python -m pytest -q                     # 全部通过（现有 7 项 + 新增 ≥8 项）
.venv/bin/python -m pytest -q           # 若 .venv 可用，同样必须通过
python -m build                         # 产出 dist/amzleak-0.1.0-py3-none-any.whl
python -c "import pathlib;h=pathlib.Path('docs/index.html').read_text();print('h1',h.count('<h1'),'steps',h.count('class=\"tg-step\"'),'http',h.count('http://'))"
```

另外**真实跑一遍** CLI 贴输出：

```bash
.venv/bin/python -m amzleak sample -o /tmp/amz278 && .venv/bin/python -m amzleak ledger --settlement /tmp/amz278/settlement-sample.tsv --inventory /tmp/amz278/inventory-age-sample.csv --ppc /tmp/amz278/search-term-sample.csv
```

## 完成定义

- 上述文件全部存在，`pytest -q` 全绿，`python -m build` 成功，`docs/index.html` 的唯一 H1 / 3 步 / 0 次 `http://` 全部由**真实命令输出**证明。
- 最后执行一次 `git add -A && git commit -m "..."`（本地提交即可，不要 push）。
- 在最终回复里逐条列出：改了哪些文件、测试数字、build 产物路径与字节数、CLI 真实输出原文。
