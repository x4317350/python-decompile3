# CPython 3.11 Fail-closed Shape 逐类修复计划

## 1. 目标

本计划承接 `PYTHON_311_OPCODE_COVERAGE_PLAN.md` 阶段 9 的发布基线，
逐类处理当前 shape 矩阵中的 10 项 `unsupported_fail_closed`。

其中：

- 9 项是阶段 8 从 401 个真实源码失败中归纳出的粗粒度失败家族；
- 1 项 `irreducible_control_flow` 是人工构造的不可约 CFG 安全边界，
  不属于 604 个真实语料输入。

本计划的真实语料目标是：

```text
输入文件：604
成功反编译：203 -> 604
fail-closed：401 -> 0
语法失败：保持 0
未包装崩溃：保持 0
行为不一致：保持 0
```

人工不可约 CFG 不以“删除错误”或输出猜测源码的方式转为 pass。阶段 10
必须审查是否存在语义等价的结构化恢复；若不能证明，则保留
`IrreducibleControlFlowError`，并将其作为已经处理的发布安全边界。

## 2. 基线产物

阶段 0 固定以下产物：

```text
test/bytecode_3.11/fail_closed_baseline311.json
PYTHON_311_FAIL_CLOSED_BASELINE.md
test/bytecode_3.11/build_fail_closed_baseline.py
pytest/test_fail_closed_baseline311.py
```

阶段 0 基线绑定：

- 源码提交：`9f5bb1e4`；
- CPython：3.11.9；
- 输入文件：604；
- 输入摘要：
  `8b69da10c639757a77c33fe575a95f5c9cd7d4ebd84e3be835e181a024b9ac62`；
- 成功反编译：203；
- fail-closed：401；
- 真实语料失败家族：9；
- 人工安全边界：1。

基线 JSON 是不可变对照。后续阶段更新
`realworld_regression311.json`、shape 矩阵和发布策略，不覆盖阶段 0
基线。

## 3. 关键原则

### 3.1 粗分类不能用部分通过冒充完成

阶段 8 的 9 项 real-world shape 是失败家族，不是单一编译器 shape。
只有该家族在固定 604 文件中的计数降为 0，才能直接从
`unsupported_fail_closed` 转为 `pass`。

如果只修复部分错误签名：

1. 已修复签名必须有最小 fixture 和差分行为测试；
2. 剩余签名必须拆成更精确的子 shape；
3. 子 shape 必须记录 opcode、错误类型、fixture 和预期错误；
4. 原粗分类不得提前标记为 pass；
5. 分类总数必须与 fail-closed 总数守恒。

### 3.2 计数下降必须来自真实恢复

禁止通过以下方式降低数字：

- 删除或排除原有语料；
- 放宽异常捕获后忽略失败；
- 调整分类顺序把失败移动到其他大类；
- 只让恢复源码通过 `ast.parse()`，不做重新编译；
- 删除 fail-closed 测试；
- 对未知栈状态、异常边界或跳转目标进行猜测。

### 3.3 每项支持必须有四种证据

每个修复签名至少需要：

1. 最小源码 fixture；
2. Scanner/Normalizer 或 CFG 结构断言；
3. 恢复 AST 和重新编译断言；
4. 原文件与恢复文件的差分行为断言。

涉及异常、生成器、上下文管理器或副作用顺序时，行为探针必须覆盖
正常路径和异常路径。

### 3.4 发布策略必须原子更新

每个阶段同一提交内更新：

```text
实现
最小 fixture
专项测试
realworld_regression311.json
PYTHON_311_REALWORLD_REGRESSION.md
shape_matrix.json
PYTHON_311_SHAPE_COVERAGE.md
release_policy311.json
PYTHON_311_RELEASE_GATE.md
PYTHON_311_SUPPORT.md
本计划执行记录
```

若归档中的失败分类集合发生变化而发布策略未更新，
`run_release_gate.py --check` 必须失败。

## 4. 修复顺序

