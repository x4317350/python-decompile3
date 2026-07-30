# CPython 3.11 Opcode 四层覆盖实施计划

## 1. 文档目标

本文件用于固化 CPython 3.11 opcode 覆盖工作的执行流程，目标是建立并持续维护：

1. 110 个 CPython 3.11 基础 opcode 的四层覆盖矩阵；
2. 控制流和多指令组合的 shape 覆盖矩阵；
3. Scanner、Normalizer、Parser 和行为验证的自动化门禁；
4. 明确的支持、内部消费、缺失和 fail-closed 状态；
5. 可从失败测试反查 opcode、源码语料、字节码、AST 和行为差异的排查链路。

这项工作不能只统计某条指令是否在测试中出现。单条 opcode 覆盖不能证明
短路表达式、异常表、循环、生成器或 `match` 等指令组合能正确反编译。

## 2. 当前基线

截至本计划创建时：

- CPython 3.11 基础 opcode：110 个；
- `test/simple_source/311` 主语料触达原始 opcode：97 个；
- 主语料触达规范化语义 opcode：96 个；
- 主语料尚未触达：13 个；
- 全量测试：`110 passed, 6 skipped`；
- 6 个 skip 均为仅适用于 Python 3.7/3.8 的 legacy 测试；
- Python 3.11 Scanner、Normalizer 和 Parser 已具备广泛支持，但仍有明确的
  fail-closed 形态。

尚未被主语料触达的 13 个 opcode：

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

“尚未触达”不等于“没有实现”。本计划需要区分：

- 已实现但缺少语料；
- 仅 Scanner/Normalizer 支持；
- Parser 内部消费；
- 明确不支持；
- 尚未实现。

## 3. 支持范围

### 3.1 本计划包含

- CPython 3.11 正式版本生成的标准磁盘 `.pyc`；
- CPython 编译器正常生成的 code object；
- `exec`、`eval`、`single` 和 lambda 等现有支持的编译模式；
- 模块、函数、类、闭包、生成器、协程和异步生成器；
- 标准 CPython 3.11 exception table；
- 基础 opcode、CACHE、EXTENDED_ARG 和内部调用协议；
- 标准编译器生成的跳转和栈操作组合；
- 语法、AST、重新编译和行为一致性验证。

### 3.2 本计划不包含

- PyPy、Cython、MicroPython 等非 CPython 字节码；
- 混淆、加密、压缩或打包后的字节码；
- 人工编辑或手工拼接的 code object；
- 非标准跳转目标或故意破坏的栈布局；
- 将 live adaptive/specialized code object 当作普通磁盘 `.pyc`；
- 原始注释、空白、引号、冗余括号等非语义源码格式；
- Python 3.11 partial offset 反编译。该功能继续作为独立项目处理。

### 3.3 目标定义

“全指令覆盖”必须同时满足：

- 110/110 opcode 已进入机器可读清单；
- Scanner 对 110/110 有明确状态；
- Normalizer 对 110/110 有明确状态；
- Parser 对 110/110 有明确状态；
- 所有声明支持的 opcode 都有行为验证；
- 所有内部协议 opcode 都有 `internal_consumed` 验证；
- 所有不支持项都有稳定的 fail-closed 测试；
- shape 矩阵不存在未经解释的 `missing`；
- 标准库和真实项目回归达到预先确定的通过率。

## 4. 四层覆盖模型

### 4.1 Scanner

验证原始字节码读取：

- opcode 编号和名称；
- 2 字节物理指令布局；
- argument 和 argval；
- CACHE 位置及 owner；
- EXTENDED_ARG；
- 物理 offset；
- jump target；
- source position 和 line table；
- nested code object；
- exception table 原始数据。

### 4.2 Normalizer

验证 parser-facing instruction：

- normalized kind；
- internal 标记；
- stack pop、push 和 effect；
- jump/fallthrough 两条路径的 stack effect；
- physical/logical offset 映射；
- call/function/scope metadata；
- specialized-to-base 映射；
- 非法协议的稳定错误。

