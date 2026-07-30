# CPython 3.11 反编译支持执行计划

> 本文档是 `python-decompile3` 增加 CPython 3.11 字节码反编译能力的顺序执行清单。
> 后续实施时应从第一个未完成的复选框继续，不跳过阶段验收。

## 1. 项目目标

在保留现有 Python 3.7/3.8 功能的前提下，为本项目增加 CPython 3.11.x `.pyc` 反编译支持。

第一版支持范围：

- CPython 3.11.x 生成的标准磁盘 `.pyc` 文件。
- 未混淆、未加密、未人工修改的字节码。
- 模块、函数、类及嵌套 code object。
- 反编译结果追求语义等价，不恢复注释和原始排版。
- 不支持的结构必须明确报错，禁止静默输出可能错误的源码。

第一版不作为强制目标：

- PyPy 3.11 字节码。
- Cython、MicroPython 或其他非 CPython 字节码。
- 经过混淆、加壳或非法修改的 `.pyc`。
- 从运行中解释器提取的任意 specialized/adaptive code object。
- 与原始源码逐字符一致。

## 2. 执行原则

1. 严格按照阶段 0 至阶段 8 的顺序执行。
2. 每个阶段只有在“验收条件”全部满足后才能标记完成。
3. 修改 3.11 支持时，不得破坏现有 3.7/3.8 行为。
4. 优先建立测试，再实现对应功能。
5. 原始 opcode、规范化 Token、CFG 和源码生成必须保持分层。
6. 不把 3.11 指令强行伪装成 3.8 指令，除非该映射语义明确且有测试。
7. 无法可靠结构化的控制流必须抛出明确异常。
8. 每完成一个阶段，在本文档末尾的“执行记录”中写入日期、结果和验证命令。
9. 未经用户明确要求，不自动创建提交、推送分支或发布版本。

## 3. 目标流水线

```text
CPython 3.11 .pyc
        |
        v
xdis 装载 Code Object
        |
        v
Scanner311 读取原始指令
        |
        v
3.11 指令规范化层
        |
        +--------------------+
        |                    |
        v                    v
  普通控制流 CFG       co_exceptiontable
        |                    |
        +---------+----------+
                  |
                  v
          结构化控制流信息
                  |
                  v
             Parser311
                  |
                  v
            Parse Tree / AST
                  |
                  v
          SourceWalker + 3.11
                  |
                  v
            Python 源代码
```

## 4. 总体状态

| 阶段 | 内容 | 状态 |
|---|---|---|
| 0 | 环境、基线和测试语料 | 未开始 |
| 1 | 3.11 `.pyc` 装载和原始扫描 | 未开始 |
| 2 | 3.11 指令规范化 | 未开始 |
| 3 | 无复杂控制流的源码恢复 | 未开始 |
| 4 | CFG 和普通控制流恢复 | 未开始 |
| 5 | 推导式、生成器和协程 | 未开始 |
| 6 | 异常表、异常语句和 `with` | 未开始 |
| 7 | `match/case`、`except*` 等新语法 | 未开始 |
| 8 | 回归、可靠性、文档和发布准备 | 未开始 |

允许的状态只有：`未开始`、`进行中`、`已完成`、`阻塞`。

---

## 阶段 0：环境、基线和测试语料

### 0.1 记录修改前状态

- [ ] 记录当前 Git 提交和工作区状态。
- [ ] 记录当前 Python、操作系统和依赖版本。
- [ ] 确认工作区中的已有修改，禁止覆盖无关用户改动。
- [ ] 建立独立的 Python 3.11 开发环境。
- [ ] 安装项目及测试依赖。
- [ ] 确认所用 `xdis` 能读取 CPython 3.11 `.pyc`。
- [ ] 如需提高 `xdis` 最低版本，先通过测试确认，再修改 `pyproject.toml`。

建议记录命令：

```bash
git rev-parse HEAD
git status --short
python3.11 --version
python3.11 -m venv .venv311
.venv311/bin/python -m pip install -e '.[dev]'
.venv311/bin/python -m pip freeze
```

安装或下载依赖时，如果执行环境要求网络授权，应先申请授权。

### 0.2 建立回归基线

