# Python 3.11 函数末尾条件与短路表达式修复记录

## 1. 目标

修复 CPython 3.11 将函数末尾的空主体条件：

```python
if left and right:
    pass
```

编译成短路判断加多个物理 `LOAD_CONST None; RETURN_VALUE` 出口后，条件结构器把
全部出口按相同返回表达式合并成一个 endpoint，最终在条件跳转处 fail-closed 的
问题。

目标样本当前错误为：

```text
Python311ParseError: Unsupported phase-3 opcode POP_JUMP_FORWARD_IF_FALSE
[version=3.11, code='received_chat_message', offset=874]
```

修复后应恢复：

```python
if msg.isMeSend() == False and msg.isRead == False:
    pass
```

同一证据路径也支持位置表能够明确证明的裸 `return` suite，并保留条件的短路
顺序。后续还处理 terminal `obj and obj.method()` 为每个短路出口复制
`POP_TOP; LOAD_CONST None; RETURN_VALUE` 的形态。两项修复都要求 CFG、函数作用域、
尾部布局和位置表共同证明所有权；不得全局放宽 `_ConditionPlan` 的双 endpoint
不变量，也不得看到空栈 `POP_TOP` 就直接忽略。

## 2. 阶段 0 基线

### 2.1 仓库

计划固化时的实际基线：

```text
仓库：/Users/ice/Desktop/Custom/WorkCode_github/python-decompile3
分支：master，跟踪 origin/master
提交：729843c33a8dfe7cf34ba35cecc4a39a819ebda1
说明：修复：合并 Python 3.11 隐式 None 函数尾声
工作区：干净
全量测试：退出码 0
```

执行过的检查：

```bash
git status --short --branch
git rev-parse HEAD
git log -1 --oneline
.venv311/bin/python -m pytest -q
```

### 2.2 外部只读样本

```text
marshal：
/Users/ice/Desktop/Custom/WorkCode_github/py3disassembly/dump/testcfg/
network.rpcentity.ClientEntities.original.marshal
SHA-256：00eb9adb0dc7ffc433dfbc29c13d12a13e0a669289ac24c176598b8069618429

Python 2.7 参考源码：
/Users/ice/Desktop/Custom/WorkCode_github/py3disassembly/dump/testcfg/
network.rpcentity.ClientEntities.py
SHA-256：c91b24b7736e957407843dca9b73aa6670543c0424958d9061ad270a59656415

旧恢复文本：
/Users/ice/Desktop/Custom/WorkCode_github/py3disassembly/dump/testcfg/
network.rpcentity.ClientEntities.original.decompyle3.py
SHA-256：d6b05d03082ea857ab3434b4a06524dd0e2ebaec4294a89144ec7692ea76c259
```

外部文件只允许：

- 读取字节、计算摘要；
- 使用 `py311tool` 静态转换自定义 Opcode；
- 使用 `marshal.loads()` 构造 code object 供扫描、反汇编和反编译；
- 对恢复文本执行 `ast.parse()` 或 `compile(..., "exec")`；
- 比较 token、位置表、CFG 和 AST。

禁止：

- import 目标模块；
- `exec`、`eval` 或调用外部 code object；
- 执行恢复源码；
- 把外部 marshal、pyc 或业务源码复制进仓库；
- 为单个样本放宽通用 fail-closed 边界。

## 3. 已固化的问题证据

### 3.1 源码位置

参考方法 `ClientAccount.received_chat_message` 末尾为：

```python
_addValidMsg()
if msg.isMeSend() == False and msg.isRead == False:
    pass
```

参考文件中的行号为 1233--1234。

### 3.2 标准化 token

目标函数尾部关键 token：

```text
828 LOAD_DEREF msg
830 LOAD_METHOD isMeSend
852 PRECALL 0
856 CALL 0
866 LOAD_CONST False
868 COMPARE_EQ
874 POP_JUMP_FORWARD_IF_FALSE -> 902

876 LOAD_DEREF msg
878 LOAD_ATTR isRead
888 LOAD_CONST False
890 COMPARE_EQ
896 POP_JUMP_FORWARD_IF_FALSE -> 906

898 LOAD_CONST None
900 RETURN_VALUE
902 LOAD_CONST None
904 RETURN_VALUE
906 LOAD_CONST None
908 RETURN_VALUE
```

