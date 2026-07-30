# CPython 3.11 Opcode 覆盖阶段 0 基线

记录日期：2026-07-30（Asia/Shanghai）

## 1. 仓库基线

- Git 提交：`17f332b4e5f800e0c98c47a71933b933c7cd1eff`
- 分支：`master`
- 阶段开始时工作区：干净
- 本阶段目标：冻结 opcode 清单、依赖、测试、支持范围和已知限制
- 本阶段约束：不修改 Scanner、Normalizer、Parser 或语义生成行为

## 2. 运行环境

- 系统：macOS 26.5.2（Build 25F84）
- 架构：arm64
- Python implementation：CPython
- Python：3.11.9
- Python cache tag：`cpython-311`
- 虚拟环境：`.venv311`
- `.pyc` magic：十六进制 `a70d0d0a`，整数 `3495`
- decompyle3：3.9.4.dev0
- xdis：6.3.0
- pytest：9.1.1
- click：8.4.2

完整 Python 包快照见
`test/bytecode_3.11/OPCODE_COVERAGE_DEPENDENCIES.txt`。

## 3. Opcode 表来源

阶段 0 将下列清单固定为当前覆盖工作的双重来源：

1. 权威运行时清单：CPython 3.11.9 的 `dis.opmap`；
2. Scanner 交叉检查：
   `xdis.opcodes.opcode_3x.opcode_311.opmap`。

检查结果：

```text
CPython dis.opmap: 110
xdis opcode_311.opmap: 110
编号和名称映射完全一致: true
```

当前目标是 CPython 3.11 标准磁盘 `.pyc`。如果后续更换 Python 或 xdis，
必须先运行矩阵一致性检查，不能静默更新 opcode 清单。

## 4. 初始矩阵

机器可读清单：

```text
test/bytecode_3.11/opcode_matrix.json
```

矩阵包含：

- 110 个 opcode 编号和名称；
- opcode 类别；
- 当前主语料中的首次 fixture；
- 当前主语料的所有来源文件；
- raw 和 normalized 是否被观察到；
- Scanner、Normalizer、Parser 和 Behavior 四层状态；
- 后续测试和说明字段。

阶段 0 中四层状态统一暂记为 `missing`。原因是现有 corpus 和回归测试虽然
已经覆盖大量功能，但尚未建立逐 opcode 的正式测试归因。后续阶段必须用
明确的测试节点将状态更新为：

```text
pass
internal_consumed
unsupported_fail_closed
not_applicable
missing
```

阶段 0 不以“指令在 corpus 中出现过”替代四层验证。

## 5. 当前 Corpus 覆盖

语料：

```text
test/simple_source/311/*.py
```

统计：

- 源文件：10 个；
- 递归 code object：105 个；
- raw opcode：97/110；
- normalized original opcode：96/110；
- raw 尚未触达：13 个；
- normalized 尚未触达：14 个，其中包含会被规范化流移除的 `CACHE`。

raw 尚未触达：

```text
DELETE_ATTR
DELETE_DEREF
DELETE_GLOBAL
IMPORT_STAR
LIST_TO_TUPLE
LOAD_ASSERTION_ERROR
LOAD_CLASSDEREF
PRINT_EXPR
SETUP_ANNOTATIONS
SET_UPDATE
STORE_GLOBAL
UNARY_NOT
UNARY_POSITIVE
```

normalized 尚未触达：

```text
CACHE
DELETE_ATTR
DELETE_DEREF
DELETE_GLOBAL
IMPORT_STAR
LIST_TO_TUPLE
LOAD_ASSERTION_ERROR
LOAD_CLASSDEREF
PRINT_EXPR
SETUP_ANNOTATIONS
SET_UPDATE
STORE_GLOBAL
UNARY_NOT
UNARY_POSITIVE
```

`CACHE` 出现在 raw stream，但不会作为 parser-facing semantic
instruction 保留。因此后续应将其标记为 `internal_consumed`，而不是要求
Parser 直接生成 AST。

## 6. 测试基线

命令：

```bash
.venv311/bin/python -m pytest -q -rs
```

第一次：

```text
110 passed, 6 skipped in 2.17s
```

第二次：

```text
110 passed, 6 skipped in 2.21s
```

两次测试结果一致，满足阶段 0 的可重复性要求。

skip 全部来自 Python 3.7/3.8 legacy 测试：

```text
pytest/test_code_deparse.py:33
pytest/test_code_deparse.py:72
pytest/test_code_deparse.py:99
pytest/test_deparse_offset.py:30
pytest/test_grammar.py:9
pytest/test_grammar.py:89
```

这些 skip 不表示新增的 Python 3.11 opcode 缺陷。

## 7. 当前声明支持范围快照

当前 `PYTHON_311_SUPPORT.md` 声明支持：

- 模块、函数、lambda、类、装饰器、注解和闭包；
- import、调用、unpack、f-string 和常用表达式；
- `if`、短路布尔表达式、`for`、`while`、break/continue 和 loop else；
- list/set/dict comprehension 和 generator expression；
- generator、yield from、coroutine、await、async comprehension 和
  async for；
- `try/except/else/finally`；
- `with` 和 `async with`；
- CPython 3.11 zero-cost exception table；
- `match/case` pattern 和 guard；
- 当前覆盖的 `except*` 和 ExceptionGroup 协议形态。

支持目标是语义等价。注释、原始空白、引号和冗余括号不能从字节码恢复。

## 8. 已知限制快照

阶段 0 固定以下已知限制：

- 不支持 PyPy 3.11、Cython、MicroPython；
- 不支持混淆、加密、打包或人工编辑的字节码；
- 普通磁盘 `.pyc` 不接受 live adaptive/specialized opcode；
- 标准库覆盖仍是测试子集；
- `except*` 与 `else` 或外层 `finally` 的部分组合 fail-closed；
- 部分不常见 stack rotation 仍未覆盖；
- assertion 和 import-star 的部分路径仍未覆盖；
- incrementally built mapping 的部分布局仍未覆盖；
- match 恢复只面向 canonical CPython 3.11 compiler shape；
- CPython 3.11 partial offset 反编译继续 fail-closed；
- 语法和重新编译通过不能单独证明行为等价。

后续如果实现或新增限制，必须同时更新：

```text
test/bytecode_3.11/opcode_matrix.json
test/bytecode_3.11/shape_matrix.json
PYTHON_311_OPCODE_COVERAGE.md
PYTHON_311_SHAPE_COVERAGE.md
PYTHON_311_SUPPORT.md
```

其中 `shape_matrix.json` 和两份生成报告将在后续阶段创建。

## 9. 阶段 0 验收

- [x] 固定 CPython 3.11 opcode 表来源；
- [x] 记录 Python、xdis、pytest 和项目版本；
- [x] 记录 110 个 opcode 编号和名称；
- [x] 记录当前 corpus 覆盖率；
- [x] 连续两次运行全量 pytest；
- [x] 保存当前支持范围；
- [x] 保存当前已知限制；
- [x] 阶段开始时 Git 工作区干净；
- [x] opcode 数量为 110；
- [x] opcode 编号没有重复；
- [x] opcode 名称没有重复；
- [x] CPython 和 xdis opcode 表一致；
- [x] 本阶段未修改 Parser 行为。

## 10. 阶段 0 产物

```text
test/bytecode_3.11/opcode_matrix.json
test/bytecode_3.11/OPCODE_COVERAGE_BASELINE.md
test/bytecode_3.11/OPCODE_COVERAGE_DEPENDENCIES.txt
```

下一阶段将实现矩阵 schema、报告生成、`--check` 和 CI 可用的一致性检查。