- [ ] 运行当前 pytest 测试。
- [ ] 运行项目现有 `make check`。
- [ ] 记录现有失败、跳过和预期失败，不能把历史失败归因于 3.11 修改。
- [ ] 保存一份简短基线报告到 `test/bytecode_3.11/BASELINE.md`。

建议验证命令：

```bash
.venv311/bin/python -m pytest -q
make check
```

### 0.3 建立 3.11 测试目录

计划新增：

```text
test/simple_source/311/
test/bytecode_3.11/
pytest/test_deparse311.py
pytest/test_scanner311.py
pytest/test_controlflow311.py
pytest/test_exceptiontable311.py
```

- [ ] 创建测试目录。
- [ ] 编写生成 `.pyc` 的可重复脚本或 Makefile 目标。
- [ ] 保留原始 `.py`，必要时生成 `.pyc`，避免只提交不可审查的二进制样本。
- [ ] 为 Token 测试建立可审查的 golden 输出。
- [ ] 为行为验证建立统一辅助函数。

### 0.4 建立最小语法语料

- [ ] 常量、赋值和删除。
- [ ] 一元、二元和原地运算。
- [ ] 属性、下标和切片。
- [ ] 位置参数和关键字参数调用。
- [ ] 函数、lambda、默认参数和注解。
- [ ] 类、继承、方法和装饰器。
- [ ] `if/elif/else` 和布尔短路。
- [ ] `for/while`、`break/continue` 和 loop-else。
- [ ] list/set/dict comprehension 和 generator expression。
- [ ] `yield`、`yield from` 和 `await`。
- [ ] `try/except/else/finally`。
- [ ] `with` 和 `async with`。
- [ ] `match/case`。
- [ ] `except*` 和 ExceptionGroup。

### 阶段 0 验收条件

- [ ] 现有测试基线已记录。
- [ ] 3.11 测试语料可以重复生成。
- [ ] 测试能够区分装载、扫描、解析、源码生成和行为验证失败。
- [ ] 原始工作区无关文件未被修改。

### 阶段 0 交付物

```text
test/bytecode_3.11/BASELINE.md
test/simple_source/311/
pytest/test_deparse311.py
pytest/test_scanner311.py
```

---

## 阶段 1：3.11 `.pyc` 装载和原始扫描

### 1.1 注册版本

涉及文件：

```text
decompyle3/scanner.py
decompyle3/parsers/main.py
decompyle3/bin/decompile.py
```

- [ ] 在 Scanner 版本注册表中增加 `(3, 11)`。
- [ ] 增加 `Scanner311` 动态分派。
- [ ] 调整 CLI 的支持版本说明。
- [ ] Parser 尚未实现时，应返回清晰的“Scanner 已支持、Parser 未支持”错误。

### 1.2 新建原始 Scanner311

计划新增：

```text
decompyle3/scanners/scanner311.py
```

- [ ] 使用 `xdis` 的 CPython 3.11 opcode 定义。
- [ ] 不直接调用依赖已删除 opcode 的 `Scanner37Base.__init__`。
- [ ] 读取所有原始指令及真实 byte offset。
- [ ] 建立 `offset -> instruction index` 映射。
- [ ] 读取嵌套 code object。
- [ ] 读取行号、位置信息和 code object 元数据。
- [ ] 暂存原始 `co_exceptiontable`，本阶段不要求恢复异常语句。
- [ ] 为未知或畸形指令提供明确错误。

### 1.3 原始指令测试

- [ ] 原始 Scanner 输出与 Python 3.11 `dis.get_instructions()` 对照。
- [ ] 验证跳转目标、常量、名称和参数。
- [ ] 验证嵌套函数、lambda、推导式和类 code object 都能遍历。
- [ ] 验证偏移量包含 inline cache 所占空间。

### 阶段 1 验收条件

- [ ] 3.11 `.pyc` 可以装载。
- [ ] 所有嵌套 code object 可以遍历。
- [ ] 原始指令 offset 和 jump target 正确。
- [ ] 不因 `SETUP_*`、`JUMP_ABSOLUTE`、`POP_BLOCK` 等属性不存在而崩溃。
- [ ] 3.7/3.8 基线测试没有新增失败。

---

## 阶段 2：3.11 指令规范化

### 2.1 建立规范化模型

建议新增：

```text
decompyle3/scanners/normalize311.py
decompyle3/ir.py
```