### 4.3 Parser

验证 opcode 在上下文中的语义消费：

- 直接生成 AST；
- 作为内部协议被消费；
- 由结构恢复器组合成语句；
- 由表达式 CFG 组合成表达式；
- 明确 fail-closed；
- 不允许静默丢弃未知 opcode。

### 4.4 行为验证

比较原源码和恢复源码：

- 返回值；
- 返回类型；
- stdout；
- stderr；
- 退出码；
- 异常类型和异常参数；
- 全局变量变化；
- 对象可观察状态；
- 副作用执行次数和顺序；
- generator/coroutine 产出顺序；
- context manager enter/exit 顺序。

## 5. 状态枚举

四层矩阵统一使用：

| 状态 | 含义 |
| --- | --- |
| `pass` | 已实现并通过该层验证 |
| `internal_consumed` | Parser 消费的内部协议，不直接生成 AST |
| `unsupported_fail_closed` | 明确不支持，并有稳定错误测试 |
| `not_applicable` | 该层不适用 |
| `missing` | 尚未实现或没有测试 |

规则：

- 不允许空字符串或未定义状态；
- `unsupported_fail_closed` 必须填写错误类型和测试；
- `pass` 必须填写至少一个测试节点；
- `internal_consumed` 必须说明由哪个上层结构消费；
- `missing` 必须填写后续阶段或 issue；
- 报告生成器遇到未知状态必须返回非零状态。

## 6. 预期目录结构

```text
test/bytecode_3.11/
├── opcode_matrix.json
├── shape_matrix.json
├── generate_opcode_matrix.py
├── generated/
├── golden/
├── golden_tokens/
├── golden_cfg/
└── opcode_fixtures/
    ├── expressions/
    ├── scope/
    ├── collections/
    ├── calls/
    ├── control_flow/
    ├── generators/
    ├── exceptions/
    ├── match/
    └── internal/

pytest/
├── test_opcode_inventory311.py
├── test_opcode_scanner311.py
├── test_opcode_normalizer311.py
├── test_opcode_parser311.py
├── test_opcode_behavior311.py
└── test_shape_behavior311.py

PYTHON_311_OPCODE_COVERAGE.md
PYTHON_311_SHAPE_COVERAGE.md
```

JSON 作为唯一机器可读事实来源，Markdown 报告由生成器产生，不手工维护
两份状态。

## 7. Opcode 矩阵格式

每个 opcode 至少记录：

```json
{
  "opcode": 100,
  "name": "LOAD_CONST",
  "category": "load",
  "source_fixture": "expressions/load_const.py",
  "scanner": {
    "status": "pass",
    "tests": [
      "pytest/test_opcode_scanner311.py::test_load_const"
    ]
  },
  "normalizer": {
    "status": "pass",
    "kind": "LOAD_CONST",
    "tests": [
      "pytest/test_opcode_normalizer311.py::test_load_const"
    ]
  },
  "parser": {
    "status": "pass",
    "consumer": "StraightLineDecompiler311",
    "tests": [
      "pytest/test_opcode_parser311.py::test_load_const"
    ]
  },
  "behavior": {
    "status": "pass",
    "tests": [
      "pytest/test_opcode_behavior311.py::test_load_const"
    ]
  },
  "notes": ""
}
```

内部协议示例：

```json
{
  "name": "CACHE",
  "scanner": {"status": "pass"},
  "normalizer": {"status": "internal_consumed"},
  "parser": {
    "status": "internal_consumed",
    "consumer": "Scanner311 cache-owner mapping"
  },
  "behavior": {"status": "not_applicable"}
}
```

## 8. Shape 矩阵格式

指令组合单独记录：

```json
{
  "name": "except_star_with_else",
  "category": "exception",
  "status": "unsupported_fail_closed",
  "fixture": "exceptions/except_star_else.py",
  "expected_error": "UnsupportedPython311ControlFlow",
  "tests": [
    "pytest/test_shape_behavior311.py::test_except_star_else_fails_closed"
  ],
  "notes": ""
}
```

