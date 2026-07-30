# Scanner311 Token golden 约定

阶段 2 实现 3.11 指令规范化后，在本目录保存规范化 Token 输出：

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
- 每次修改 Scanner311 后由 `pytest/test_normalize311.py` 和 corpus
  生成物检查共同验证 golden。

阶段 1 的原始 Scanner 输出直接与
`dis.get_instructions(show_caches=True)` 逐项对照，因此仍保留 `CACHE`，
不产生本目录约定的规范化 golden。阶段 2 已生成并开始跟踪这些文件。