| 阶段 | Shape | 基线失败 | 主要信号 | 风险 |
| ---: | --- | ---: | --- | --- |
| 0 | 冻结基线和执行规则 | 401 | 10 项 inventory | low |
| 1 | `realworld_unpack_assignment` | 2 | `UNPACK_SEQUENCE` | medium |
| 2 | `realworld_import_protocol` | 6 | `IMPORT_FROM` | medium |
| 3 | `realworld_exception_cleanup_control_transfer` | 57 | `RERAISE`、`POP_EXCEPT` | very high |
| 4 | `realworld_call_and_expression_stack` | 217 | `SWAP_STACK`、栈下溢、表达式合流 | very high |
| 5 | `realworld_function_object_flow` | 9 | `_FunctionValue`、`STORE_ATTR` | medium |
| 6 | `realworld_comprehension_and_iterator_protocol` | 67 | `MAP_ADD`、`FOR_ITER` | very high |
| 7 | `realworld_with_control_transfer` | 29 | with-body return/yield/cleanup | high |
| 8 | `realworld_recursive_structure` | 13 | 结构恢复递归耗尽 | high |
| 9 | `realworld_match_boundary` | 1 | case body terminator | high |
| 10 | `irreducible_control_flow` | 0 | 人工不可约 CFG | security boundary |
| 11 | 全量收敛和发布门禁 | 目标 0 | 604 文件重放 | release |

异常和表达式基础能力安排在依赖它们的 function、comprehension、with、
recursion 和 match 之前。小规模 unpack/import 先执行，用于验证新的逐类
归档流程。

## 5. 阶段 0：冻结基线

任务：

- [x] 固定 10 项 shape 名称和当前状态；
- [x] 在归档环境中重放 604 个输入；
- [x] 验证 203 success + 401 fail-closed 守恒；
- [x] 统计每类 error type；
- [x] 统计每类 opcode；
- [x] 归一化并统计错误签名；
- [x] 为每类保留代表文件、code name 和 offset；
- [x] 明确修复依赖和风险；
- [x] 明确人工不可约 CFG 的安全边界处置；
- [x] 增加基线 JSON/Markdown 一致性测试。

退出标准：

```text
真实语料输入摘要与阶段 8 归档一致
9 个真实失败家族计数之和为 401
无未分类异常
10 项均有明确顺序、风险和处置
阶段 0 生成物可由固定环境完整重放
全量发布门禁通过
```

## 6. 阶段 1：解包赋值

基线：

```text
realworld_unpack_assignment = 2
UNPACK_SEQUENCE = 2
```

代表输入：

- `stdlib/code.py::showsyntaxerror`；
- `stdlib/multiprocessing/util.py::_run_after_forkers`。

任务：

- 最小化普通赋值中的嵌套解包和异常分支；
- 最小化循环 target 中的解包；
- 让 `_UnpackItem` 只在 assignment-target 构造期间存在；
- 区分表达式栈值与 store target；
- 覆盖 `UNPACK_SEQUENCE` 和 `UNPACK_EX`；
- 比较赋值结果、循环顺序和异常行为。

退出标准：

```text
该家族计数 2 -> 0，或剩余项拆为精确子 shape
两个代表输入均恢复并重新编译
新增行为探针通过
全量发布门禁通过
```

## 7. 阶段 2：导入协议

基线：

```text
realworld_import_protocol = 6
IMPORT_FROM = 6
```

任务：

- 建立显式 import transaction，而不是依赖相邻栈顶；
- 支持括号、多名称、别名和相对导入；
- 允许 `IMPORT_FROM` 之间存在合法中间操作；
- 验证 `IMPORT_NAME` owner 不跨越无关 import；
- 覆盖导入成功、缺失模块和循环导入异常。

退出标准：

```text
该家族计数 6 -> 0，或剩余项拆为精确子 shape
六个归档输入均恢复并重新编译
导入绑定和异常类型差分一致
```

## 8. 阶段 3：异常清理控制转移

基线：

