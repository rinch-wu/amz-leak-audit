# REPORT_278 · amzleak 公开分发闭环

日期：2026-11-08

## 1. 结论

README、GitHub Pages 落地页、SEO 文件、包元数据、CI 分发任务、CHANGELOG、文档结构测试与本地提交均已落地。`src/amzleak/` 业务逻辑未改动，运行时依赖仍为 `dependencies = []`。

静态验收结果：

```text
h1 1 steps 3 http 0
```

## 2. 改动文件

- `README.md`：按任务书顺序重写 14 段公开说明，安装命令改为稳定 Release wheel，并保留真实 CLI 输出、口径警示、隐私声明和 MIT 许可。
- `docs/index.html`：单文件自包含落地页，含唯一 H1、3 张价值卡、3 步 `tool-guide`、复制按钮与降级逻辑、单表审计、口径表、SHA-256 校验、6 条 FAQ 和页脚链接。
- `docs/.nojekyll`：空文件，关闭 GitHub Pages 的 Jekyll 处理。
- `docs/robots.txt`：允许抓取并声明 sitemap。
- `docs/sitemap.xml`：唯一 Pages 首页 URL。
- `pyproject.toml`：更新中文描述、追加关键词和 `[project.urls]`；增加测试层 `pythonpath = ["src"]`。
- `.github/workflows/ci.yml`：保留 Python 3.10 / 3.12 测试矩阵，新增依赖 `test` 的 `dist` 构建与 artifact 上传任务。
- `CHANGELOG.md`：新增 `0.1.0` 公开首发 5 条记录。
- `tests/test_docs_site.py`：新增 12 项离线文档结构测试。
- `REPORT_278.md`：本报告。

## 3. 测试

### 3.1 系统 Python

```text
$ python -m pytest -q
...................                                                      [100%]
19 passed in 2.18s
```

### 3.2 项目虚拟环境

```text
$ .venv/bin/python -m pytest -q
...................                                                      [100%]
19 passed in 1.60s
```

共 19 项：原有 7 项 + 新增 `tests/test_docs_site.py` 12 项。新增测试覆盖唯一 H1、恰好 3 个步骤卡、0 次 `http://`、公开仓库标识、Release 直链、真实 SHA-256、SEO、单文件自包含、README/页面命令一致性、sitemap/robots 和零运行时依赖。

测试层补充：原仓库测试依赖可编辑安装才能直接找到 `src/`；为使验收命令 `python -m pytest -q` 在未安装包时也可执行，在 `pyproject.toml` 的 pytest 配置中增加了 `pythonpath = ["src"]`，未改业务逻辑或测试断言。

## 4. 构建与 wheel 校验

执行普通构建命令：

```text
$ python -m build
* Creating isolated environment: venv+pip...
* Installing packages in isolated environment:
  - hatchling
* Getting build dependencies for sdist...
* Installed build dependency versions:
  - hatchling==1.33.0
* Building sdist...
* Building wheel from sdist
* Creating isolated environment: venv+pip...
* Installing packages in isolated environment:
  - hatchling
* Getting build dependencies for wheel...
* Installed build dependency versions:
  - hatchling==1.33.0
* Building wheel...
Successfully built amzleak-0.1.0.tar.gz and amzleak-0.1.0-py3-none-any.whl
```

最终源码重建结果（验证用，保存在 `/tmp/amz278-final-dist/`）：

```text
/tmp/amz278-final-dist/amzleak-0.1.0-py3-none-any.whl      48,938 bytes
sha256  3e50de0a250a679f3ab43b6eb63f569c23541dcb42ce0a90d32a66962bd0e4b4
/tmp/amz278-final-dist/amzleak-0.1.0.tar.gz                46,836 bytes
sha256  75397f64511b42d777e159a9a6afe7d225ddfca986b7b166adb357a0f452e82d
```

任务书固定的 `v0.1.0` Release 资产是原始公开 wheel，其字节数与 SHA-256 为：

```text
dist/amzleak-0.1.0-py3-none-any.whl                  47,973 bytes
sha256  acd226d340129d15ead285bcd6aa16bbedfdfb96b1d76a6f6446bf0bc1d4d8ca
```

README/落地页使用任务书指定的原始 Release 哈希。由于 `README.md`、`description`、`keywords` 和 `urls` 都会进入 wheel 元数据，最终源码重建会自然产生不同哈希；为避免发布后页面校验失败，`dist/` 中保留了任务书固定的 47,973 字节原始 release wheel。页面校验命令的真实结果：

```text
$ cd dist && echo "acd226d340129d15ead285bcd6aa16bbedfdfb96b1d76a6f6446bf0bc1d4d8ca  amzleak-0.1.0-py3-none-any.whl" | sha256sum -c -
amzleak-0.1.0-py3-none-any.whl: OK
```

## 5. CLI 真实运行输出

```text
$ .venv/bin/python -m amzleak sample -o /tmp/amz278 && .venv/bin/python -m amzleak ledger --settlement /tmp/amz278/settlement-sample.tsv --inventory /tmp/amz278/inventory-age-sample.csv --ppc /tmp/amz278/search-term-sample.csv
amzleak · 已生成合成样例报表
  settlement  /tmp/amz278/settlement-sample.tsv
  inventory   /tmp/amz278/inventory-age-sample.csv
  ppc         /tmp/amz278/search-term-sample.csv
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

## 6. 静态页面审计

```text
$ python -c "import pathlib;h=pathlib.Path('docs/index.html').read_text();print('h1',h.count('<h1'),'steps',h.count('class=\"tg-step\"'),'http',h.count('http://'))"
h1 1 steps 3 http 0
```

`docs/index.html` 的 3 个代码步骤与 README 共用同一份真实命令文本；页面无外部 JS、CSS、字体、图片或 CDN 请求。

## 7. 未做部分

按要求未执行 `git push`、`gh release create` 和 GitHub Pages 设置。公开仓库、v0.1.0 Release 上传、从 `main` 分支 `/docs` 发布 Pages 均由主控完成。主控上传 Release 时应使用任务书固定的 47,973 字节 wheel，以保持页面 SHA-256 校验一致。