shape 矩阵至少包含：

- 嵌套 `and/or` 和短路返回；
- 三元表达式；
- 链式比较；
- 多 return；
- `if/elif/else`；
- `for/while`、`break/continue` 和 loop `else`；
- 多层 comprehension filter；
- generator、yield from、await、async for；
- `try/except/else/finally`；
- `with/async with`；
- `except*` 和 ExceptionGroup 组合；
- `match/case` pattern 和 guard；
- 闭包和 class scope；
- annotation、assert、import-star；
- starred collection 和增量 mapping。

## 9. 分阶段执行计划

### 阶段 0：冻结范围和基线

任务：

- [ ] 固定 CPython 3.11 opcode 表来源；
- [ ] 记录 Python、xdis、pytest 和项目版本；
- [ ] 记录 110 个 opcode 编号和名称；
- [ ] 记录当前 corpus 覆盖率；
- [ ] 运行全量 pytest；
- [ ] 保存 3.11 当前支持和已知限制；
- [ ] 确认 Git 工作区干净。

产物：

- `opcode_matrix.json` 初始清单；
- 基线报告；
- 依赖和测试快照。

验收：

- opcode 数量为 110；
- 不存在重复编号或名称；
- 当前测试结果可重复；
- 本阶段不修改 Parser 行为。

### 阶段 1：实现矩阵生成和一致性检查

任务：

- [x] 实现 `generate_opcode_matrix.py`；
- [x] 验证 JSON schema；
- [x] 生成 `PYTHON_311_OPCODE_COVERAGE.md`；
- [x] 生成 `PYTHON_311_SHAPE_COVERAGE.md`；
- [x] 增加 `--check` 模式；
- [x] 检查报告是否过期；
- [x] 未知状态或缺少字段时返回非零状态。

验收命令：

```bash
.venv311/bin/python test/bytecode_3.11/generate_opcode_matrix.py
.venv311/bin/python test/bytecode_3.11/generate_opcode_matrix.py --check
```

### 阶段 2：补齐 13 个未触达 opcode 语料

建议语料：

| Opcode | 源码结构 |
| --- | --- |
| `DELETE_ATTR` | `del obj.attr` |
| `DELETE_DEREF` | 闭包中的 `nonlocal` 删除 |
| `DELETE_GLOBAL` | `global value; del value` |
| `IMPORT_STAR` | `from module import *` |
| `LIST_TO_TUPLE` | `(*values,)` |
| `LOAD_ASSERTION_ERROR` | `assert condition, message` |
| `LOAD_CLASSDEREF` | 类体引用外层闭包变量 |
| `PRINT_EXPR` | `compile(..., mode="single")` |
| `SETUP_ANNOTATIONS` | 模块和类变量注解 |
| `SET_UPDATE` | `{*values}` |
| `STORE_GLOBAL` | `global value; value = 1` |
| `UNARY_NOT` | `not value` |
| `UNARY_POSITIVE` | `+value` |

任务：

- [x] 只使用 CPython 编译器生成字节码；
- [x] 禁止手工拼接 code object；
- [x] 为每个语料生成 `.pyc`；
- [x] 更新标准 `dis` golden；
- [x] 更新 normalized token golden；
- [x] 更新 CFG golden；
- [x] 矩阵关联到具体 fixture 和测试。

验收：

- 主语料 raw opcode 达到 110/110；
- 每个新增语料可以独立编译；
- golden `--check` 通过。

### 阶段 3：Scanner 110/110

新增 `pytest/test_opcode_scanner311.py`。

任务：

- [x] 为 110 个 opcode 建立参数化 Scanner 测试；
- [x] 验证编号、名称、参数和物理 offset；
- [x] 验证 CACHE owner；
- [x] 验证 EXTENDED_ARG；
- [x] 验证 jump target；
- [x] 验证 line/position；
- [x] 验证 nested code object；
- [x] 验证 exception table 读取；
- [x] 保留非法 opcode、奇数长度和非法 CACHE 测试。