异常表为空。CFG 为：

```text
B16 condition A
├── true  -> B17 condition B
│            ├── true  -> B18 [898, 902) None-return
│            └── false -> B20 [906, 910) None-return
└── false -> B19 [902, 906) None-return
```

### 3.3 位置表证据

CPython 3.11 `co_positions()` 显示：

- offset 828--896 属于条件行；
- offset 898--900 是单独的下一源码行，列跨度精确覆盖四字符 `pass`；
- offset 902--908 仍映射到条件的短路范围，而不是独立 suite 行。

因此 B18 是条件为真时的空 suite 路径，B19/B20 是两个条件为假的短路路径。
位置表只能作为 CFG 证明的一部分，不能单独授权结构恢复。

## 4. 根因

`_bounded_condition_plan()` 先收集三个物理 endpoint：

```text
{898, 902, 906}
```

`_condition_endpoint_signature()` 对三个出口都得到相同的
`Constant(value=None)`。随后 `_coalesce_condition_endpoints()` 将它们全部映射到
同一个 canonical endpoint。

`_ConditionPlan` 要求两个逻辑 endpoint，用来分别代表条件为真和条件为假。合并后
只剩一个 endpoint，计划返回 `None`。直线解析器随后在 offset 874 收到尚未被
结构器消费的条件跳转并报错。

既有 `_ImplicitReturnEpiloguePlan` 无法处理这个形态，因为它的入口参数已经是有效
的 `_ConditionPlan`。本问题发生在该计划建立之前。

## 5. 修复原则

### 5.1 不改变通用 endpoint 合并语义

保留 `_coalesce_condition_endpoints()` 和 `_ConditionPlan` 的现有安全不变量。新增
一个在通用同签名合并之前运行的专用识别器，仅把已证明的 suite endpoint
作为 True endpoint，把其余物理 None-return endpoint 归一成 False endpoint。

### 5.2 双重证明

条件计划阶段和 AST 发射阶段都必须验证 terminal empty-if 所有权。第一层只允许
建立二出口条件表达式；第二层再次验证完整 CFG 并拥有全部物理尾声，之后才允许
跳过这些 token，并按位置跨度发射 `ast.If(body=[ast.Pass()])` 或
`ast.If(body=[ast.Return(value=None)])`。

### 5.3 初始支持范围

必须同时满足：

1. CPython 3.11 普通同步函数；
2. 当前 region 精确结束于 code object 末尾；
3. 不在 loop context、module、class body、generator、coroutine 或 async generator；
4. 条件图无回边、异常边和交叉入口；
5. 从条件入口可达的非条件出口全部是独立 basic block 中的精确
   `LOAD_CONST None; RETURN_VALUE`；
6. 所有出口组成连续函数尾部 cluster，且没有正常 continuation；
7. 每个出口都被当前条件图拥有，没有外部 predecessor 或不可达出口；
8. 恰好一个出口的 `LOAD_CONST None` 位置位于条件之后的独立源码行，列跨度
   精确为四字符 `pass` 或六字符裸 `return`，并且 suite 起始列只比推导出的
   `if` 语句缩进增加 1--4 个源码字符；
9. suite endpoint 是 CPython 3.11 canonical 前置 suite 布局；
10. 其余出口只能是同一条件图的短路失败出口；
11. 条件求值图完整、有界、无环，并能恢复成单个布尔表达式。

任一条件不满足时返回 `None`，保持现有 fail-closed 行为。

## 6. 明确拒绝的形态

- 无法与非典型缩进安全区分的显式 `return None`；
- 非 None return；
- 条件之后存在任意普通语句；
- terminal `if/else` 的两个实际 suite；
- exception handler、`finally`、`with` 或 `except*` cleanup；
- 循环体、break/continue、反向边；
- module/class body 或任何 suspension code object；
- 缺失、冲突或无法解析的 `co_positions()`；
- suite span 不精确、出现多个 suite 候选或没有 suite 候选；
- 人工编辑、损坏或 CFG 所有权不能闭合的字节码。