如果一个独立 `ir` 包能显著降低耦合，可改为：

```text
decompyle3/ir/
    __init__.py
    instruction.py
    call.py
    jump.py
```

实施时只选择一种结构，避免同时保留两个方案。

- [ ] 定义规范化指令的最小数据结构。
- [ ] 保留原始 opcode、规范化 kind、offset、target、参数和栈效果。
- [ ] 区分原始物理 offset 与过滤缓存后的逻辑序号。
- [ ] 定义统一的未知指令处理策略。

### 2.2 缓存和内部指令

- [ ] 识别并跳过 `CACHE`，但保留 offset 映射。
- [ ] 将 `RESUME` 标记为内部指令。
- [ ] 对磁盘 `.pyc` 和运行时 specialized 指令采取不同策略。
- [ ] specialized opcode 能反规范化时转为基础 opcode；不能时明确报错。
- [ ] 验证 jump target 不会落入缓存中间。

### 2.3 运算指令

- [ ] 根据参数展开 `BINARY_OP`。
- [ ] 区分普通运算和原地运算。
- [ ] 处理 `COMPARE_OP`、`CONTAINS_OP` 和 `IS_OP`。
- [ ] 处理 `COPY` 和 `SWAP`。
- [ ] 为每种运算建立 Token golden 测试。

### 2.4 调用协议

- [ ] 识别 `PUSH_NULL`。
- [ ] 识别 `KW_NAMES`。
- [ ] 识别并验证 `PRECALL`。
- [ ] 将 `CALL` 表示成统一调用 Token。
- [ ] 区分函数调用和方法调用。
- [ ] 处理位置参数、关键字参数、`*args` 和 `**kwargs`。
- [ ] 保留调用序列中 `NULL` 与 `self` 的语义差别。

建议统一结构：

```python
CallToken(
    argc=0,
    positional_count=0,
    keyword_names=(),
    is_method=False,
    has_null=False,
)
```

具体字段可根据现有 Token API 调整，但必须由测试固定。

### 2.5 跳转协议

- [ ] 处理所有前向条件跳转。
- [ ] 处理所有后向条件跳转。
- [ ] 处理 `JUMP_BACKWARD` 和 `JUMP_BACKWARD_NO_INTERRUPT`。
- [ ] 处理 `*_IF_NONE` 和 `*_IF_NOT_NONE`。
- [ ] 处理 `JUMP_IF_TRUE_OR_POP` 和 `JUMP_IF_FALSE_OR_POP`。
- [ ] 将相对参数解析成绝对 target offset。
- [ ] 给规范化跳转标注方向、条件和是否弹栈。

### 2.6 函数、闭包和局部变量

- [ ] 处理 `MAKE_CELL`。
- [ ] 处理 `COPY_FREE_VARS`。
- [ ] 处理 `LOAD_CLOSURE`、`LOAD_DEREF`、`STORE_DEREF`。
- [ ] 适配 3.11 的 locals-plus 索引。
- [ ] 处理新的 `MAKE_FUNCTION` 栈布局。
- [ ] 验证默认参数、注解和 closure flags。

### 阶段 2 验收条件

- [ ] 规范化 Token 流不包含未处理的 `CACHE`。
- [ ] 所有规范化 jump target 有效。
- [ ] 栈深度分析不会无故变成负数。
- [ ] 调用参数和关键字名称正确。
- [ ] 函数和闭包元数据正确。
- [ ] Scanner golden 测试全部通过。
- [ ] 3.7/3.8 基线测试没有新增失败。

---

## 阶段 3：无复杂控制流的源码恢复

### 3.1 新建 Parser311

计划新增：

```text
decompyle3/parsers/p311/__init__.py
decompyle3/parsers/p311/base.py
decompyle3/parsers/p311/full.py
decompyle3/parsers/p311/heads.py
decompyle3/parsers/p311/lambda_expr.py
decompyle3/parsers/p311/full_custom.py
```

- [ ] 注册 `Python311ParserExec`。
- [ ] 注册 `single`、`eval`、`expr` 和 `lambda` compile mode。
- [ ] 只继承仍然语义有效的 3.8 规则。
- [ ] 删除或覆盖依赖旧调用、旧异常和旧跳转模式的规则。
- [ ] 对尚未支持的控制流给出明确错误。