验收：

```text
Scanner: 110/110 pass
```

### 阶段 4：Normalizer 110/110

新增 `pytest/test_opcode_normalizer311.py`。

任务：

- [x] 验证 normalized kind；
- [x] 验证 internal 标记；
- [x] 验证 stack pop/push/effect；
- [x] 验证 jump stack effect；
- [x] 验证 physical/logical offset；
- [x] 验证 call/function/scope metadata；
- [x] 验证 specialized-to-base；
- [x] 验证 malformed protocol fail-closed。

内部协议至少包括：

```text
CACHE
RESUME
EXTENDED_ARG
PUSH_NULL
PRECALL
KW_NAMES
MAKE_CELL
COPY_FREE_VARS
```

验收：

```text
Normalizer: 110/110 pass 或 internal_consumed
Unknown stack effect: 0
Unknown normalized kind: 0
```

### 阶段 5：Parser 按语义族补齐

执行顺序：

1. 名称、作用域、global/nonlocal 和删除；
2. 一元、二元、比较和短路表达式；
3. collection、unpack、starred 和 mapping；
4. 调用、函数、闭包、类和装饰器；
5. import、annotation 和 assert；
6. 条件、循环和跳转；
7. comprehension、generator 和 async；
8. exception table、with 和 except*；
9. match/case；
10. 内部协议 opcode。

每个语义族必须：

- [x] 更新 opcode 矩阵；
- [x] 更新 shape 矩阵；
- [x] 增加 AST 测试；
- [x] 增加行为测试；
- [x] 增加不支持形态的 fail-closed 测试；
- [x] 运行该语义族相关测试；
- [x] 运行全量测试。

建议修复优先级：

1. assert、annotation、global/delete、starred collection；
2. import-star；
3. 增量 mapping 和不常见 stack rotation；
4. `except* + else`；
5. `except* + finally`；
6. 标准库回归发现的其他组合。

### 阶段 6：行为验证框架

新增：

```text
pytest/test_opcode_behavior311.py
pytest/test_shape_behavior311.py
```

标准执行链路：

```text
fixture.py
   ↓ CPython 3.11 compile
fixture.pyc
   ↓ decompyle3
recovered.py
   ↓ 隔离子进程执行
对比原文件和恢复文件
```

任务：

- [ ] 比较返回值和类型；
- [ ] 比较 stdout、stderr 和退出码；
- [ ] 比较异常类型和参数；
- [ ] 比较全局状态和对象状态；
- [ ] 比较副作用顺序；
- [ ] 比较 generator/coroutine 产出；
- [ ] 比较 context manager 协议；
- [ ] 屏蔽路径、地址和时间等非确定性字段；
- [ ] 设置超时；
- [ ] 失败时保留源码、`.pyc`、恢复文件和输出。

### 阶段 7：补齐已知不支持组合

每个已知不支持项只能选择以下两种结果：

1. 实现并转为 `pass`；
2. 保持 `unsupported_fail_closed`，增加明确错误测试。

禁止：

- 静默生成猜测源码；
- 仅以“生成文件成功”作为通过；
- 仅检查语法，不检查行为；
- 为了矩阵数字删除 fail-closed 防护。

重点项目：

- `except*` 与 `else`；
- `except*` 与外层 `finally`；
- assertion paths；
- import-star namespace 行为；
- uncommon stack rotations；
- incrementally built mapping；
- canonical 之外的 match 边界。

### 阶段 8：标准库和真实项目回归

语料范围：

- Python 3.11 标准库纯 Python 模块；
- 本工程源码；
- 常见纯 Python 第三方包；
- 控制流、异常、生成器和 async 密集模块。

每次运行记录：

```text
输入文件数
成功反编译数
语法失败数
fail-closed 数
行为一致数
行为不一致数
首次失败 opcode
首次失败 shape
```

失败必须归类到：