显式 terminal `return` 与 `pass` 在部分字节码操作序列上相同；`return None` 的
常量位置跨度甚至也恰好是四个字符。因此必须同时利用 suite token span 和相对
缩进列区分：`pass` 从 suite 缩进列开始，而 `None` 还要跨过 `return `。没有完整
证据时不得猜测。初始版本只接受单层缩进增加 1--4 个源码字符；更宽的非典型缩进
继续 fail-closed。

## 7. 实现步骤

### 阶段 1：位置与尾部证据 helper

- 在 `StructuredDecompiler311` 中建立只读 offset-to-position 映射；
- 新增 terminal empty-if evidence 数据结构；
- 验证尾部 None-return pair、物理 block、CFG 可达性、出口闭合、异常边和所有权；
- 精确识别唯一 pass suite endpoint。

### 阶段 2：条件 endpoint 拆分

- 在 `_bounded_condition_plan()` 的通用 endpoint signature 合并之前调用专用 helper；
- 仅在 raw endpoint 超过两个且全部证据成立时，将 pass endpoint 保留为 True，
  其余出口映射到一个 False canonical endpoint；
- 使用现有 `_combine_decision()` 恢复 `and`、`or`、`not` 和混合短路表达式；
- 专用匹配失败时继续现有合并和 fail-closed 路径。

### 阶段 3：AST 发射

- 新增 `_TerminalEmptyIfPlan`；
- 在普通 terminal-if/implicit-epilogue 之前尝试严格 empty-if 计划；
- 成功时输出 `ast.If(test=..., body=[ast.Pass()], orelse=[])`；
- 只跳过计划证明拥有的完整 terminal region；
- 不复用全局 Return 清理，不改变其他 return 的保留规则。

### 阶段 4：测试

正向 fixture：

- 单条件 terminal `pass`；
- 两项和多项 `and`；
- `or`、`not`、混合 `and/or`；
- 条件之前有普通语句；
- 带调用的条件，验证求值一次、顺序、短路和异常传播。

负向测试：

- terminal 显式 `return`；
- early return 后有 continuation；
- 非 None return；
- module/class/generator/coroutine/async-generator；
- 缺失或冲突位置、错误 pass span；
- 非 None/缺失 return、额外语义 token；
- normal successor、back edge、exception edge、foreign predecessor；
- 不可达出口、越界 region、loop context 和 work limit。

验证层级：

1. 新增定向 unit tests；
2. `pytest/test_controlflow311.py`；
3. Python 3.11 高风险控制流/异常/语法测试；
4. 全量 pytest；
5. 外部样本静态反编译；
6. 恢复文本 `ast.parse()` 和 `compile(..., "exec")`；
7. 输入 SHA-256 前后对比、`git diff --check` 和工作区审查。

## 8. 验收标准

- `received_chat_message` 不再在 offset 874 失败；
- 目标尾部恢复为带 `Pass`、无 `orelse` 的 `ast.If`；
- `isMeSend()` 和 `isRead` 的短路顺序不变；
- 外部整个 marshal 能继续反编译；若出现新的独立失败，应单独记录，不能把本修复
  宣称为整文件完全通过；
- 显式 `return` 与所有负向 CFG 形态不被误判；
- 现有 terminal if/else、隐式 None 尾声、exception cleanup 和 fail-closed 测试
  无回退；
- 全量测试通过；
- 外部文件摘要不变且从未执行。

## 9. 风险与回滚

主要风险是把显式 `return` 或多个真实终止分支误判为 `pass`。控制措施是：专用
matcher、精确位置 span、完整 CFG 所有权、同步函数末尾限制和双重验证。

若任何高风险回归出现，回滚范围应只包括：

- terminal empty-if evidence/plan；
- `_bounded_condition_plan()` 的专用 endpoint 拆分接入；
- `_if_statement()` 的专用发射接入；
- 对应 fixture/tests。

不得回滚或放宽既有 terminal-if、implicit-return epilogue、异常 cleanup 和
fail-closed 逻辑。

## 10. 执行记录

