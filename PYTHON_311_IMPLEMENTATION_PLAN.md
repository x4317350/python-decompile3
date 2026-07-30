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
| 0 | 环境、基线和测试语料 | 已完成 |
| 1 | 3.11 `.pyc` 装载和原始扫描 | 已完成 |
| 2 | 3.11 指令规范化 | 已完成 |
| 3 | 无复杂控制流的源码恢复 | 已完成 |
| 4 | CFG 和普通控制流恢复 | 已完成 |
| 5 | 推导式、生成器和协程 | 已完成 |
| 6 | 异常表、异常语句和 `with` | 已完成 |
| 7 | `match/case`、`except*` 等新语法 | 已完成 |
| 8 | 回归、可靠性、文档和发布准备 | 未开始 |

允许的状态只有：`未开始`、`进行中`、`已完成`、`阻塞`。

---

## 阶段 0：环境、基线和测试语料

### 0.1 记录修改前状态

- [x] 记录当前 Git 提交和工作区状态。
- [x] 记录当前 Python、操作系统和依赖版本。
- [x] 确认工作区中的已有修改，禁止覆盖无关用户改动。
- [x] 建立独立的 Python 3.11 开发环境。
- [x] 安装项目及测试依赖。
- [x] 确认所用 `xdis` 能读取 CPython 3.11 `.pyc`。
- [x] 如需提高 `xdis` 最低版本，先通过测试确认，再修改 `pyproject.toml`。（当前无需提高最低版本。）

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

- [x] 运行当前 pytest 测试。
- [x] 运行项目现有 `make check`。
- [x] 记录现有失败、跳过和预期失败，不能把历史失败归因于 3.11 修改。
- [x] 保存一份简短基线报告到 `test/bytecode_3.11/BASELINE.md`。

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

- [x] 创建测试目录。
- [x] 编写生成 `.pyc` 的可重复脚本或 Makefile 目标。
- [x] 保留原始 `.py`，必要时生成 `.pyc`，避免只提交不可审查的二进制样本。
- [x] 为 Token 测试建立可审查的 golden 输出格式。
- [x] 为行为验证建立统一辅助函数。

### 0.4 建立最小语法语料

- [x] 常量、赋值和删除。
- [x] 一元、二元和原地运算。
- [x] 属性、下标和切片。
- [x] 位置参数和关键字参数调用。
- [x] 函数、lambda、默认参数和注解。
- [x] 类、继承、方法和装饰器。
- [x] `if/elif/else` 和布尔短路。
- [x] `for/while`、`break/continue` 和 loop-else。
- [x] list/set/dict comprehension 和 generator expression。
- [x] `yield`、`yield from` 和 `await`。
- [x] `try/except/else/finally`。
- [x] `with` 和 `async with`。
- [x] `match/case`。
- [x] `except*` 和 ExceptionGroup。

### 阶段 0 验收条件

- [x] 现有测试基线已记录。
- [x] 3.11 测试语料可以重复生成。
- [x] 测试能够区分装载、扫描、解析、源码生成和行为验证失败。
- [x] 原始工作区无关文件未被修改。

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

- [x] 在 Scanner 版本注册表中增加 `(3, 11)`。
- [x] 增加 `Scanner311` 动态分派。
- [x] 调整 CLI 的支持版本说明。
- [x] Parser 尚未实现时，应返回清晰的“Scanner 已支持、Parser 未支持”错误。

### 1.2 新建原始 Scanner311

计划新增：

```text
decompyle3/scanners/scanner311.py
```

- [x] 使用 `xdis` 的 CPython 3.11 opcode 定义。
- [x] 不直接调用依赖已删除 opcode 的 `Scanner37Base.__init__`。
- [x] 读取所有原始指令及真实 byte offset。
- [x] 建立 `offset -> instruction index` 映射。
- [x] 读取嵌套 code object。
- [x] 读取行号、位置信息和 code object 元数据。
- [x] 暂存原始 `co_exceptiontable`，本阶段不要求恢复异常语句。
- [x] 为未知或畸形指令提供明确错误。

### 1.3 原始指令测试

- [x] 原始 Scanner 输出与 Python 3.11 `dis.get_instructions()` 对照。
- [x] 验证跳转目标、常量、名称和参数。
- [x] 验证嵌套函数、lambda、推导式和类 code object 都能遍历。
- [x] 验证偏移量包含 inline cache 所占空间。

### 阶段 1 验收条件

- [x] 3.11 `.pyc` 可以装载。
- [x] 所有嵌套 code object 可以遍历。
- [x] 原始指令 offset 和 jump target 正确。
- [x] 不因 `SETUP_*`、`JUMP_ABSOLUTE`、`POP_BLOCK` 等属性不存在而崩溃。
- [x] 3.7/3.8 基线测试没有新增失败。

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

- [x] 定义规范化指令的最小数据结构。
- [x] 保留原始 opcode、规范化 kind、offset、target、参数和栈效果。
- [x] 区分原始物理 offset 与过滤缓存后的逻辑序号。
- [x] 定义统一的未知指令处理策略。

### 2.2 缓存和内部指令

- [x] 识别并跳过 `CACHE`，但保留 offset 映射。
- [x] 将 `RESUME` 标记为内部指令。
- [x] 对磁盘 `.pyc` 和运行时 specialized 指令采取不同策略。
- [x] specialized opcode 能反规范化时转为基础 opcode；不能时明确报错。
- [x] 验证 jump target 不会落入缓存中间。

### 2.3 运算指令

- [x] 根据参数展开 `BINARY_OP`。
- [x] 区分普通运算和原地运算。
- [x] 处理 `COMPARE_OP`、`CONTAINS_OP` 和 `IS_OP`。
- [x] 处理 `COPY` 和 `SWAP`。
- [x] 为每种运算建立 Token golden 测试。

### 2.4 调用协议

