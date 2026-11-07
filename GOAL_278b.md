# GOAL_278b · 用真实重新构建的 wheel 哈希替换文档中的旧哈希（机械替换，勿扩大范围）

## 背景

`python -m build` 在本仓库重新构建出的 wheel 是**确定性可复现**的，当前真实哈希：

- 文件：`dist/amzleak-0.1.0-py3-none-any.whl`（47,976 字节左右，以实际为准）
- sha256：`3e50de0a250a679f3ab43b6eb63f569c23541dcb42ce0a90d32a66962bd0e4b4`

旧哈希 `acd226d340129d15ead285bcd6aa16bbedfdfb96b1d76a6f6446bf0bc1d4d8ca` 是上一版构建产物，已失效，必须在本轮全部替换成上面的新值。

## 禁止事项

1. 禁止使用任何 goal 管理工具（`create_goal` 等）。
2. 禁止修改 `src/amzleak/` 下任何文件。
3. 禁止改 README / docs 的其余文案结构，只做哈希与最小必要的一致性修补。
4. 禁止 git push（主控负责）。

## 执行步骤

1. `cd /root/code/amz-leak-audit && python -m build`（若 `dist/` 已存在则先 `rm -rf dist`），然后 `sha256sum dist/*.whl`，**以你的实测值为唯一真相**；若与上面给出的值不同，说明构建不确定，立刻停止并在报告里写明。
2. 全仓库搜索旧哈希 `acd226d340129d15ead285bcd6aa16bbedfdfb96b1d76a6f6446bf0bc1d4d8ca`，全部替换为新哈希（预期位置：`docs/index.html` 两处、`tests/test_docs_site.py` 一处）。
3. 在 `README.md` 的安装小节里，紧跟 wheel 安装命令后补一行校验命令（与 docs 页保持同一哈希字符串）：
   ```bash
   echo "<新哈希>  amzleak-0.1.0-py3-none-any.whl" | sha256sum -c -
   ```
4. 在 `tests/test_docs_site.py` 增加一项测试：README 里也包含该 sha256 字符串（若已有断言则复用同一常量，避免重复硬编码两份不同值）。
5. 跑 `python -m pytest -q`，必须全绿；并确认 `python -c "import pathlib;h=pathlib.Path('docs/index.html').read_text();print(h.count('acd226d3401'))"` 输出 `0`。
6. `git add -A && git commit -m "docs: pin real rebuilt wheel sha256 (3e50de0a)"`。

## 完成定义

- 全仓库不再出现旧哈希（`grep -rn acd226d3401 . --exclude-dir=.git --exclude-dir=dist` 无输出）。
- `pytest -q` 全绿，并在最终回复里贴出真实测试数字与 `sha256sum dist/*.whl` 原文。