```text
阶段 0：完成
阶段 1：完成；建立 offset-to-position 映射和严格 CFG/尾部证据
阶段 2：完成；只在专用证据成立时拆分 suite/false endpoint
阶段 3：完成；双重验证后发射 Pass 或裸 Return suite
阶段 4：完成；新增 11 个正向 fixture、61 个定向/负向测试实例
外部目标函数验证：完成
terminal 短路表达式：完成；结构层拥有全部 POP_TOP/None-return cleanup
外部整文件验证：完成
全量验证：完成
```

### 10.1 实现结果

```text
新增：_TerminalEmptyIfEvidence、_TerminalEmptyIfPlan、
      _TerminalShortCircuitStatementPlan
条件阶段：在通用 signature 合并前保留唯一 suite endpoint，归一其余 false exits
表达式阶段：以全部受控 POP_TOP 为 CFG expression terminals，恢复 And/Or BoolOp
发射阶段：重新验证同步函数末尾、位置、CFG、全部出口与 predecessor 所有权
拒绝：不明确的 return None、非 None return、异常边、回边、普通后继、
      foreign predecessor、缺失/冲突位置、module/class、generator 和 loop context
```

### 10.2 测试结果

```text
terminal/control-flow：124 passed
高风险定向：258 passed
全量 pytest：1025 passed，6 skipped
维护的 CPython 3.11 corpus：32 files checked
固定真实语料：604/604 decompile/syntax success，检查通过
Opcode：110/110
Behavior：110/110
Shape：45 pass，1 approved fail-closed，0 missing
release gate、flake8、git diff --check：通过
```

实现和 fixture 改变了固定真实语料输入，归档输入摘要按实际 604 文件重放结果从：

```text
2444e1cd696e13cf7a227469f9f1a85a13b9958bda0f7b74f7a5fb13aa881064
```

更新为：

```text
5356c34e2a760b8fbfac70301c03830b4775b3136169865d83578d62f3461a6c
```

计数、失败分类和行为结果均未变化。

### 10.3 外部样本结果

`received_chat_message` 已能够单独静态反编译，恢复尾部为：

```python
_addValidMsg()
if msg.isMeSend() == False and msg.isRead == False:
    pass
```

该 code object 的恢复文本通过 `ast.parse()` 和 `compile(..., "exec")`。整个外部
marshal 已越过原错误 `received_chat_message:874`。

第二个错误为：

```text
Python311ParseError: Operand stack underflow (opcode POP_TOP)
[version=3.11, code='godapp_bindthirdparty_cb', offset=836]
```

根因是 `JUMP_IF_FALSE_OR_POP` 的 false 分支保留左值并跳到备用 `POP_TOP`，true
分支则弹出左值、计算右值并由另一处 `POP_TOP` 清理。线性解析 true 分支后栈已经
清空，进入备用 cleanup 时 `_resolve_booleans()` 再次 `_pop_expr()`，产生栈下溢。

修复在结构层验证全部分支都以精确的
`POP_TOP; LOAD_CONST None; RETURN_VALUE` 结束、无后继/回边/异常边/外部入口，
然后以这些 `POP_TOP` 为 expression terminal 调用既有 CFG-aware expression
recovery，恢复为：

```python
godpanel and godpanel.binding(False)
settingpanel and settingpanel.update_godappbtn()
```

没有修改 `_resolve_booleans()`，也没有在空栈时跳过 `POP_TOP`。

最终真实样本静态验证：

```text
完整反编译：成功，source_bytes=304709，syntax=OK
原 fixed.pyc：455 code objects
恢复源码重新 compile：455 code objects
co_qualname 多重集合：完全一致
received_chat_message：terminal And + Pass
godapp_bindthirdparty_cb：Expr(BoolOp(And))
godapp_unbind_notify：Expr(BoolOp(And))
错误/占位标记：0
```

外部三个输入的 SHA-256 在验证前后保持：

```text
marshal：00eb9adb0dc7ffc433dfbc29c13d12a13e0a669289ac24c176598b8069618429
参考源码：c91b24b7736e957407843dca9b73aa6670543c0424958d9061ad270a59656415
旧恢复文本：d6b05d03082ea857ab3434b4a06524dd0e2ebaec4294a89144ec7692ea76c259
```

验证期间未 import、exec、eval 或调用外部代码。