- [x] 识别 `PUSH_NULL`。
- [x] 识别 `KW_NAMES`。
- [x] 识别并验证 `PRECALL`。
- [x] 将 `CALL` 表示成统一调用 Token。
- [x] 区分函数调用和方法调用。
- [x] 处理位置参数、关键字参数、`*args` 和 `**kwargs`。
- [x] 保留调用序列中 `NULL` 与 `self` 的语义差别。

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

- [x] 处理所有前向条件跳转。
- [x] 处理所有后向条件跳转。
- [x] 处理 `JUMP_BACKWARD` 和 `JUMP_BACKWARD_NO_INTERRUPT`。
- [x] 处理 `*_IF_NONE` 和 `*_IF_NOT_NONE`。
- [x] 处理 `JUMP_IF_TRUE_OR_POP` 和 `JUMP_IF_FALSE_OR_POP`。
- [x] 将相对参数解析成绝对 target offset。
- [x] 给规范化跳转标注方向、条件和是否弹栈。

### 2.6 函数、闭包和局部变量

- [x] 处理 `MAKE_CELL`。
- [x] 处理 `COPY_FREE_VARS`。
- [x] 处理 `LOAD_CLOSURE`、`LOAD_DEREF`、`STORE_DEREF`。
- [x] 适配 3.11 的 locals-plus 索引。
- [x] 处理新的 `MAKE_FUNCTION` 栈布局。
- [x] 验证默认参数、注解和 closure flags。

### 阶段 2 验收条件

- [x] 规范化 Token 流不包含未处理的 `CACHE`。
- [x] 所有规范化 jump target 有效。
- [x] 栈深度分析不会无故变成负数。
- [x] 调用参数和关键字名称正确。
- [x] 函数和闭包元数据正确。
- [x] Scanner golden 测试全部通过。
- [x] 3.7/3.8 基线测试没有新增失败。

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

- [x] 注册 `Python311ParserExec`。
- [x] 注册 `single`、`eval`、`expr` 和 `lambda` compile mode。
- [x] 只继承仍然语义有效的 3.8 规则。
- [x] 删除或覆盖依赖旧调用、旧异常和旧跳转模式的规则。
- [x] 对尚未支持的控制流给出明确错误。

### 3.2 首批语法

- [x] 常量和集合字面量。
- [x] 一元、二元、比较和布尔表达式。
- [x] 属性、下标和切片。
- [x] 简单赋值和链式赋值。
- [x] 增量赋值。
- [x] import。
- [x] return、raise 和 delete。
- [x] 普通函数调用和方法调用。
- [x] 函数和 lambda。
- [x] 默认参数、位置专用参数、关键字专用参数和注解。
- [x] 类、继承、方法和装饰器。
- [x] f-string。

### 3.3 语义输出

计划新增或修改：

```text
decompyle3/semantics/customize311.py
decompyle3/semantics/make_function311.py
decompyle3/semantics/customize.py
```

- [x] 尽量复用现有 `SourceWalker`。
- [x] 处理 3.11 `MAKE_FUNCTION`。
- [x] 处理 3.11 闭包和注解布局。
- [x] 确保生成源码能被 Python 3.11 解析。

### 阶段 3 验收条件

- [x] 简单模块可以生成源码。
- [x] 每个结果通过 `ast.parse()`。
- [x] 每个结果通过 `compile(..., "exec")`。
- [x] 纯计算样本通过行为对比。
- [x] 尚未支持的控制流不会输出伪正确源码。
- [x] 3.7/3.8 基线测试没有新增失败。

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

- [x] 根据入口、跳转目标和终结指令划分基础块。
- [x] 建立 fall-through 边。
- [x] 建立条件跳转边。
- [x] 建立无条件跳转边。
- [x] 标记 return、raise 和不可达块。
- [x] 生成稳定、可调试的 CFG 文本表示。

### 4.2 图分析

- [x] 计算 dominator。
- [x] 计算 immediate dominator。
- [x] 计算 post-dominator。
- [x] 识别 back edge。
- [x] 识别 natural loop。
- [x] 识别 irreducible control flow 并明确报错。

### 4.3 结构恢复

- [x] 简单 `if`。
- [x] `if/else`。
- [x] `if/elif/else`。
- [x] 短路 `and/or`。
- [x] 三元表达式。
- [x] `while`。
- [x] `for`。
- [x] `break`。
- [x] `continue`。
- [x] `for/else`。
- [x] `while/else`。
- [x] 多层嵌套分支和循环。

### 4.4 与 Parser 集成

- [x] 决定 CFG 直接生成结构化节点，还是生成兼容 `COME_FROM` Token。
- [x] 将选定方案写入代码注释和执行记录。
- [x] 禁止同时维护两套未经测试的结构恢复路径。
- [x] 保留 CFG 调试输出选项。

### 阶段 4 验收条件

- [x] 控制流测试覆盖所有上述结构。
- [x] `break` 和 `continue` 不混淆。
- [x] loop-else 正确。
- [x] 死代码不会错误并入循环。
- [x] 嵌套控制流生成源码可解析、可编译并通过行为验证。
- [x] 3.7/3.8 基线测试没有新增失败。

---

## 阶段 5：推导式、生成器和协程

### 5.1 推导式

- [x] list comprehension。
- [x] set comprehension。
- [x] dict comprehension。
- [x] generator expression。
- [x] 多个 `for`。
- [x] 多个 `if`。
- [x] 嵌套推导式。
- [x] async comprehension。

### 5.2 生成器和协程协议

- [x] `RETURN_GENERATOR`。
- [x] `YIELD_VALUE`。
- [x] `SEND`。
- [x] `ASYNC_GEN_WRAP`。
- [x] `GET_AWAITABLE`。
- [x] `GET_ANEXT`。
- [x] `END_ASYNC_FOR`。
- [x] `yield from`。
- [x] `await`。
- [x] async generator。

### 阶段 5 验收条件