- 已知 opcode 项；
- 已知 shape 项；
- 新增 matrix 项；
- malformed/unsupported input。

不允许只记录 traceback 而不更新矩阵。

### 阶段 9：CI 和发布门禁

CI 必须执行：

```text
opcode inventory check
shape inventory check
Scanner matrix tests
Normalizer matrix tests
Parser matrix tests
behavior tests
golden --check
full pytest
documentation freshness check
```

报告至少输出：

```text
Opcode inventory: 110/110
Scanner: x/110
Normalizer: x/110
Parser pass: x/110
Parser internal_consumed: x/110
Parser unsupported_fail_closed: x/110
Parser missing: x/110
Behavior verified: x/110
Shape pass: x
Shape fail-closed: x
Shape missing: x
```

发布门禁：

- `missing` 数量必须符合当前发布目标；
- 新增 `missing` 必须阻止合并；
- `pass` 退化为 fail-closed 必须显式审批；
- 行为不一致必须阻止合并；
- 生成报告与 JSON 不同步必须阻止合并；
- 全量 pytest 不得新增失败或无解释的 skip。

## 10. 每阶段统一执行流程

每个阶段按下列顺序执行：

1. 确认 Git 工作区状态；
2. 记录阶段前矩阵和测试基线；
3. 添加最小失败语料；
4. 添加 Scanner/Normalizer/Parser/行为测试；
5. 先确认测试能够暴露缺口；
6. 实现最小修复；
7. 运行定向测试；
8. 运行相邻语义族测试；
9. 运行全量测试；
10. 更新矩阵和 Markdown 报告；
11. 执行 golden `--check`；
12. 更新阶段执行记录；
13. 核对 Git diff；
14. 单独提交该阶段。

建议每个阶段独立提交，避免 Scanner、Normalizer、Parser 和行为框架混入
同一个难以回退的提交。

## 11. 测试命令模板

环境检查：

```bash
.venv311/bin/python -c \
  'import sys; assert sys.version_info[:2] == (3, 11); print(sys.version)'
.venv311/bin/decompyle3 --version
```

矩阵检查：

```bash
.venv311/bin/python \
  test/bytecode_3.11/generate_opcode_matrix.py --check
```

golden 检查：

```bash
.venv311/bin/python test/bytecode_3.11/generate.py --check
```

定向测试：

```bash
.venv311/bin/python -m pytest \
  pytest/test_opcode_inventory311.py \
  pytest/test_opcode_scanner311.py \
  pytest/test_opcode_normalizer311.py \
  pytest/test_opcode_parser311.py \
  pytest/test_opcode_behavior311.py \
  pytest/test_shape_behavior311.py -q
```

现有 3.11 回归：

```bash
.venv311/bin/python -m pytest \
  pytest/test_scanner311.py \
  pytest/test_normalize311.py \
  pytest/test_deparse311.py \
  pytest/test_expressions311.py \
  pytest/test_controlflow311.py \
  pytest/test_generators311.py \
  pytest/test_exceptiontable311.py \
  pytest/test_syntax311.py \
  pytest/test_reliability311.py -q
```

全量测试：

```bash
.venv311/bin/python -m pytest -q -rs
```

格式和差异：

```bash
.venv311/bin/flake8 decompyle3 pytest
git diff --check
git status --short
```

## 12. 失败产物保留

行为验证失败时必须保留：

```text
fixture.py
fixture.pyc
fixture.dis
fixture.tokens
fixture.cfg
recovered.py
original.stdout
original.stderr
original.exitcode
recovered.stdout
recovered.stderr
recovered.exitcode
failure.json
```

`failure.json` 至少记录：

```json
{
  "opcode": "POP_JUMP_FORWARD_IF_FALSE",
  "shape": "mixed_short_circuit_return",
  "code_name": "apply",
  "offset": 6,
  "exception": "Python311ParseError",
  "runtime": "3.11.x",
  "target": "3.11",
  "fixture": "path/to/fixture.py"
}
```

## 13. 风险控制