```text
realworld_exception_cleanup_control_transfer = 57
RERAISE = 29
finally normal-path jump = 15
POP_EXCEPT = 8
PUSH_EXC_INFO = 3
SWAP_STACK = 2
```

任务顺序：

1. 从 exception table 恢复 handler/cleanup 区域；
2. 区分正常路径、异常路径和重复 finally cleanup；
3. 处理 `RERAISE` 的 lasti 标志和传播目标；
4. 处理 `PUSH_EXC_INFO`/`POP_EXCEPT` 生命周期；
5. 处理 finally 中的 return/break/continue；
6. 最后处理 cleanup 专用 `SWAP_STACK`。

必须覆盖：

- try/except/else/finally；
- 嵌套 try；
- handler 内再次抛出；
- bare raise；
- return/break/continue 覆盖；
- 异常类型、参数、traceback 可观察部分和副作用顺序。

退出标准：

```text
五类基线错误签名分别归零或拆为精确子 shape
不得由普通表达式 fallback 消费 exception cleanup
异常差分行为全部一致
```

## 9. 阶段 4：调用与表达式栈

基线：

```text
realworld_call_and_expression_stack = 217
错误签名 = 32
SWAP_STACK invalid = 68
Expression final values = 45
Invalid expression range = 20
POP_TOP/CALL/LOAD_ATTR 等栈下溢 = 多项
SemanticGenerationError = 3
```

该项必须拆成内部里程碑：

### 4A：栈排列

- 基于物理指令和 logical value 建模 `COPY`/`SWAP`；
- 禁止把 cache、NULL sentinel 或 exception state 当普通表达式；
- 消除 68 个 `SWAP_STACK` invalid。

### 4B：表达式区域

- 用 CFG 边界和支配关系选择表达式区间；
- 处理多出口短路、条件表达式和比较链；
- 消除 final stack cardinality 和 invalid range。

### 4C：调用协议

- 统一 `PUSH_NULL`、receiver、`PRECALL`、`KW_NAMES` 和 `CALL`；
- 处理嵌套调用、装饰器、属性调用和 `CALL_FUNCTION_EX`；
- 保证参数求值顺序。

### 4D：剩余栈消费者

- 处理 `POP_TOP`、`LOAD_ATTR`、`STORE_FAST`、build collection；
- 对 3 个生成源码校验失败建立独立最小 fixture；
- 仍无法恢复的签名必须拆为精确子 shape。

退出标准：

```text
217 项全部恢复，或所有剩余项已拆为精确子 shape
原粗分类计数为 0
32 个基线签名都有“已修复”或“已拆分”证据
调用参数、短路、副作用顺序差分一致
```

## 10. 阶段 5：函数对象流

基线：

```text
realworld_function_object_flow = 9
STORE_ATTR = 5
CALL(_FunctionValue) = 4
```

任务：

- 将 `_FunctionValue` 建模为可延迟消费的 IR；
- 支持函数定义赋给 name、attribute 和 subscript；
- 区分装饰器调用与普通函数值调用；
- 保留 defaults、kwdefaults、annotations、closure 和 qualname；
- 覆盖 descriptor/decorator 产生的可观察行为。

退出标准：

```text
该家族计数 9 -> 0，或剩余项拆为精确子 shape
函数签名、闭包、装饰顺序和绑定行为一致
```

## 11. 阶段 6：推导式与迭代协议

基线：

```text
realworld_comprehension_and_iterator_protocol = 67
MAP_ADD = 28
FOR_ITER = 26
RETURN_GENERATOR = 11
SET_ADD = 1
YIELD_VALUE = 1
```

任务：

- 识别嵌套 code object 中的 comprehension/genexpr 协议；
- 支持多层 `for` 和多重过滤；
- 处理 map/set/list append 的 stack depth；
- 恢复生成器入口、yield、send 和 return；
- 覆盖迭代器关闭、过滤异常和惰性求值。

退出标准：