- [x] 各类推导式可解析和重新编译。
- [x] 推导式变量作用域正确。
- [x] lambda 与推导式嵌套正确。
- [x] generator 和 coroutine 样本通过行为验证。
- [x] 3.7/3.8 基线测试没有新增失败。

---

## 阶段 6：异常表、异常语句和 `with`

### 6.1 解码异常表

计划新增：

```text
decompyle3/controlflow/exceptiontable311.py
decompyle3/controlflow/exception_regions.py
```

- [x] 解码 `co_exceptiontable`。
- [x] 表示 start、end、target、depth 和 lasti。
- [x] 对照 Python 3.11 `dis` 的 ExceptionTable 输出。
- [x] 验证所有保护范围和 handler target。
- [x] 将异常边加入 CFG。

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

- [x] `PUSH_EXC_INFO`。
- [x] `CHECK_EXC_MATCH`。
- [x] `POP_EXCEPT`。
- [x] `RERAISE`。
- [x] 异常状态在栈上的单对象表示。

### 6.3 异常结构

- [x] `try/except`。
- [x] 多个 `except`。
- [x] `except ... as ...`。
- [x] `try/else`。
- [x] `try/finally`。
- [x] `try/except/finally`。
- [x] 嵌套异常结构。
- [x] finally 中的 return、break 和 continue。

### 6.4 上下文管理器

- [x] `BEFORE_WITH`。
- [x] `WITH_EXCEPT_START`。
- [x] 普通 `with`。
- [x] 多个 context manager。
- [x] 嵌套 `with`。
- [x] `async with`。

### 阶段 6 验收条件

- [x] 异常表解码与标准 `dis` 一致。
- [x] 异常 CFG 不丢失正常路径或 handler 路径。
- [x] try、except、else、finally 边界正确。
- [x] with 的正常退出和异常退出正确。
- [x] 不确定的异常结构会明确报错。
- [x] 3.7/3.8 基线测试没有新增失败。

---

## 阶段 7：3.10/3.11 新语法

### 7.1 `match/case`

- [x] `MATCH_MAPPING`。
- [x] `MATCH_SEQUENCE`。
- [x] `MATCH_KEYS`。
- [x] `MATCH_CLASS`。
- [x] literal pattern。
- [x] capture pattern。
- [x] wildcard pattern。
- [x] sequence pattern。
- [x] mapping pattern。
- [x] class pattern。
- [x] OR pattern。
- [x] `case` guard。
- [x] 嵌套 pattern。

### 7.2 `except*`

- [x] `CHECK_EG_MATCH`。
- [x] `PREP_RERAISE_STAR`。
- [x] 单个 `except*`。
- [x] 多个 `except*`。
- [x] ExceptionGroup 子组拆分和重新抛出。
- [x] 确保 `except` 与 `except*` 不混淆。

### 阶段 7 验收条件

- [x] 官方风格的 pattern matching 样本可反编译。
- [x] pattern 变量绑定和 guard 正确。
- [x] `except*` 输出语法正确。
- [x] match 和 ExceptionGroup 行为测试通过。
- [x] 3.7/3.8 基线测试没有新增失败。

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

### 2026-07-30：阶段 0

- 状态：已完成
- 基线提交：
  - `78b1d89e402ff9a94e309be73213ccec0c7aee53`
- 修改文件：
  - `pyproject.toml`
  - `pytest/support311.py`
  - `pytest/test_corpus311.py`
  - `pytest/test_scanner311.py`
  - `pytest/test_deparse311.py`
  - `pytest/test_controlflow311.py`
  - `pytest/test_exceptiontable311.py`
  - `test/simple_source/311/*.py`
  - `test/bytecode_3.11/*`
- 已实现：
  - 创建 CPython 3.11.9 独立虚拟环境并安装完整开发依赖。
  - 验证 `xdis 6.3.0` 可以装载版本 `(3, 11)`、magic `3495` 的 `.pyc`。
  - 建立 9 份覆盖基础语法、控制流、推导式、异步、异常表、
    `match/case` 和 `except*` 的源码语料。
  - 建立 hash-based `.pyc` 和稳定标准 `dis` golden 生成器。
  - 建立 Scanner、Parser、CFG 和异常表的分阶段 pytest 入口。
  - 将 pytest 收集范围限制到 `pytest/`，不再误收集 Python 2 源码语料。
- 验证命令：
  - `.venv311/bin/python test/bytecode_3.11/generate.py`
  - `.venv311/bin/python test/bytecode_3.11/generate.py --check`
  - `.venv311/bin/python -m pytest pytest/test_corpus311.py -q`
  - `.venv311/bin/python -m pytest -q`
  - `make check PYTHON=/absolute/path/to/.venv311/bin/python`
- 验证结果：
  - 生成并检查 9 份 CPython 3.11 corpus：成功。
  - corpus 测试：`3 passed`。
  - 全部 pytest：`1 failed, 7 passed, 20 skipped`。
  - `make check`：在相同的单一基线失败处停止。
- 3.7/3.8 回归：
  - 阶段 0 没有引入新的 Scanner 失败。
  - 当前基线中 3.7/3.8 Scanner 因 `xdis 6.3.0` opcode 模块迁移而无法导入。
- 已知限制：
  - `xdis 6.3.0` 将 3.x opcode 模块移动到
    `xdis.opcodes.opcode_3x`，现有 Scanner 仍使用旧导入路径。
  - Scanner311 尚未实现，相关阶段测试按计划跳过。
  - Token golden 当前只固定格式，实际规范化 Token 文件将在阶段 2 生成。
- 下一步：
  - 阶段 1：先恢复现有 Scanner 对 `xdis 6.3.0` 的兼容导入，
    再注册 `(3, 11)` 并实现原始 `Scanner311`。

### 2026-07-30：阶段 1

- 状态：已完成
- 修改文件：
  - `decompyle3/scanner.py`
  - `decompyle3/scanners/scanner37base.py`
  - `decompyle3/scanners/scanner311.py`
  - `decompyle3/parsers/main.py`
  - `decompyle3/bin/decompile.py`
  - `pytest/test_scanner311.py`
  - `test/simple_source/311/01_functions_classes.py`
  - `test/bytecode_3.11/golden/01_functions_classes.dis`
  - `test/bytecode_3.11/golden_tokens/README.md`