### 3.2 首批语法

- [ ] 常量和集合字面量。
- [ ] 一元、二元、比较和布尔表达式。
- [ ] 属性、下标和切片。
- [ ] 简单赋值和链式赋值。
- [ ] 增量赋值。
- [ ] import。
- [ ] return、raise 和 delete。
- [ ] 普通函数调用和方法调用。
- [ ] 函数和 lambda。
- [ ] 默认参数、位置专用参数、关键字专用参数和注解。
- [ ] 类、继承、方法和装饰器。
- [ ] f-string。

### 3.3 语义输出

计划新增或修改：

```text
decompyle3/semantics/customize311.py
decompyle3/semantics/make_function311.py
decompyle3/semantics/customize.py
```

- [ ] 尽量复用现有 `SourceWalker`。
- [ ] 处理 3.11 `MAKE_FUNCTION`。
- [ ] 处理 3.11 闭包和注解布局。
- [ ] 确保生成源码能被 Python 3.11 解析。

### 阶段 3 验收条件

- [ ] 简单模块可以生成源码。
- [ ] 每个结果通过 `ast.parse()`。
- [ ] 每个结果通过 `compile(..., "exec")`。
- [ ] 纯计算样本通过行为对比。
- [ ] 尚未支持的控制流不会输出伪正确源码。
- [ ] 3.7/3.8 基线测试没有新增失败。

---

## 阶段 4：CFG 和普通控制流恢复

### 4.1 建立控制流模块

计划新增：

```text
decompyle3/controlflow/__init__.py
decompyle3/controlflow/basicblock.py
decompyle3/controlflow/cfg.py
decompyle3/controlflow/dominators.py
decompyle3/controlflow/structures.py
```

- [ ] 根据入口、跳转目标和终结指令划分基础块。
- [ ] 建立 fall-through 边。
- [ ] 建立条件跳转边。
- [ ] 建立无条件跳转边。
- [ ] 标记 return、raise 和不可达块。
- [ ] 生成稳定、可调试的 CFG 文本表示。

### 4.2 图分析

- [ ] 计算 dominator。
- [ ] 计算 immediate dominator。
- [ ] 计算 post-dominator。
- [ ] 识别 back edge。
- [ ] 识别 natural loop。
- [ ] 识别 irreducible control flow 并明确报错。

### 4.3 结构恢复

- [ ] 简单 `if`。
- [ ] `if/else`。
- [ ] `if/elif/else`。
- [ ] 短路 `and/or`。
- [ ] 三元表达式。
- [ ] `while`。
- [ ] `for`。
- [ ] `break`。
- [ ] `continue`。
- [ ] `for/else`。
- [ ] `while/else`。
- [ ] 多层嵌套分支和循环。

### 4.4 与 Parser 集成

- [ ] 决定 CFG 直接生成结构化节点，还是生成兼容 `COME_FROM` Token。
- [ ] 将选定方案写入代码注释和执行记录。
- [ ] 禁止同时维护两套未经测试的结构恢复路径。
- [ ] 保留 CFG 调试输出选项。

### 阶段 4 验收条件

- [ ] 控制流测试覆盖所有上述结构。
- [ ] `break` 和 `continue` 不混淆。
- [ ] loop-else 正确。
- [ ] 死代码不会错误并入循环。
- [ ] 嵌套控制流生成源码可解析、可编译并通过行为验证。
- [ ] 3.7/3.8 基线测试没有新增失败。

---

## 阶段 5：推导式、生成器和协程

### 5.1 推导式

- [ ] list comprehension。
- [ ] set comprehension。
- [ ] dict comprehension。
- [ ] generator expression。
- [ ] 多个 `for`。
- [ ] 多个 `if`。
- [ ] 嵌套推导式。
- [ ] async comprehension。

### 5.2 生成器和协程协议

- [ ] `RETURN_GENERATOR`。
- [ ] `YIELD_VALUE`。
- [ ] `SEND`。
- [ ] `ASYNC_GEN_WRAP`。
- [ ] `GET_AWAITABLE`。
- [ ] `GET_ANEXT`。
- [ ] `END_ASYNC_FOR`。
- [ ] `yield from`。
- [ ] `await`。
- [ ] async generator。