```text
五类 opcode 失败分别归零或拆为精确子 shape
67 项家族计数归零
集合内容、顺序、惰性和异常行为一致
```

## 12. 阶段 7：with 控制转移

基线：

```text
realworld_with_control_transfer = 29
Returning with-body is not one expression = 29
```

任务：

- 让 with body 使用 statement region，而不是单表达式假设；
- 恢复 return、yield、break、continue；
- 区分 `__exit__` 抑制异常和正常清理；
- 支持多个 context manager 和 async-with；
- 比较 enter/exit 次序、参数、抑制结果和副作用。

退出标准：

```text
29 项全部恢复或拆为精确子 shape
with/async-with 正常与异常路径差分一致
```

## 13. 阶段 8：递归结构

基线：

```text
realworld_recursive_structure = 13
Parser311 recursion limit reached = 13
```

任务：

- 在前置修复后重新测量，确认哪些递归失败已自然消失；
- 给结构探测增加 visited/memo 和明确区间键；
- 将深层线性递归改为迭代 worklist；
- 保留环检测和最大工作量限制；
- 禁止通过提高全局 recursion limit 作为正式修复。

退出标准：

```text
13 项归零或拆为精确的复杂度边界
深层合法结构通过
循环/恶意输入仍快速 fail-closed
```

## 14. 阶段 9：match 边界

基线：

```text
realworld_match_boundary = 1
stdlib/tarfile.py
```

任务：

- 最小化 tarfile 中的 match 结构；
- 用 dominator/post-dominator 确定 case body 终点；
- 处理 guard、fallthrough、return/raise 和嵌套结构；
- 保留非 canonical 或歧义边界的安全拒绝。

退出标准：

```text
tarfile.py 恢复并重新编译
match 行为差分一致
歧义人工 CFG 仍 fail-closed
```

## 15. 阶段 10：人工不可约 CFG 审查

基线：

```text
irreducible_control_flow = 0 个真实语料
来源 = 人工 CFG 单元测试
```

审查问题：

1. CPython 3.11 编译器是否会生成该 CFG；
2. 是否存在不引入状态机的结构化 Python 表达；
3. 结构化结果能否证明控制流和副作用等价；
4. 若只能生成 `while True` + synthetic state，是否超出源码反编译契约。

默认结论：

- 标准 CPython 3.11 输入范围内不可达；
- 保留 `IrreducibleControlFlowError`；
- 不计入 604 文件成功率；
- 将“已审查并保留安全边界”视为该类完成。

只有在新增真实 CPython fixture 且能证明语义等价时，才允许改为 pass。

## 16. 阶段 11：全量收敛

目标：

```text
604/604 成功反编译并重新编译
真实语料 fail-closed = 0
syntax_failure = 0
unexpected_crash = 0
behavior mismatch = 0
人工不可约 CFG 保持明确策略
```

任务：

- 重跑 604 文件真实语料；
- 更新所有矩阵、归档、报告和支持文档；
- 删除已经归零且不再需要的粗分类；
- 保留精确 residual shape（如果有）及其测试；
- 更新发布策略中的 approved fail-closed 集合；
- 运行固定依赖和跨平台 CI；
- 形成最终修复总结。

## 17. 每阶段统一执行顺序

1. 确认工作区干净；
2. 记录当前家族计数和签名；
3. 从代表输入提取最小 fixture；
4. 添加能够稳定失败的专项测试；
5. 修复 Scanner/Normalizer/CFG/Parser/AST 中最小必要层；
6. 运行语法和重新编译验证；
7. 运行差分行为验证；
8. 重跑受影响的全部归档输入；
9. 重跑 604 文件真实语料；
10. 更新 shape 矩阵和发布策略；
11. 更新生成报告和支持文档；
12. 运行统一发布门禁；
13. 核对 Git diff；
14. 单独提交该阶段。

## 18. 命令模板

阶段 0 基线重放：

```bash
.venv311/bin/python \
  test/bytecode_3.11/build_fail_closed_baseline.py --check
```