- 已实现：
  - 改用 `xdis.op_imports.get_opcode_module()` 解析 3.7、3.8 和 3.11
    opcode 表，修复 `xdis 6.3.0` 模块迁移造成的现有 Scanner 导入失败。
  - 注册 CPython 3.11 Scanner，并对 PyPy 3.11 返回明确的不支持错误。
  - 新增独立于 `Scanner37Base` 的原始 `Scanner311`，保留 `CACHE` 和
    `EXTENDED_ARG` 物理指令、两字节 offset、原始参数字节及 offset 索引。
  - 遍历所有嵌套 code object，并保存行号、位置信息、code object
    元数据、原始 `co_exceptiontable` 和 `xdis` 解码后的异常表项。
  - 新增未知 opcode 和畸形字节码错误；Parser311 未实现时返回明确错误。
  - 增加 lambda 语料，并用标准库 `dis` 更新对应 golden。
  - 明确原始 Scanner 对照测试属于阶段 1，过滤 `CACHE` 后的规范化
    Token golden 留在阶段 2 生成。
- 验证命令：
  - `.venv311/bin/python test/bytecode_3.11/generate.py`
  - `.venv311/bin/python test/bytecode_3.11/generate.py --check`
  - `.venv311/bin/pytest -q pytest/test_scanner311.py`
  - `.venv311/bin/pytest -q`
  - `make check PYTHON=/absolute/path/to/.venv311/bin/python`
  - `make -C test check-bytecode-3.8 PYTHON=/absolute/path/to/.venv311/bin/python`
  - `.venv311/bin/flake8 decompyle3/scanner.py decompyle3/scanners/scanner37base.py decompyle3/scanners/scanner311.py decompyle3/parsers/main.py decompyle3/bin/decompile.py pytest/test_scanner311.py`
- 验证结果：
  - Scanner311 阶段测试：`5 passed`。
  - 全部 pytest：`13 passed, 19 skipped`。
  - 3.7 bytecode 回归：`54 okay, 0 failed, 0 failed verification`。
  - 3.8 bytecode 回归：`48 okay, 0 failed, 0 failed verification`。
  - flake8 和生成物一致性检查：通过。
- 3.7/3.8 回归：
  - 阶段 0 的 `xdis 6.3.0` 导入失败已修复。
  - 3.7 和 3.8 现有回归语料全部成功反编译并通过可执行范围内的验证。
- 已知限制：
  - Scanner311 当前输出未经规范化的物理 Token 流，仍包含 `CACHE`、
    `RESUME`、`PRECALL` 等 3.11 内部指令。
  - Parser311 尚未实现，因此 CLI 可以装载和扫描 3.11 `.pyc`，但不会
    输出反编译源码。
  - 本阶段只保存和解码异常表，不恢复 `try`、`with` 等异常控制流。
- 下一步：
  - 阶段 2：定义 3.11 规范化指令模型，建立 CACHE/offset 映射，
    再处理运算、调用和跳转协议。

### 2026-07-30：阶段 2

- 状态：已完成
- 修改文件：
  - `decompyle3/ir.py`
  - `decompyle3/scanner.py`
  - `decompyle3/scanners/normalize311.py`
  - `decompyle3/scanners/scanner311.py`
  - `pytest/test_scanner311.py`
  - `pytest/test_normalize311.py`
  - `test/simple_source/311/00_expressions.py`
  - `test/simple_source/311/02_control_flow.py`
  - `test/bytecode_3.11/generate.py`
  - `test/bytecode_3.11/golden/*.dis`
  - `test/bytecode_3.11/golden_tokens/*.tokens`
  - `test/bytecode_3.11/README.md`
  - `test/bytecode_3.11/golden_tokens/README.md`
- 已实现：
  - 新增 `NormalizedInstruction`、`CallInfo`、`FunctionInfo` 和
    `StackAnalysis` 最小 IR，保留原始 opcode、物理 offset、逻辑序号、
    参数、绝对 target 和分支栈效果。
  - 将 `Scanner311.ingest_raw()` 固定为阶段 1 物理流接口；
    `Scanner311.ingest()` 返回不含 `CACHE` 的规范化 Token，并维护双向
    physical/logical offset 与 cache-owner 映射。
  - 展开全部 26 种 `BINARY_OP`，规范化比较、包含、身份、`COPY` 和
    `SWAP` 指令。
  - 建立统一 `CallInfo`，验证 `KW_NAMES -> PRECALL -> CALL` 协议，
    区分普通 NULL 调用和 `LOAD_METHOD` self-or-null 调用，并覆盖位置、
    关键字、`*args` 和 `**kwargs`。
  - 规范化所有 3.11 前向、后向和 None 条件跳转，记录方向、条件、
    是否弹栈及绝对 target；拒绝跳入 `CACHE`。
  - 解析 3.11 locals-plus 和 `MAKE_FUNCTION` flags，保留 defaults、
    kwdefaults、annotations 和 closure names。
  - 对磁盘 `.pyc` 默认拒绝 specialized opcode；对实时 CPython 3.11
    adaptive 指令流反规范化已知 specialized opcode，未知项明确失败。
  - 建立含异常入口和生成器恢复入口的栈深度数据流检查；9 份 corpus
    的全部可达深度均非负且不超过 `co_stacksize`。
  - 生成并跟踪 9 份稳定规范化 Token golden，共 1613 行。
