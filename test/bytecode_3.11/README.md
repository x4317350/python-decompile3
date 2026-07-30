# CPython 3.11 测试语料

主语料位于 `test/simple_source/311/`，逐 opcode 最小语料位于
`test/bytecode_3.11/opcode_fixtures/`。

使用 CPython 3.11 生成 `.pyc`、标准 `dis` golden 和 Scanner311
规范化 Token、CFG golden：

```bash
.venv311/bin/python test/bytecode_3.11/generate.py
```

检查 tracked golden 文件是否与当前源码一致：

```bash
.venv311/bin/python test/bytecode_3.11/generate.py --check
```

约定：

- `generated/**/*.pyc` 是本地生成物，由 `.gitignore` 忽略。
- `golden/**/*.dis` 是可审查的 CPython 3.11 标准反汇编基线。
- `golden_tokens/**/*.tokens` 是不含 `CACHE` 的规范化 Scanner311 基线；
- `golden_cfg/**/*.cfg` 是规范化指令对应的控制流基线；
- `opcode_fixtures/internal/print_expr.py` 使用首行
  `# compile-mode: single` 声明编译模式，其 code object 仍完全由 CPython
  `compile()` 产生；
- 如果 Normalizer 当前明确拒绝某项语料，Token 和 CFG golden 记录稳定的
  错误类型与上下文，不把该项误报为已支持；
- `09_straight_line.py` 是阶段 3 可生成源码并进行行为对比的直线型语料。
- 测试 corpus 必须能在 CPython 3.11 下编译。
- 新增语法样本后必须重新生成并检查 golden 文件。