真实语料更新：

```bash
.venv311/bin/python \
  test/bytecode_3.11/run_realworld_regression.py
.venv311/bin/python \
  test/bytecode_3.11/run_realworld_regression.py --check
```

专项测试：

```bash
.venv311/bin/python -m pytest -q \
  pytest/test_fail_closed_baseline311.py \
  pytest/test_realworld311.py \
  pytest/test_shape_behavior311.py
```

完整发布门禁：

```bash
make check-3.11-release PYTHON=.venv311/bin/python
```

格式和差异：

```bash
.venv311/bin/flake8 decompyle3 pytest test/bytecode_3.11
git diff --check
git status --short
```

## 19. 阶段执行记录

当前状态：

- 阶段 0 已完成并提交；
- 阶段 1 已完成并提交；
- 阶段 2 已完成并提交；
- 阶段 3 已完成并提交；
- 阶段 4 已完成并提交；
- 阶段 5 已完成并提交；
- 阶段 6 已完成并提交；
- 阶段 7 已完成，等待单独提交；
- 阶段 8 及后续阶段尚未开始；
- 原发布基线提交为 `9f5bb1e4`；
- 阶段 0 基线提交为 `4d6483b8`；
- 阶段 1 修复提交为 `748aafb9`；
- 阶段 2 修复提交为 `b41d6cdb`；
- 阶段 3 修复提交为 `58452b95`；
- 阶段 4 修复提交为 `ae53e1ae`；
- 阶段 5 修复提交为 `30595e11`；
- 阶段 6 修复提交为 `45c77535`；
- 当前 shape 状态为 37 pass、3 fail-closed、0 missing。

每阶段完成后追加：

```text
阶段：
提交：
目标 shape：
阶段前计数：
阶段后计数：
已修复签名：
剩余子 shape：
新增 fixture：
行为验证：
真实语料：
全量测试：
发布门禁：
已知限制：
```

阶段 0：

```text
阶段：0，冻结 fail-closed 修复基线
提交：4d6483b8
目标 shape：10 项现有 unsupported_fail_closed
阶段前计数：真实语料 401；人工安全边界 1 项
阶段后计数：不改变实现和发布状态
已修复签名：0
剩余子 shape：9 个真实语料粗分类 + 1 个人工不可约 CFG 边界
新增 fixture：0；复用固定 604 文件归档和人工 CFG 测试
行为验证：不改变既有 6 个真实语料差分探针
真实语料：固定环境重放 604 个输入；203 success、401 fail-closed，分类和输入摘要与阶段 8 归档一致
专项测试：baseline、real-world 和 shape behavior，68 passed
全量测试：779 passed, 6 skipped；6 项均与既有 legacy skip 白名单一致
发布门禁：矩阵、报告、golden、固定依赖和全量 pytest 门禁通过
已知限制：阶段 0 只冻结错误签名、opcode 分布、顺序和规则，不实现恢复
```

阶段 1：

```text
阶段：1，修复嵌套解包赋值和循环 target
提交：本阶段修复提交（见 Git 历史）
目标 shape：realworld_unpack_assignment
阶段前计数：2
阶段后计数：0
已修复签名：_UnpackItem 被嵌套 UNPACK_SEQUENCE 当作表达式；循环 target 的嵌套 UNPACK_SEQUENCE 被当作非 STORE opcode
剩余子 shape：无；该粗分类已从 unsupported_fail_closed 转为 pass
新增 fixture：test/simple_source/311/10_nested_unpacking.py；同步 dis 和 normalized token golden
行为验证：覆盖嵌套 UNPACK_SEQUENCE、UNPACK_EX、普通赋值、for、推导式，以及成功和异常路径
真实语料：604 个输入重放；204 success、400 fail-closed；两个代表 code object 均恢复并重新编译
专项测试：unpack、baseline、real-world、shape behavior、release gate 共 81 passed
全量测试：786 passed, 6 skipped；skip 与 legacy 白名单一致
发布门禁：31 shape pass、9 fail-closed、0 missing；完整门禁通过
已知限制：multiprocessing/util.py 在解包修复后继续暴露 PUSH_EXC_INFO 异常清理失败，已守恒迁移到阶段 3 家族，不属于解包残留
```