- 验证命令：
  - `.venv311/bin/python test/bytecode_3.11/generate.py`
  - `.venv311/bin/python test/bytecode_3.11/generate.py --check`
  - `.venv311/bin/pytest -q pytest/test_normalize311.py`
  - `.venv311/bin/pytest -q`
  - `make check PYTHON=/absolute/path/to/.venv311/bin/python`
  - `make -C test check-bytecode-3.8 PYTHON=/absolute/path/to/.venv311/bin/python`
  - `.venv311/bin/flake8 decompyle3/ir.py decompyle3/scanner.py decompyle3/scanners/normalize311.py decompyle3/scanners/scanner311.py pytest/test_scanner311.py pytest/test_normalize311.py test/bytecode_3.11/generate.py`
- 验证结果：
  - 阶段 2 规范化测试：`8 passed`。
  - 全部 pytest：`21 passed, 19 skipped`。
  - golden 生成与一致性检查：9 份源码、9 份 dis、9 份 Token 均通过。
  - 3.7 bytecode 回归：`54 okay, 0 failed, 0 failed verification`。
  - 3.8 bytecode 回归：`48 okay, 0 failed, 0 failed verification`。
  - flake8、`git diff --check`：通过。
- 3.7/3.8 回归：
  - 原 Scanner 与 Parser 路径未改用 3.11 IR。
  - 3.7 和 3.8 现有回归语料全部成功反编译，未新增失败。
- 已知限制：
  - `RESUME`、`PUSH_NULL`、`KW_NAMES`、`PRECALL` 和 `EXTENDED_ARG`
    仍作为标记过的 internal Token 保留，阶段 3 Parser 应显式忽略或消费。
  - `LOAD_METHOD` 的第二协议槽在运行时可能是 self 或 NULL，因此
    `receiver_mode` 保留为 `self_or_null`，不做不可靠的静态猜测。
  - 当前栈分析只验证深度一致性；CFG 结构化、异常区间语义恢复和
    post-dominator 分别留在阶段 4 和阶段 6。
  - Parser311 尚未实现，仍不会输出 3.11 反编译源码。
- 下一步：
  - 阶段 3：注册 Parser311，从无复杂控制流的表达式、赋值、调用、
    函数和类开始恢复源码，并用 `ast.parse()` 和重新编译验证。

### 2026-07-30：阶段 3

- 状态：已完成
- 修改文件：
  - `decompyle3/parsers/p311/*.py`
  - `decompyle3/parsers/main.py`
  - `decompyle3/semantics/customize311.py`
  - `decompyle3/semantics/make_function311.py`
  - `decompyle3/semantics/pysource.py`
  - `decompyle3/bin/decompile.py`
  - `pytest/test_deparse311.py`
  - `pytest/test_scanner311.py`
  - `pytest/test_corpus311.py`
  - `test/simple_source/311/09_straight_line.py`
  - `test/bytecode_3.11/golden/09_straight_line.dis`
  - `test/bytecode_3.11/golden_tokens/09_straight_line.tokens`
- 已实现：
  - 注册 CPython 3.11 的 `exec`、`single`、`eval`、`expr` 和
    `lambda` Parser 入口；3.11 路径不混入依赖旧 CALL、SETUP、异常和
    跳转协议的 3.8 Spark 规则。
  - 建立基于阶段 2 规范化 Token 的直线型栈解析器，生成标准库
    `ast`，再由 `ast.unparse()` 输出语义等价源码。
  - 恢复常量与集合、运算和比较、封闭的 `and/or` 短路表达式、
    属性/下标/切片、链式/解包/增量赋值、import、return、raise、
    delete、普通/方法/扩展调用及 f-string。
  - 恢复 3.11 `MAKE_FUNCTION` 栈布局、闭包、默认值、位置专用参数、
    关键字专用参数、可变参数、注解、lambda、函数/类装饰器、继承和
    方法。
  - 通过 `Python311SourceWalker` 复用现有输出、调试和 CLI 管线。
  - 对普通分支/循环、推导式、生成器/协程、异常表、`with`、
    `match/case` 和 `except*` fail closed，并报告 code object、opcode
    与物理 offset。
  - 新增第 10 份阶段 3 直线型 corpus、标准 dis 和规范化 Token
    golden；对磁盘 `.pyc` 执行解析、重编译和行为对比。
- 验证命令：
  - `.venv311/bin/python test/bytecode_3.11/generate.py`
  - `.venv311/bin/python test/bytecode_3.11/generate.py --check`
  - `.venv311/bin/pytest -q pytest/test_deparse311.py`
  - `.venv311/bin/pytest -q`
  - `make check PYTHON=/absolute/path/to/.venv311/bin/python`
  - `make -C test check-bytecode-3.8 PYTHON=/absolute/path/to/.venv311/bin/python`
  - `.venv311/bin/flake8 decompyle3/parsers/p311 decompyle3/parsers/main.py decompyle3/semantics/customize311.py decompyle3/semantics/make_function311.py decompyle3/semantics/pysource.py decompyle3/bin/decompile.py pytest/test_deparse311.py pytest/test_scanner311.py pytest/test_corpus311.py test/bytecode_3.11/generate.py test/simple_source/311/09_straight_line.py`
- 验证结果：
  - 阶段 3 源码恢复测试：`8 passed`。
  - 全部 pytest：`29 passed, 18 skipped`。
  - golden 生成与一致性检查：10 份源码、dis 和 Token 均通过。
  - 3.7 bytecode 回归：`54 okay, 0 failed, 0 failed verification`。
  - 3.8 bytecode 回归：`48 okay, 0 failed, 0 failed verification`。
  - flake8、`git diff --check`：通过。
- 3.7/3.8 回归：
  - 旧版本继续使用原有 Scanner、Spark Parser 和 SourceWalker 语义路径。
  - 3.7 和 3.8 现有回归语料全部成功反编译，未新增失败。
- 已知限制：
  - 阶段 3 只接受无需通用 CFG 的直线型语句；复合 `and/or`、
    条件表达式、链式比较及普通分支/循环在阶段 4 处理。
  - 推导式、生成器、协程和 async 结构留在阶段 5。
  - 异常表、`try`、`with` 留在阶段 6；`match/case` 与 `except*`
    留在阶段 7。
  - Parser311 的源码格式由 `ast.unparse()` 规范化，不保证恢复原始排版。