### 阶段 5 验收条件

- [ ] 各类推导式可解析和重新编译。
- [ ] 推导式变量作用域正确。
- [ ] lambda 与推导式嵌套正确。
- [ ] generator 和 coroutine 样本通过行为验证。
- [ ] 3.7/3.8 基线测试没有新增失败。

---

## 阶段 6：异常表、异常语句和 `with`

### 6.1 解码异常表

计划新增：

```text
decompyle3/controlflow/exceptiontable311.py
decompyle3/controlflow/exception_regions.py
```

- [ ] 解码 `co_exceptiontable`。
- [ ] 表示 start、end、target、depth 和 lasti。
- [ ] 对照 Python 3.11 `dis` 的 ExceptionTable 输出。
- [ ] 验证所有保护范围和 handler target。
- [ ] 将异常边加入 CFG。

建议数据结构：

```python
ExceptionRegion(
    start=0,
    end=0,
    target=0,
    depth=0,
    lasti=False,
)
```

### 6.2 异常指令

- [ ] `PUSH_EXC_INFO`。
- [ ] `CHECK_EXC_MATCH`。
- [ ] `POP_EXCEPT`。
- [ ] `RERAISE`。
- [ ] 异常状态在栈上的单对象表示。

### 6.3 异常结构

- [ ] `try/except`。
- [ ] 多个 `except`。
- [ ] `except ... as ...`。
- [ ] `try/else`。
- [ ] `try/finally`。
- [ ] `try/except/finally`。
- [ ] 嵌套异常结构。
- [ ] finally 中的 return、break 和 continue。

### 6.4 上下文管理器

- [ ] `BEFORE_WITH`。
- [ ] `WITH_EXCEPT_START`。
- [ ] 普通 `with`。
- [ ] 多个 context manager。
- [ ] 嵌套 `with`。
- [ ] `async with`。

### 阶段 6 验收条件

- [ ] 异常表解码与标准 `dis` 一致。
- [ ] 异常 CFG 不丢失正常路径或 handler 路径。
- [ ] try、except、else、finally 边界正确。
- [ ] with 的正常退出和异常退出正确。
- [ ] 不确定的异常结构会明确报错。
- [ ] 3.7/3.8 基线测试没有新增失败。

---

## 阶段 7：3.10/3.11 新语法

### 7.1 `match/case`

- [ ] `MATCH_MAPPING`。
- [ ] `MATCH_SEQUENCE`。
- [ ] `MATCH_KEYS`。
- [ ] `MATCH_CLASS`。
- [ ] literal pattern。
- [ ] capture pattern。
- [ ] wildcard pattern。
- [ ] sequence pattern。
- [ ] mapping pattern。
- [ ] class pattern。
- [ ] OR pattern。
- [ ] `case` guard。
- [ ] 嵌套 pattern。

### 7.2 `except*`

- [ ] `CHECK_EG_MATCH`。
- [ ] `PREP_RERAISE_STAR`。
- [ ] 单个 `except*`。
- [ ] 多个 `except*`。
- [ ] ExceptionGroup 子组拆分和重新抛出。
- [ ] 确保 `except` 与 `except*` 不混淆。

### 阶段 7 验收条件

- [ ] 官方风格的 pattern matching 样本可反编译。
- [ ] pattern 变量绑定和 guard 正确。
- [ ] `except*` 输出语法正确。
- [ ] match 和 ExceptionGroup 行为测试通过。
- [ ] 3.7/3.8 基线测试没有新增失败。

---

## 阶段 8：回归、可靠性、文档和发布准备

### 8.1 测试层级

- [ ] Scanner 单元测试。
- [ ] Token golden 测试。
- [ ] CFG 单元测试。
- [ ] 异常表单元测试。
- [ ] Parser 语法树测试。
- [ ] 源码生成测试。
- [ ] `ast.parse` 验证。
- [ ] 重新编译验证。
- [ ] 原程序与反编译程序行为对比。
- [ ] 3.7/3.8 全量回归。
- [ ] Python 3.11 标准库子集测试。
- [ ] 大函数、深层嵌套和长集合压力测试。

### 8.2 错误类型

根据现有异常体系调整命名，至少覆盖：