阶段 2：

```text
阶段：2，修复显式 import transaction 和 dotted alias owner 路由
提交：本阶段修复提交（见 Git 历史）
目标 shape：realworld_import_protocol
阶段前计数：6
阶段后计数：0
已修复签名：IMPORT_FROM has no owning IMPORT_NAME；覆盖 IMPORT_FROM → SWAP_STACK → POP_TOP → IMPORT_FROM 中间链
剩余子 shape：无；该粗分类已从 unsupported_fail_closed 转为 pass
新增 fixture：test/simple_source/311/11_import_transactions.py；同步 dis 和 normalized token golden
行为验证：覆盖 dotted import、括号多名称、别名、相对导入、transaction 隔离、缺失模块和循环导入异常
真实语料：604 个输入重放；208 success、396 fail-closed；6 个归档输入均清除 import protocol
专项测试：imports、unpack、baseline、real-world、shape behavior、release gate、corpus 共 134 passed
全量测试：797 passed, 6 skipped；skip 与 legacy 白名单一致
发布门禁：32 shape pass、8 fail-closed、0 missing；完整门禁通过
已知限制：pysource.py 和 _pytest/junitxml.py 越过导入后分别暴露 CALL 栈与 STORE_ATTR 函数对象失败，已守恒迁移到阶段 4、5；其余 4 个归档文件完整恢复并重新编译
```

阶段 3：

```text
阶段：3，修复异常清理控制转移
提交：58452b95，提交说明为“修复：完善 Python 3.11 异常清理控制转移”
目标 shape：realworld_exception_cleanup_control_transfer
阶段前计数：58；原基线 57，另有 multiprocessing/util.py 在阶段 1 后守恒迁入 1 项
阶段后计数：0
已修复签名：RERAISE 29、finally normal-path jump 15、POP_EXCEPT 8、PUSH_EXC_INFO 4，以及异常清理 SWAP；同时处理 handler 返回值、异常名清除、last-i cleanup、嵌套 finally、handler 内 raise/bare raise 和 while-true handler break
剩余子 shape：固定 604 文件中无异常清理残留；普通返回表达式 SWAP 和函数名含 exception 的 CALL 栈错误按错误本质迁移到阶段 4
新增 fixture：test/simple_source/311/12_exception_cleanup.py；同步 dis 和 normalized token golden
行为验证：覆盖返回变量、raise from 的 cause、bare raise、嵌套 handler、正常与异常 finally 副作用顺序、生成器 yield/close/异常退出；既有 finally return/break/continue 回归继续通过
真实语料：604 个输入重放；239 success、365 fail-closed；0 syntax failure、0 unexpected crash；异常清理分类归零
专项测试：exception cleanup、exception table、baseline、real-world、shape behavior、release gate、corpus 共 92 passed
全量测试：803 passed, 6 skipped；skip 与 legacy 白名单一致
发布门禁：33 shape pass、7 fail-closed、0 missing；完整门禁和 skip 白名单检查通过
已知限制：本阶段只消费结构化异常协议；普通表达式 COPY/SWAP、函数调用栈、迭代器、with、递归和 match 残留继续由后续阶段处理
```

阶段 4：