- 下一步：
  - 阶段 4：建立基本块、CFG、dominator/post-dominator 和结构识别，
    恢复普通 `if`、循环、break/continue、loop-else 与条件表达式。

### 2026-07-30：阶段 4

- 状态：已完成
- 修改文件：
  - `decompyle3/controlflow/__init__.py`
  - `decompyle3/controlflow/basicblock.py`
  - `decompyle3/controlflow/cfg.py`
  - `decompyle3/controlflow/dominators.py`
  - `decompyle3/controlflow/structures.py`
  - `decompyle3/parsers/p311/base.py`
  - `pytest/test_controlflow311.py`
  - `pytest/test_deparse311.py`
  - `test/simple_source/311/02_control_flow.py`
  - `test/bytecode_3.11/generate.py`
  - `test/bytecode_3.11/golden/02_control_flow.dis`
  - `test/bytecode_3.11/golden_tokens/02_control_flow.tokens`
  - `test/bytecode_3.11/golden_cfg/02_control_flow.cfg`
- 已实现：
  - 根据入口、跳转目标及 return/raise 等终结指令划分基本块，建立
    fall-through、条件和无条件跳转边，并显式标记不可达块。
  - 提供无地址噪声的稳定 CFG 文本格式及 Parser `cfg` 调试选项；
    为控制流 corpus 跟踪 CFG、back edge 和 natural loop golden。
  - 通过固定点算法计算 dominator、immediate dominator、
    post-dominator 和 immediate post-dominator。
  - 识别 back edge 与 natural loop；对具有多个外部入口的循环 SCC
    抛出 `IrreducibleControlFlowError`，不输出猜测源码。
  - 恢复简单分支、`if/else`、`if/elif/else`、短路 `and/or`、
    条件表达式和链式比较。
  - 恢复 `while`、`for`、`break`、`continue`、`for/else`、
    `while/else` 及嵌套循环和分支。
  - 结构恢复只保留一条经过测试的路径：CFG 分析后直接生成标准库
    `ast` 结构化节点；不合成或维护兼容旧 Parser 的 `COME_FROM`
    Token 路径。该选择同时记录在 `structures.py` 模块注释中。
  - 对磁盘 `.pyc` 的反编译结果执行 `ast.parse()`、重新编译和原始/
    恢复源码行为对比。
- 验证命令：
  - `.venv311/bin/python test/bytecode_3.11/generate.py`
  - `.venv311/bin/python test/bytecode_3.11/generate.py --check`
  - `.venv311/bin/pytest -q pytest/test_corpus311.py pytest/test_scanner311.py pytest/test_normalize311.py pytest/test_deparse311.py pytest/test_controlflow311.py`
  - `.venv311/bin/pytest -q`
  - `make -C test check-bytecode-3.7 PYTHON=../.venv311/bin/python`
  - `make -C test check-bytecode-3.8 PYTHON=../.venv311/bin/python`
  - `.venv311/bin/flake8 decompyle3/controlflow decompyle3/parsers/p311/base.py pytest/test_controlflow311.py pytest/test_deparse311.py test/bytecode_3.11/generate.py test/simple_source/311/02_control_flow.py`
  - `git diff --check`
- 验证结果：
  - 阶段 0 至阶段 4 定向测试：`30 passed`。
  - 全部 pytest：`35 passed, 17 skipped`。
  - 10 份 corpus 的 dis/Token golden 及阶段 4 CFG golden：
    生成和一致性检查通过。
  - 3.7 bytecode 回归：`54 okay, 0 failed, 0 failed verification`。
  - 3.8 bytecode 回归：`48 okay, 0 failed, 0 failed verification`。
  - flake8、`git diff --check`：通过。
- 3.7/3.8 回归：
  - 新控制流模块只由 3.11 Parser 路径使用。
  - 3.7 和 3.8 继续使用原 Scanner、Spark Parser 与 SourceWalker，
    现有回归语料未新增失败。
- 已知限制：
  - 当前结构恢复面向 CPython 3.11 编译器生成的可约普通控制流；
    不可约 CFG 会明确拒绝。
  - 推导式、生成器、协程和 async 结构留在阶段 5。
  - 异常表、`try`、`with` 留在阶段 6；`match/case` 与 `except*`
    留在阶段 7。
  - 源码继续由 `ast.unparse()` 规范化，不恢复原始排版。
- 下一步：
  - 阶段 5：恢复推导式、生成器、协程及 async 控制流协议。

### 2026-07-30：阶段 5

- 状态：已完成
- 修改文件：
  - `decompyle3/parsers/p311/comprehensions.py`
  - `decompyle3/parsers/p311/base.py`
  - `decompyle3/controlflow/structures.py`
  - `pytest/test_generators311.py`
  - `pytest/test_deparse311.py`
  - `test/simple_source/311/03_comprehensions.py`
  - `test/simple_source/311/04_generators_async.py`
  - `test/bytecode_3.11/golden/03_comprehensions.dis`
  - `test/bytecode_3.11/golden/04_generators_async.dis`
  - `test/bytecode_3.11/golden_tokens/03_comprehensions.tokens`
  - `test/bytecode_3.11/golden_tokens/04_generators_async.tokens`
