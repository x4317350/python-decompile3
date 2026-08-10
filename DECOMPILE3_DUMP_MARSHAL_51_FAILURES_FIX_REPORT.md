# decompile3：Python 3.11 Marshal 51 个失败修复报告

## 1. 结果

- 修复前：2,374 / 2,425 个 fixed pyc 可反编译，51 个文件失败。
- 修复后：2,425 / 2,425 个 fixed pyc 可完整反编译。
- 重新编译：2,425 / 2,425 份生成源码通过 CPython 3.11 `compile()`。
- 原始 51 个失败文件单独回归：51 / 51 通过。
- 项目完整测试：1,088 passed，6 skipped。
- realworld 固定语料：604 / 604 反编译及语法验证通过，零 fail-closed、零未包装崩溃。

外部 pyc 在验证中只经 `xdis.load_module()` 读取、反编译并对生成文本调用
`compile()`；没有导入或执行其中代码。

## 2. 两类根因

### 2.1 CFG、region 和控制转移所有权不完整

Python 3.11 将源码结构拆成普通 CFG、exception table 和若干非抛异常的物理
清理块。旧实现对这些边界的证明不完整，主要表现为：

- 分支局部短路表达式的复制清理被当成第二次求值；
- 条件 trampoline、等价端点和退化跳转没有被条件规划器接管；
- 无直接 latch 的 `for` 退出、循环 iterator cleanup 和 try/except frontier
  被误分到相邻 region；
- handler 的多个异常前驱被误认为多个逻辑入口；
- `try: return` 的主路径不会抛异常，CPython 因而省略其 exception-table
  主入口，旧实现无法识别仍保留的 handler；
- 多行条件表达式被 match 恢复器误判成 `match/case`。

修复统一使用 CFG 前驱/后继、exception-table 范围、闭合 region、精确终止块和
受限 source-position 证据决定所有权。任何普通旁路、外部前驱、回边、异常边或
不完整协议仍然 fail-closed。

### 2.2 VM 临时值和源码表达式值的所有权混淆

旧实现有时把 CPython 的物理栈协议值当成源码 AST 值，或者反过来丢失真正的
源码值，主要包括：

- lambda `_FunctionValue` 未在受限位置转换为表达式；
- comprehension 的 `EXTENDED_ARG` 和 forward trampoline 破坏语义回边；
- held return 穿过 finally/except 清理时丢失；
- `SWAP 2 / POP_TOP` 或独立 `POP_TOP` 删除 iterator 时被当成普通表达式清理；
- 调用参数中的嵌套 `IfExp` 被 statement-level 条件结构器提前接管。

修复没有为空栈 `POP_TOP`、缺值 `RETURN_VALUE` 或未知跳转提供默认行为；只有
lambda、iterator cleanup、held return 和闭合条件值的具体协议证明成立时，才把
物理栈操作映射为源码 AST。

## 3. 修改范围

主要源码：

- `decompyle3/controlflow/structures.py`
- `decompyle3/controlflow/exception_structures.py`
- `decompyle3/controlflow/match_structures.py`
- `decompyle3/parsers/p311/base.py`
- `decompyle3/parsers/p311/comprehensions.py`

测试与语料：

- `pytest/test_controlflow311.py`
- `pytest/test_exceptiontable311.py`
- `pytest/test_function_object_flow311.py`
- `pytest/test_comprehension_iterator_protocol311.py`
- `pytest/test_opcode_corpus311.py`
- `test/fixtures311/terminal_if_else.py`
- `test/fixtures311/except_handler_return.py`
- `test/simple_source/311/05_exceptions_with.py`
- `test/simple_source/311/14_function_object_flow.py`
- `test/simple_source/311/15_comprehension_iterator_protocol.py`
- 对应 CPython 3.11 dis/token/CFG golden 和 realworld 固定档案。

原修复计划保存在：

- `decompyle3-dump-marshal-51-failures-fix-plan.md`

## 4. 新增回归覆盖

- lambda 延迟闭包值和 `STORE_DEREF` 行为；
- 180 项条件推导式的 `EXTENDED_ARG`、布尔 filter trampoline；
- 无 latch iterator break、嵌套循环和 iterator return；
- 分支局部 `and/or`、混合短路、异常保护和条件参数；
- 退化 truth/identity 条件的求值次数；
- handler 多异常前驱下的显式 return；
- entry-less `try: return`；
- 循环内 `except: continue` 与条件 return；
- held return 在清理成功、清理抛异常且异常被捕获时的求值顺序；
- 多行嵌套 `IfExp` 调用参数不被误判为 `match/case`；
- 相关 CFG corruption、外部前驱、异常边和不完整协议继续 fail-closed。

动态测试会执行原源码和反编译源码，比较返回值、异常、副作用顺序、真值测试
次数、回调次数和短路行为，而不是只比较文本或只做 `ast.parse()`。

## 5. 验证命令和结果

```bash
.venv311/bin/pytest -q \
  pytest/test_exceptiontable311.py \
  pytest/test_controlflow311.py \
  pytest/test_function_object_flow311.py \
  pytest/test_comprehension_iterator_protocol311.py \
  pytest/test_patch_helpers_regression311.py \
  pytest/test_stage11_convergence311.py \
  pytest/test_call_expression_stack311.py
# 217 passed

.venv311/bin/pytest -q
# 1088 passed, 6 skipped
```

外部语料最终门禁使用 6 个只读 worker，对 `report.json` 中 2,425 个
`fixed_pyc` 逐一执行 `load_module -> code_deparse -> compile`：

```text
WORKER_STATUSES [0, 0, 0, 0, 0, 0]
RESULT 2425 0
```

## 6. 安全边界

本次没有采用以下规避方式：

- 跳过未知 opcode；
- 捕获反编译异常后输出 `pass` 或占位函数；
- 空栈时忽略 `POP_TOP`；
- 缺少值时把 `RETURN_VALUE` 默认解释为 `None`；
- 清空 structured region 的残留栈；
- 按模块名、函数名或 offset 硬编码特判；
- 手工修改任何反编译结果。

所有新增接受路径都有对应的 CFG、exception-table 或受检表达式栈证明，证明
失败时仍抛出带 code object 和 offset 的 Python 3.11 解析错误。