```text
阶段：4，修复调用与表达式栈
提交：ae53e1ae，提交说明为“修复：完成 Python 3.11 调用与表达式栈恢复”
目标 shape：realworld_call_and_expression_stack
阶段前计数：219；原冻结基线 217，另有阶段 2、3 清除前置错误后守恒迁入 2 项
阶段后计数：0
已修复签名：SWAP_STACK invalid、Expression final values、Invalid expression range、POP_TOP/CALL/LOAD_ATTR 栈下溢、嵌套和多行条件表达式、比较链、独立 FORMAT_VALUE、普通 callback lambda 与装饰器协议混淆、循环返回值跨 finally 清理；大型表达式 CFG 的后支配计算由近似三次方收敛改为逆序传播
剩余子 shape：原调用/表达式栈粗分类归零；后续暴露的推导式、函数对象、with 和高级异常清理均通过 code object、协议位置或结构级 shape hint 精确分类；match 边界误判同时归零
新增 fixture：test/simple_source/311/13_call_expression_stack.py；同步 dis 和 normalized token golden
行为验证：覆盖 f-string、嵌套三元表达式、多行条件选择、callback lambda、位置/关键字参数求值顺序、比较链循环、循环 return/finally 和嵌套 finally/except
真实语料：604 个输入重放；331 success、273 fail-closed；0 syntax failure、0 unexpected crash；调用/表达式栈分类归零；6/6 真实语料行为探针一致
专项测试：调用与表达式栈、矩阵、shape behavior、real-world、release gate、corpus 和 opcode corpus 共 122 passed
全量测试：808 passed, 6 skipped；skip 与 legacy 白名单逐项一致
发布门禁：34 shape pass、6 fail-closed、0 missing；调用/表达式栈与 match 边界转为 pass，高级异常清理恢复为精确 fail-closed；完整门禁、报告时效、golden 和 skip 白名单检查通过
已知限制：25 个高级异常清理布局在阶段 4 暴露后恢复为精确 fail-closed 状态；推导式 124、函数对象 15、递归 33、with 76 继续由后续阶段处理
```

阶段 5：

```text
阶段：5，修复函数对象流
提交：30595e11，提交说明为“修复：完成 Python 3.11 函数对象流恢复”
目标 shape：realworld_function_object_flow
阶段前计数：15；原冻结基线 9，另有前置阶段清除 import、调用和表达式栈错误后暴露或迁入 6 项
阶段后计数：0
已修复签名：_FunctionValue 进入 BUILD_TUPLE、BUILD_LIST、BUILD_MAP、BUILD_CONST_KEY_MAP 等变长表达式消费者；lambda 存入 attribute 和 subscript；lambda 作为默认值、容器成员和闭包值；装饰器调用与普通函数值调用；defaults、kwdefaults、annotations、closure、descriptor 及装饰顺序；同时用 CFG 区分 case _ 的 NOP 边界与装饰器行表填充
剩余子 shape：原函数对象粗分类归零；15 个代表文件中 4 个完整恢复，其余 11 个守恒迁移到推导式、with、高级异常清理和递归等后续精确家族
新增 fixture：test/simple_source/311/14_function_object_flow.py；同步 dis 和 normalized token golden
行为验证：覆盖双层装饰器求值与调用顺序、函数签名与 annotations、staticmethod/classmethod/property、lambda 的属性/下标/字典/列表/元组/默认值/闭包消费，以及 match 通配分支与装饰器 NOP 边界
真实语料：604 个输入重放；335 success、269 fail-closed；0 syntax failure、0 unexpected crash；函数对象和调用/表达式栈分类均归零；6/6 真实语料行为探针一致
专项测试：函数对象、shape behavior、发布门禁、opcode corpus 和控制流共 98 passed；阶段 2 后继错误断言回归 49 passed
全量测试：813 passed, 6 skipped；skip 与 legacy 白名单逐项一致
发布门禁：35 shape pass、5 fail-closed、0 missing；函数对象流转为 pass；报告时效、golden、真实语料归档和完整门禁均通过
已知限制：推导式/迭代协议 130、高级异常清理 27、递归结构 34、with 控制转移 78 继续由后续阶段处理；人工不可约 CFG 保持明确 fail-closed
```

阶段 6：