- 已实现：
  - 新增独立 `ComprehensionDecompiler311`，解析推导式 code object 的
    隐藏 `.0` 迭代器参数和嵌套循环，直接生成标准库 comprehension
    AST。
  - 恢复 list/set/dict comprehension、generator expression、多个
    `for`、多个 `if`、嵌套推导式，以及同步和异步推导式。
  - 保留推导式独立 code object 的作用域语义；加入循环变量不泄漏及
    lambda 位于推导式元素中的行为验证。
  - 识别 `RETURN_GENERATOR` 恢复入口；普通 generator 生成
    `FunctionDef`，coroutine 和 async generator 生成
    `AsyncFunctionDef`。
  - 恢复普通 `yield`、接收发送值的 yield expression、`yield from`
    和 async generator 的 `ASYNC_GEN_WRAP -> YIELD_VALUE` 协议。
  - 折叠 `GET_AWAITABLE -> SEND -> YIELD_VALUE -> RESUME` 为
    `ast.Await`；异步推导式保留自身 async generator clause，不生成
    多余的外层 `await`。
  - 在 async comprehension 内恢复 `GET_AITER`、`GET_ANEXT`、
    `SEND`、`END_ASYNC_FOR`，并通过异步行为对比验证。
  - 对磁盘 `.pyc` 的阶段 5 源码执行 AST 解析、重新编译、同步生成器
    send 流程及 asyncio 行为对比。
- 验证命令：
  - `.venv311/bin/python test/bytecode_3.11/generate.py`
  - `.venv311/bin/python test/bytecode_3.11/generate.py --check`
  - `.venv311/bin/pytest -q pytest/test_corpus311.py pytest/test_scanner311.py pytest/test_normalize311.py pytest/test_deparse311.py pytest/test_controlflow311.py pytest/test_generators311.py`
  - `.venv311/bin/pytest -q`
  - `make -C test check-bytecode-3.7 PYTHON=../.venv311/bin/python`
  - `make -C test check-bytecode-3.8 PYTHON=../.venv311/bin/python`
  - `.venv311/bin/flake8 decompyle3/parsers/p311/base.py decompyle3/parsers/p311/comprehensions.py decompyle3/controlflow/structures.py pytest/test_generators311.py pytest/test_deparse311.py test/simple_source/311/03_comprehensions.py test/simple_source/311/04_generators_async.py`
  - `git diff --check`
- 验证结果：
  - 阶段 0 至阶段 5 定向测试：`36 passed`。
  - 全部 pytest：`41 passed, 17 skipped`。
  - 10 份 corpus 的 dis/Token golden 生成与一致性检查通过。
  - 3.7 bytecode 回归：`54 okay, 0 failed, 0 failed verification`。
  - 3.8 bytecode 回归：`48 okay, 0 failed, 0 failed verification`。
  - flake8、`git diff --check`：通过。
- 3.7/3.8 回归：
  - 推导式和 suspension 协议恢复只接入 3.11 Parser。
  - 3.7 和 3.8 继续使用原有 Scanner、Spark Parser 与
    SourceWalker，现有语料未新增失败。
- 已知限制：
  - 普通 `async for` 语句带有异常表清理区，其结构化与异常 CFG
    一并留在阶段 6；阶段 5 已恢复 async comprehension 中的相同
    iteration/suspension 协议。
  - `async with` 留在阶段 6。
  - `match/case` 与 `except*` 留在阶段 7。
  - 源码继续由 `ast.unparse()` 规范化，不恢复原始排版。
- 下一步：
  - 阶段 6：解码 `co_exceptiontable`，将异常边加入 CFG，并恢复
    `try`、`except`、`finally`、`with` 和普通 `async for`。

### 2026-07-30：阶段 6

- 状态：已完成
- 修改文件：
  - `decompyle3/controlflow/__init__.py`
  - `decompyle3/controlflow/cfg.py`
  - `decompyle3/controlflow/exception_regions.py`
  - `decompyle3/controlflow/exception_structures.py`
  - `decompyle3/controlflow/exceptiontable311.py`
  - `decompyle3/controlflow/structures.py`
  - `decompyle3/parsers/p311/base.py`
  - `pytest/test_deparse311.py`
  - `pytest/test_exceptiontable311.py`
  - `test/bytecode_3.11/generate.py`
  - `test/simple_source/311/05_exceptions_with.py`
  - `test/bytecode_3.11/golden/05_exceptions_with.dis`
  - `test/bytecode_3.11/golden_tokens/05_exceptions_with.tokens`
  - `test/bytecode_3.11/golden_cfg/05_exceptions_with.cfg`
- 已实现：
  - 独立解码并验证 CPython 3.11 `co_exceptiontable`，保存
    start、end、target、depth 和 lasti，并逐个 code object 与标准库
    `dis.Bytecode.exception_entries` 精确对照。
  - 按异常保护范围和 handler target 划分基本块，将异常边加入 CFG，
    同时保留正常 fall-through、分支、循环和 return 路径。
  - 用单一 `ExceptionState311` 表示 handler 的栈深度和 lasti 状态，
    对同一 handler target 的状态一致性执行校验。
  - 恢复 `try/except`、多分支和裸 `except`、`except ... as ...`、
    `try/else`、`try/finally`、`try/except/finally`、嵌套异常结构，
    以及 finally 中的 return、break 和 continue。
  - 恢复无 `as`/有 `as` 的普通 `with`、多个和嵌套 context manager、
    `async with`，并验证正常退出、异常退出和异常抑制行为。
  - 恢复普通 `async for`、break 和 loop-else；复用 3.11
    `GET_ANEXT/SEND/END_ASYNC_FOR` 协议并依据异常表定位清理边界。
  - 扩充阶段 6 corpus、标准 dis、规范化 Token 和带异常边的 CFG
    golden；对磁盘 `.pyc` 执行重新解析、编译和同步/异步行为对比。
  - 对阶段 7 的 `except*`/`CHECK_EG_MATCH` 保持明确 fail closed，
    错误包含 code object、opcode 和物理 offset。
- 验证命令：
  - `.venv311/bin/python test/bytecode_3.11/generate.py`
  - `.venv311/bin/python test/bytecode_3.11/generate.py --check`
  - `.venv311/bin/pytest -q pytest/test_exceptiontable311.py`
  - `.venv311/bin/pytest -q`
  - `make -C test check-bytecode-3.7 PYTHON=../.venv311/bin/python`
  - `make -C test check-bytecode-3.8 PYTHON=../.venv311/bin/python`
  - `.venv311/bin/flake8 decompyle3/controlflow decompyle3/parsers/p311/base.py pytest/test_exceptiontable311.py pytest/test_deparse311.py test/bytecode_3.11/generate.py test/simple_source/311/05_exceptions_with.py`
  - `git diff --check`