### 13.1 Parser 回归

防护：

- 先添加失败测试；
- 尽量复用已有 CFG/AST 恢复路径；
- 新路径必须 fail-closed；
- 失败时不得修改父解析器状态；
- 普通 if、循环、异常和 match 保留防回归测试。

### 13.2 为覆盖数字而支持非法输入

防护：

- 只使用 CPython 编译器生成的标准 code object；
- 不手工构造无效字节码；
- malformed 输入保持稳定拒绝；
- `unsupported_fail_closed` 是合法完成状态。

### 13.3 行为测试不稳定

防护：

- 隔离子进程；
- 固定输入；
- 固定环境；
- 设置超时；
- 规范化临时路径；
- 不比较对象地址和时间；
- 失败时保存完整现场。

### 13.4 矩阵和实现不同步

防护：

- JSON 是唯一事实来源；
- Markdown 自动生成；
- CI 使用 `--check`；
- 测试节点不存在时矩阵生成失败；
- opcode 表变化时阻止合并。

## 14. 提交策略

建议提交顺序：

1. `test: add Python 3.11 opcode inventory`
2. `test: add opcode coverage matrix generator`
3. `test: cover remaining Python 3.11 raw opcodes`
4. `test: complete Scanner311 opcode matrix`
5. `test: complete Normalizer311 opcode matrix`
6. `feat: complete Python 3.11 scope and assertion recovery`
7. `feat: complete Python 3.11 collection and import recovery`
8. `feat: extend Python 3.11 exception-group recovery`
9. `test: add Python 3.11 differential behavior corpus`
10. `ci: enforce Python 3.11 opcode and shape coverage`

每个提交必须包含：

- 修改说明；
- 矩阵变化；
- 定向测试结果；
- 全量测试结果；
- 新增支持或 fail-closed 边界。

## 15. 最终验收清单

- [x] 110/110 opcode 已进入矩阵；
- [x] Scanner 110/110 有明确状态；
- [x] Normalizer 110/110 有明确状态；
- [x] Parser 110/110 有明确状态；
- [ ] 行为层 110/110 有明确状态；
- [x] 内部协议 opcode 全部标记为 `internal_consumed`；
- [x] 13 个未触达 opcode 已补齐语料；
- [x] shape 矩阵不存在未解释空白；
- [ ] 所有声明支持项都有行为测试；
- [x] 所有不支持项都有 fail-closed 测试；
- [x] golden 检查通过；
- [x] 3.11 定向回归通过；
- [x] 全量 pytest 无新增失败和 skip；
- [ ] 标准库和真实项目结果已归档；
- [ ] CI 能阻止矩阵、报告和实现不同步；
- [ ] `PYTHON_311_SUPPORT.md` 与最终矩阵一致。

## 16. 执行记录

每个阶段完成后追加：

```text
阶段：
提交：
新增语料：
新增 opcode 覆盖：
新增 shape 覆盖：
Scanner：
Normalizer：
Parser：
Behavior：
定向测试：
全量测试：
已知限制：
失败现场：
```

当前状态：

- 阶段 0、阶段 1、阶段 2、阶段 3、阶段 4 和阶段 5 已完成；
- 已创建 `opcode_matrix.json` 和 `shape_matrix.json`；
- 两份 Markdown 覆盖报告均由生成器维护，`--check` 可检测过期；
- raw opcode corpus 已达到 110/110，normalized original opcode 为
  109/110，`CACHE` 由 owner 消费；
- Scanner 层已达到 110/110 pass；
- Normalizer 层已达到 102 pass + 8 internal_consumed，即 110/110；
- Parser 层已达到 102 pass + 8 internal_consumed，即 110/110；
- shape inventory 为 31 项：27 pass、4 unsupported_fail_closed、
  0 missing；
- 本阶段补齐 assert、annotation、import-star、删除声明和未来注解恢复；
- 行为层逐 opcode 状态仍为 missing，留待阶段 6 建立统一框架。

阶段 0：