```text
UnsupportedVersionError
UnsupportedOpcodeError
MalformedBytecodeError
ControlFlowError
ExceptionTableError
ParserError
SemanticGenerationError
VerificationError
```

- [ ] 错误中包含目标版本、code object 名称和 offset。
- [ ] CLI 失败时返回非零状态。
- [ ] 批量处理时单个失败不会隐藏其他结果。
- [ ] 不完整输出文件要明确标记或清理。

### 8.3 文档

- [ ] 更新 README 支持版本。
- [ ] 更新 CLI 帮助。
- [ ] 说明“运行 Python 版本”和“目标字节码版本”的区别。
- [ ] 列出已支持语法。
- [ ] 列出已知限制。
- [ ] 更新 bug 报告指南。
- [ ] 记录如何生成最小复现 `.py` 和 `.pyc`。
- [ ] 检查 GPL-3.0-or-later 分发要求。

### 8.4 最终验收

- [ ] 3.11 测试语料全部通过。
- [ ] 3.7/3.8 没有新增失败。
- [ ] 不支持结构可以被明确检测。
- [ ] CLI 可以批量反编译 3.11 `.pyc`。
- [ ] 生成源码通过语法和重新编译验证。
- [ ] 行为测试结果已记录。
- [ ] 已知问题清单已完成。
- [ ] 在用户明确要求后再进行提交、推送或发布。

---

## 5. 每次继续执行时的恢复流程

后续执行者或自动化代理必须：

1. 完整阅读本文档。
2. 运行 `git status --short`，识别已有和用户修改。
3. 查看“总体状态”和“执行记录”。
4. 找到第一个未完成的阶段和复选框。
5. 运行该阶段已经存在的相关测试。
6. 只修改当前阶段必要文件。
7. 完成实现后运行阶段验收测试及 3.7/3.8 回归测试。
8. 只有验证成功后才勾选任务并更新总体状态。
9. 在“执行记录”中写入证据，包括命令、测试数量和已知限制。
10. 若阻塞，将状态改为“阻塞”，记录具体错误和恢复条件，禁止跳过后假装完成。

## 6. 阶段报告模板

每个阶段结束时使用以下格式：

```markdown
### YYYY-MM-DD：阶段 N

- 状态：已完成 / 阻塞
- 修改文件：
  - `path/to/file.py`
- 已实现：
  - ...
- 验证命令：
  - `python -m pytest ...`
- 验证结果：
  - `N passed, M skipped`
- 3.7/3.8 回归：
  - ...
- 已知限制：
  - ...
- 下一步：
  - 阶段 N+1，第一个未完成项
```

## 7. 关键停止条件

出现以下情况时必须停止扩大修改范围，先解决或报告：

- `xdis` 无法正确装载目标 3.11 `.pyc`。
- 规范化后出现无法解释的跳转目标或负栈深度。
- CFG 无法区分两个语义不同的源代码结构。
- 异常表解码与标准 `dis` 不一致。
- 反编译结果通过语法检查但行为明显不同。
- 3.7/3.8 出现新增回归。
- 工作区存在与当前任务冲突的用户修改。
- 完成下一步需要联网、安装系统依赖或执行超出当前授权范围的操作。

## 8. 主要风险

| 风险 | 影响 | 应对方式 |
|---|---|---|
| 旧 Scanner 基类引用已删除 opcode | Scanner311 无法初始化 | 新建 3.11 基类或拆分版本无关逻辑 |
| `CACHE` 破坏 offset 计算 | 跳转和行号错误 | 保留物理 offset，单独维护逻辑序号 |
| 调用协议判断错误 | 参数、方法调用错误 | 统一 CallToken 并做栈验证 |
| 旧 `COME_FROM` 模式不足 | 控制流误判 | 引入 CFG、dominator 和 post-dominator |
| 异常表分析错误 | try/with 结构错误 | 与标准 `dis` 对照并加入异常边测试 |
| Parser 规则过度继承 3.8 | 误解析而不报错 | 只继承经过测试的规则，默认 fail closed |
| 语法正确但语义错误 | 产生危险的伪正确源码 | 加入行为对比和明确的不支持错误 |
| 同时修改过多层 | 难以定位失败 | 严格按阶段和最小测试样本推进 |

## 9. 执行记录

当前尚未开始代码实现。