- 验证结果：
  - 阶段 6 异常表、结构和行为验收：`10 passed`。
  - 全部 pytest：`51 passed, 16 skipped`。
  - 10 份 corpus 的 dis/Token golden 及阶段 6 异常 CFG golden：
    生成和一致性检查通过。
  - 3.7 bytecode 回归：`54 okay, 0 failed, 0 failed verification`。
  - 3.8 bytecode 回归：`48 okay, 0 failed, 0 failed verification`。
  - flake8、`git diff --check`：通过。
- 3.7/3.8 回归：
  - 异常表解码、异常 CFG 和结构恢复只接入 CPython 3.11 Parser。
  - 3.7 和 3.8 继续使用原有 Scanner、Spark Parser 与
    SourceWalker，现有语料未新增失败。
- 已知限制：
  - 当前异常结构恢复面向 CPython 3.11 编译器生成且已覆盖的规范
    模式；畸形异常表、状态冲突和无法确认边界的结构会明确报错。
  - `match/case` 和 `except*` 留在阶段 7。
  - 源码继续由 `ast.unparse()` 规范化，不恢复原始排版。
- 下一步：
  - 阶段 7：恢复 `match/case`、`except*` 及其 3.11 专用协议。

### 2026-07-30：阶段 7

- 状态：已完成
- 修改文件：
  - `decompyle3/controlflow/exception_structures.py`
  - `decompyle3/controlflow/match_structures.py`
  - `decompyle3/controlflow/structures.py`
  - `pytest/test_deparse311.py`
  - `pytest/test_exceptiontable311.py`
  - `pytest/test_syntax311.py`
  - `test/bytecode_3.11/generate.py`
  - `test/simple_source/311/06_match.py`
  - `test/simple_source/311/07_exception_group.py`
  - `test/bytecode_3.11/golden/06_match.dis`
  - `test/bytecode_3.11/golden/07_exception_group.dis`
  - `test/bytecode_3.11/golden_tokens/06_match.tokens`
  - `test/bytecode_3.11/golden_tokens/07_exception_group.tokens`
  - `test/bytecode_3.11/golden_cfg/06_match.cfg`
  - `test/bytecode_3.11/golden_cfg/07_exception_group.cfg`
- 已实现：
  - 新增独立 `MatchStructureDecompiler311`，对 CPython 3.11 pattern
    matcher 的栈协议执行符号化恢复，直接生成标准库 `ast.Match` 和
    `ast.match_case`。
  - 恢复 literal、singleton、capture、wildcard、sequence/star、
    mapping/`**rest`、class 位置与关键字、OR、guard 和嵌套 pattern。
  - 识别 case 成功、失败清理和公共 join 边界；支持 case body
    return/raise 和执行后汇合到 match 后续语句的官方风格结构。
  - 解码 `CHECK_EG_MATCH`、clause subgroup 分支、
    `PREP_RERAISE_STAR` 和清理路径，直接生成 `ast.TryStar` 与
    `except*` 源码。
  - 支持带/不带 `as` 的单个及多个 `except*`；由重新生成的
    `except*` 保留 ExceptionGroup 子组拆分、嵌套组形状和未处理
    子组重新抛出语义。
  - 普通 `except` 继续生成 `ast.Try`，`except*` 生成
    `ast.TryStar`，并通过 AST 测试保证两条路径不会混淆。
  - 扩充阶段 7 corpus、标准 dis、规范化 Token 和 CFG golden；
    对磁盘 `.pyc` 执行解析、重新编译、pattern 绑定/guard 行为及
    ExceptionGroup 行为对比。
- 验证命令：
  - `.venv311/bin/python test/bytecode_3.11/generate.py`
  - `.venv311/bin/python test/bytecode_3.11/generate.py --check`
  - `.venv311/bin/pytest -q pytest/test_syntax311.py`
  - `.venv311/bin/pytest -q`
  - `make -C test check-bytecode-3.7 PYTHON=../.venv311/bin/python`
  - `make -C test check-bytecode-3.8 PYTHON=../.venv311/bin/python`
  - `.venv311/bin/flake8 decompyle3/controlflow pytest/test_syntax311.py pytest/test_deparse311.py pytest/test_exceptiontable311.py test/bytecode_3.11/generate.py test/simple_source/311/06_match.py test/simple_source/311/07_exception_group.py`
  - `git diff --check`
- 验证结果：
  - 阶段 7 语法树和行为验收：`7 passed`。
  - 全部 pytest：`56 passed, 16 skipped`。
  - 10 份 corpus 的 dis/Token golden 及阶段 7 CFG golden：
    生成和一致性检查通过。
  - 3.7 bytecode 回归：`54 okay, 0 failed, 0 failed verification`。
  - 3.8 bytecode 回归：`48 okay, 0 failed, 0 failed verification`。
  - flake8、`git diff --check`：通过。
- 3.7/3.8 回归：
  - pattern matcher 和 ExceptionGroup 协议恢复只接入 CPython 3.11
    Parser。
  - 3.7 和 3.8 继续使用原有 Scanner、Spark Parser 与
    SourceWalker，现有语料未新增失败。
- 已知限制：
  - pattern 恢复面向 CPython 3.11 编译器生成的规范多行
    `match/case` 布局；无法确认 case/body 边界的非规范结构会拒绝，
    不输出猜测源码。
  - 当前 `except*` 覆盖普通单/多 clause 和子组重新抛出；
    与多层 `else/finally` 组合的额外编译形态留待阶段 8 压力语料
    验证。
  - 源码继续由 `ast.unparse()` 规范化，不恢复原始排版。
- 下一步：
  - 阶段 8：执行标准库子集、压力和 CLI 可靠性回归，更新支持文档
    并准备发布验收。