```text
阶段：0，冻结范围和基线
提交：本文件所在 Git 提交，提交说明为“测试：固化 Python 3.11 opcode 覆盖基线”
新增语料：0
新增 opcode 覆盖：110/110 inventory；raw corpus 97/110；normalized corpus 96/110
新增 shape 覆盖：0
Scanner：逐 opcode 正式状态暂为 missing，后续阶段归因
Normalizer：逐 opcode 正式状态暂为 missing，后续阶段归因
Parser：逐 opcode 正式状态暂为 missing，后续阶段归因
Behavior：逐 opcode 正式状态暂为 missing，后续阶段归因
定向测试：现有 10 个 CPython 3.11 corpus golden --check 通过
全量测试：110 passed, 6 skipped；连续两次结果一致
已知限制：见 test/bytecode_3.11/OPCODE_COVERAGE_BASELINE.md
失败现场：无
```

阶段 1：

```text
阶段：1，实现矩阵生成和一致性检查
提交：本文件所在 Git 提交，提交说明为“测试：建立 Python 3.11 opcode 覆盖矩阵基础设施”
新增语料：0
新增 opcode 覆盖：0；沿用阶段 0 inventory 110/110、raw 97/110、normalized 96/110
新增 shape 覆盖：建立 30 项 shape inventory；pass 20、fail-closed 3、missing 7
Scanner：未修改；110 项状态仍为 missing
Normalizer：未修改；110 项状态仍为 missing
Parser：未修改；110 项状态仍为 missing
Behavior：未修改；110 项状态仍为 missing
定向测试：pytest/test_opcode_inventory311.py，6 passed
全量测试：116 passed, 6 skipped；本阶段无新增 skip
已知限制：7 项 shape 尚为 missing；逐 opcode 四层状态将在后续阶段归因
失败现场：未知状态和缺少字段返回 2；过期或缺失报告返回 1
```

阶段 2：

```text
阶段：2，补齐 13 个未触达 opcode 语料
提交：本文件所在 Git 提交，提交说明为“测试：补齐 Python 3.11 的 13 个 opcode 语料”
新增语料：13；全部由 CPython 3.11 compile/py_compile 生成，single-mode 使用 CPython checked-hash pyc 序列化
新增 opcode 覆盖：raw 从 97/110 提升到 110/110；normalized 从 96/110 提升到 108/110
新增 shape 覆盖：为 single-mode 和 6 项 missing shape 关联最小 fixture；状态未提前变更
Scanner：未修改；13 个目标 raw opcode 均有独立 fixture 和标准 dis golden
Normalizer：未修改；12 个目标进入 normalized token；SETUP_ANNOTATIONS 稳定触发 StackDepthError
Parser：未修改；逐 opcode 正式状态仍为 missing，留待阶段 5 和阶段 6 归因
Behavior：未修改；新增语料只验证编译、pyc 加载、raw/normalized 和 golden
定向测试：opcode corpus、inventory 和 corpus 测试，49 passed
相邻测试：Scanner、Normalizer、表达式和反编译测试，38 passed
全量测试：156 passed, 6 skipped；本阶段无新增 skip
已知限制：CACHE 为内部缓存；SETUP_ANNOTATIONS 的 Normalizer 栈效应留待阶段 4 修复
失败现场：SETUP_ANNOTATIONS 在模块 offset 2 和类体 offset 10 触发 StackDepthError，已固化到 token/CFG golden
```

阶段 3：