```text
阶段：6，修复推导式与迭代协议
提交：45c77535，提交说明为“修复：完成 Python 3.11 推导式与迭代协议恢复”
目标 shape：realworld_comprehension_and_iterator_protocol
阶段前计数：130；原冻结基线 67，另有前置阶段清除调用、表达式和函数对象错误后暴露或迁入 63 项
阶段后计数：0
已修复签名：FOR_ITER 50、MAP_ADD 29、RETURN_GENERATOR 23、POP_JUMP_FORWARD_IF_FALSE 10、LIST_APPEND 5、POP_JUMP_FORWARD_IF_TRUE 5、无 loop-back/break edge 2、生成器表达式范围 4、JUMP_FORWARD 1、SET_ADD 1；覆盖 stack depth=1 的增量容器、EXTENDED_ARG 前缀循环、无回边终止循环、条件输出、布尔/条件过滤、比较链、闭包生成器、yield from 和生成器 lambda
剩余子 shape：原推导式/迭代协议粗分类归零；阶段修复后新暴露的调用/表达式栈 11、异常清理 45、递归结构 45、with 控制转移 90 均按实际失败边界守恒分类
新增 fixture：test/simple_source/311/15_comprehension_iterator_protocol.py；同步 dis 和 normalized token golden
行为验证：覆盖增量 list/set/dict、长循环 EXTENDED_ARG、首项返回/空迭代异常、条件 dictcomp 输出、and/or 与条件过滤、比较链 genexpr、闭包生成器、yield from 和生成器 lambda；真实语料 6/6 行为探针一致
真实语料：604 个输入重放；413 success、191 fail-closed；0 syntax failure、0 unexpected crash；推导式/迭代协议分类归零
专项测试：阶段 6 专项、生成器、表达式、解包、shape behavior、语料与 opcode corpus 均通过
全量测试：817 passed, 6 skipped；skip 与 legacy 白名单逐项一致
发布门禁：36 shape pass、4 fail-closed、0 missing；推导式/迭代协议转为 pass；报告时效、golden、真实语料归档和完整门禁均通过
已知限制：真实语料仍有调用/表达式栈 11、高级异常清理 45、递归结构 45、with 控制转移 90；人工不可约 CFG 保持明确 fail-closed
```

阶段 7：

```text
阶段：7，修复 with 控制转移
提交：本阶段修复提交（等待单独提交）
目标 shape：realworld_with_control_transfer
阶段前计数：90；原冻结基线 29，另有前置阶段清除调用、函数对象和推导式错误后暴露或迁入 61 项
阶段后计数：0
已修复签名：返回型 with-body 非单表达式 59、context-manager callable 丢失 11、跨外层异常区间 10、target 脱离协议 6，以及嵌套条件、嵌套清理调用和栈边界 4；支持 statement region、物理 __exit__/__aexit__ 协议剥离、return/yield/break/continue、多 context、嵌套 context、异常抑制、普通外层 try 和裸 except 区分
剩余子 shape：原 with 控制转移粗分类归零；90 个代表文件中 56 个完整恢复，其余 34 个按实际根因守恒迁移到高级异常清理 27、递归结构 6 和调用/表达式复合结构 1
新增 fixture：test/simple_source/311/16_with_control_transfer.py；同步 dis 和 normalized token golden
行为验证：覆盖同步和异步 enter/exit 次序、正常与异常参数、抑制结果、多语句 return、yield/send、break、continue、多 context、嵌套 context 和 try/finally 副作用；真实语料 6/6 行为探针一致
真实语料：604 个输入重放；469 success、135 fail-closed；0 syntax failure、0 unexpected crash；with 控制转移分类归零
专项测试：阶段 7、异常表、异常清理、控制流、生成器、表达式、推导式、调用、函数对象、导入、解包与 corpus 回归均通过
全量测试：822 passed, 6 skipped；skip 与 legacy 白名单逐项一致
发布门禁：37 shape pass、3 fail-closed、0 missing；with 控制转移转为 pass；报告时效、golden、真实语料归档和完整门禁均通过
已知限制：真实语料仍有调用/表达式栈 12、高级异常清理 72、递归结构 51；人工不可约 CFG 保持明确 fail-closed
```
