# Scanner311 Token golden 约定

阶段 1 实现 `Scanner311` 后，在本目录保存规范化 Token 输出：

```text
00_expressions.tokens
01_functions_classes.tokens
...
```

每条记录至少包含：

```text
physical_offset | logical_index | normalized_kind | argument | target
```

要求：

- 不把 `CACHE` 当作普通语义 Token。
- 必须保留物理 offset，以便核对跳转和行号。
- 输出必须稳定，不包含内存地址或临时绝对路径。
- 每次修改 Scanner311 后由 `pytest/test_scanner311.py` 检查 golden。

阶段 0 只固定格式；具体 Token 文件在阶段 1 Scanner311 可用后生成。