```text
阶段：3，Scanner 110/110
提交：本文件所在 Git 提交，提交说明为“测试：完成 Python 3.11 Scanner 的 110 项 opcode 覆盖”
新增语料：0；复用阶段 2 的 23 个 corpus、131 个 code object
新增 opcode 覆盖：Scanner 从 0/110 提升到 110/110 pass
新增 shape 覆盖：0
Scanner：新增 110 项参数化 dis 对照；拒绝孤立/缺失 CACHE；重新计算前向和后向 jump-target 标记
Normalizer：未修改；CACHE owner 仅通过现有规范化接口交叉验证
Parser：未修改；逐 opcode Parser 状态仍为 missing
Behavior：未修改；逐 opcode Behavior 状态仍为 missing
定向测试：pytest/test_opcode_scanner311.py，116 passed
相邻测试：Scanner、Normalizer、控制流和异常表，145 passed
全量测试：272 passed, 6 skipped；本阶段无新增 skip
golden/报告：23 个 corpus golden --check 通过；opcode/shape 报告 --check 通过
已知限制：SETUP_ANNOTATIONS 的 Normalizer StackDepthError 留待阶段 4
失败现场：Scanner 原先接受非法 CACHE 布局；xdis 原始结果遗漏部分后向 jump-target 标记，均已修复并回归
```

阶段 4：

```text
阶段：4，Normalizer 110/110
提交：本文件所在 Git 提交，提交说明为“测试：完成 Python 3.11 Normalizer 的 110 项 opcode 覆盖”
新增语料：0；复用 23 个 corpus、131 个 code object
新增 opcode 覆盖：Normalizer 从 0/110 提升到 102 pass + 8 internal_consumed
新增 shape 覆盖：0；variable_annotations 仍为 missing，但 Normalizer 缺口已解除
Scanner：未修改；维持 110/110 pass
Normalizer：SETUP_ANNOTATIONS 改为 0 pop/0 push/0 effect；统一 8 个内部协议；unknown effect/kind 均为 0
Parser：未修改；逐 opcode Parser 状态仍为 missing
Behavior：未修改；逐 opcode Behavior 状态仍为 missing
定向测试：pytest/test_opcode_normalizer311.py，185 passed
相邻测试：Normalizer、Scanner、Parser、控制流、生成器、异常表和语法，345 passed
全量测试：457 passed, 6 skipped；本阶段无新增 skip
golden/报告：23 个 corpus golden --check 通过；opcode/shape 报告 --check 通过
已知限制：CACHE 不生成独立 normalized instruction，由 owner 的 cache_offsets 消费
失败现场：xdis 将 SETUP_ANNOTATIONS 表示为 1 pop/1 push，导致入口深度误判；已按 CPython dis 的零栈效应修复
```

阶段 5：

```text
阶段：5，Parser 按语义族补齐
提交：本文件所在 Git 提交，提交说明为“功能：完成 Python 3.11 Parser 的 110 项 opcode 覆盖”
新增语料：0 个文件；新增 1 项 inline 标准 CPython compound-assert shape
新增 opcode 覆盖：Parser 从 0/110 提升到 102 pass + 8 internal_consumed
新增 shape 覆盖：7 项 missing 转为 pass；新增 compound_assert_condition fail-closed；合计 27 pass、4 fail-closed、0 missing
Scanner：未修改；维持 110/110 pass
Normalizer：未修改；维持 102 pass + 8 internal_consumed
Parser：新增 110 项参数化契约与十个语义族 AST 验证；恢复 IMPORT_STAR、SETUP_ANNOTATIONS、LOAD_ASSERTION_ERROR、DELETE_GLOBAL/DELETE_DEREF 声明和未来注解
Behavior：新增阶段 5 聚焦语义验证；逐 opcode 统一行为状态留待阶段 6
定向测试：pytest/test_opcode_parser311.py，131 passed；连同矩阵门禁为 137 passed
相邻测试：Parser、矩阵、控制流、表达式、生成器、异常表、语法和可靠性，215 passed
全量测试：588 passed, 6 skipped；本阶段无新增 skip
golden/报告：23 个 corpus golden 已按 dont_inherit=True 刷新；opcode/shape 报告已更新
已知限制：compound and/or assert、except* + else、except* + finally 和人工不可约 CFG 保持显式 fail-closed
失败现场：IMPORT_STAR、SETUP_ANNOTATIONS、LOAD_ASSERTION_ERROR 初始失败；延迟注解二次加引号及删除声明缺失已修复
```
