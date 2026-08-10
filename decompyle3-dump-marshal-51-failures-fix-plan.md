# decompile3：Python 3.11 全量 Marshal 的 51 个失败修复方案

## 1. 问题结论

对 `dump/dump_marshal` 中 2,425 个 Python 3.11 Marshal 完成自定义 Opcode 映射后：

- Opcode 修复成功：2,425 / 2,425。
- decompile3 反编译成功：2,374 / 2,425，覆盖率 97.90%。
- decompile3 反编译失败：51 个。
- 51 个失败全部发生在 decompile3 的 Python 3.11 CFG、表达式栈或异常结构恢复阶段，不是自定义 Opcode 映射失败。

这些失败可以归并成 9 个修复主题，不需要为 51 个文件分别增加特判。

## 2. 失败根因分组

| 修复主题 | 数量 | 主要错误 | 代表样本 |
|---|---:|---|---|
| 分支局部的短路表达式及复制尾部清理 | 23 | `Operand stack underflow (POP_TOP)` | `GameWorld.doSelectRole` |
| finally/循环中的 held return 和物理栈清理 | 3 | `Operand stack underflow (RETURN_VALUE)` | `SubResPatcher._fetch_http_npk` |
| 普通条件分支没有被 CFG 结构器接管 | 9 | `Unsupported phase-3 opcode POP_JUMP_*` | `AutoOpenUITask.OpenCondition` |
| 目标等于下一条指令的退化条件跳转 | 2 | `Unsupported phase-3 opcode POP_JUMP_FORWARD_IF_NONE` | `threading.Thread._stop` |
| 无直接回边、通过迭代器清理退出的 `for` | 4 | `FOR_ITER has neither a loop-back nor break edge` | `com.RPCCache.update` |
| lambda `_FunctionValue` 没有转换为表达式 | 4 | `Expected an expression ... _FunctionValue` | `bson.<module>` |
| 结构化区域和嵌套表达式的边界错误 | 2 | `Structured statement region left stack values` | `re._parser._parse` |
| 推导式回边及跳转 trampoline 识别错误 | 2 | comprehension/back-edge 错误 | `avatarmembers.JieJieMember.<listcomp>` |
| 异常 handler、循环和 `SWAP` 的所有权错误 | 2 | handler return / `SWAP_STACK` | `module.chat.PlayerMsg`、`cinematic.datacommon` |

合计：51 个。

## 3. 根因一：分支局部短路表达式的复制清理没有被接管

### 3.1 影响范围

23 个 `POP_TOP` 栈下溢具有一致的尾部模板：

```text
LOAD_FAST value
JUMP_IF_FALSE_OR_POP false_cleanup
...                         # 右侧调用
POP_TOP
LOAD_CONST None
RETURN_VALUE
false_cleanup:
POP_TOP
LOAD_CONST None
RETURN_VALUE
```

代表字节码：

```text
74  JUMP_IF_FALSE_OR_POP  to 122
76  ... accountentity.selectRole(...)
116 POP_TOP
118 LOAD_CONST            None
120 RETURN_VALUE
122 POP_TOP
124 LOAD_CONST            None
126 RETURN_VALUE
```

这通常对应分支中的短路表达式语句，例如：

```python
accountentity and accountentity.selectRole(role_id)
```

该表达式所在分支终止后，编译器为不同出口复制了 `POP_TOP + implicit None return`。当前 `_terminal_short_circuit_statement_plan()` 只接受：

- `region_end == len(self.tokens)`；
- 整个函数末尾的终止短路表达式；
- 所有 `POP_TOP` 都属于同一个全函数计划。

当相同模板位于外层 `if` 的局部分支中时，计划拒绝接管。之后条件值已经被表达式恢复逻辑消费，结构解析器再次执行 `false_cleanup` 的 `POP_TOP`，于是报栈下溢。

### 3.2 修改位置

- `decompyle3/controlflow/structures.py`
  - `_terminal_short_circuit_statement_plan()`
  - `_try_terminal_short_circuit_statement()`
  - `_capture_region()`
  - `_parse_region()`

### 3.3 修复方案

把“terminal short-circuit”从“全函数终止结构”扩展为“CFG 证明闭合的分支局部终止结构”：

1. 允许 `region_end < len(self.tokens)`，但必须证明当前 region 是一个封闭子图。
2. 从 region entry 只沿 normal edge 遍历，所有出口必须为：
   - `POP_TOP → LOAD_CONST None → RETURN_VALUE`；或
   - 当前 region 已证明拥有的等价隐式返回尾部。
3. 所有短路条件块只能使用 `JUMP_IF_FALSE_OR_POP` 或 `JUMP_IF_TRUE_OR_POP`。
4. 每个条件出口必须精确指向对应 cleanup block。
5. cleanup block 不能存在 region 外的后向 predecessor；region 外前驱只允许进入 entry block。
6. 将所有已证明属于表达式的 `POP_TOP` 和隐式返回加入 owned/suppressed offsets，避免 `_dispatch()` 再次消费。
7. 使用 `recover_expression311()` 恢复一个 `ast.BoolOp`，在 AST 中只输出一次 `ast.Expr`。

不能简单忽略空栈上的 `POP_TOP`。只有 CFG、出口模板和 predecessor 所有权同时成立时才能消费清理指令。

### 3.4 回归测试

至少覆盖：

- `a and call()`、`a or call()`。
- 上述表达式位于函数末尾。
- 上述表达式位于 `if/elif/else` 的局部分支中，分支之后还有代码。
- 左侧 truthy/falsy 时，右侧调用次数和顺序与原函数一致。
- 人为增加外部 predecessor 后必须继续 fail-closed。

预计修复：23 个。

## 4. 根因二：held return、finally 和迭代器物理栈没有进入 AST 栈模型

### 4.1 影响范围

3 个失败发生于 `RETURN_VALUE`：

- `SubResPatcher._fetch_http_npk`
- `prePatch.download_const.DirectDownload.start_download`
- `avatarmembers.SquareMember.five_anniversary_party_chat_message_cb`

前两个具有类似模板：

```text
... close()
POP_TOP
LOAD_CONST None
STORE_FAST response
RETURN_VALUE
RETURN_VALUE
PUSH_EXC_INFO
...
```

`RETURN_VALUE` 使用的是 finally 协议跨清理区保存的 held return value，而不是紧邻它的 `STORE_FAST` 结果。结构恢复在切分 try/finally region 时丢失了该物理栈值。

第三个样本是循环返回路径中的 iterator cleanup：`POP_TOP` 先移除物理迭代器，再执行 `LOAD_CONST None; RETURN_VALUE`。

### 4.2 修改位置

- `decompyle3/controlflow/structures.py`
  - `_for_iterator_return_cleanup()`
  - `_for_iterator_cleanup_before_return()`
- `decompyle3/controlflow/exception_structures.py`
  - try/finally normal-copy 和 return frontier 恢复逻辑
- `decompyle3/controlflow/exception_regions.py`

### 4.3 修复方案

1. 为异常结构恢复结果增加明确的 held-return ownership，而不是依赖普通 AST expression stack。
2. 当 exception table 和 CFG 共同证明一条路径正在携带返回值穿过 finally copy 时，在 AST 层直接生成 `ast.Return(value=held_value)`。
3. `SWAP 2 / POP_TOP` 或单独 `POP_TOP` 只有在活动 `_LoopContext` 且 CFG 证明其用于移除 iterator 时才能作为物理清理跳过。
4. 区分以下三类值：
   - AST 源表达式值；
   - CPython 协议临时值；
   - 跨 finally 保存的控制转移值。
5. region 切分时必须把控制转移值所有权传给子 region，不能用普通 `self.stack = []` 丢弃。

预计修复：3 个；同时可能解决第 10 节中的一个 `SWAP_STACK` 样本。

## 5. 根因三：普通条件分支落入 straight-line `_dispatch()`

### 5.1 影响范围

9 个正常条件跳转未被 `_bounded_condition_plan()`、`_if_statement()` 或表达式恢复路径接管，最终落到 `base.py::_dispatch()`，报：

```text
Unsupported phase-3 opcode POP_JUMP_FORWARD_IF_FALSE/TRUE
```

代表样本：

- `AutoOpenUITask.AllCollectTips_Task.OpenCondition`
- `UniCineDriver.Movie.DirectorManager.setCameraByPreviewInfo`
- `avatarmembers.ChatMember.received_chat_message`
- `com.ChatData.ChatMsg.getChannels`
- `module.peishi_new.PeiShiLogic.get_zhujue_addition_sfx_data`
- `scenemembers.HeroRoomScene.trySwitchToTenGamble`

这些不是未知 Opcode，而是 CFG structurer 没能为合法分支建立 condition plan。

### 5.2 修改位置

- `decompyle3/controlflow/structures.py`
  - `_bounded_condition_plan()`
  - `_condition_jump()`
  - `_if_statement()`
  - `_try_inline_if_expression()`
  - terminal/implicit-return plan 系列函数
- `decompyle3/controlflow/dominators.py`
- `decompyle3/controlflow/cfg.py`

### 5.3 修复方案

1. 对每个未接管的条件跳转输出调试证据：entry block、两个 successor、join/post-dominator、region 边界和拒绝原因。
2. 允许 condition plan 包含：
   - 多级 `and/or`；
   - 分支内提前 `return/continue/break`；
   - 同一判断链中的多个 false/true endpoint；
   - 外层循环中的前向分支，但不能把真实 loop back-edge 当成表达式边。
3. join 应由 immediate post-dominator 和 region ownership 决定，不应只依赖“最后一个 `JUMP_FORWARD`”的局部形状。
4. 若一侧终止而另一侧继续，把终止侧恢复成无 `else` 的 guard clause；不要强制制造错误的 `else`。
5. 计划失败时继续 fail-closed，但异常中应包含具体拒绝原因，便于下一轮覆盖测试定位。

预计修复：9 个。

## 6. 根因四：目标等于下一条语义指令的退化条件跳转

### 6.1 影响范围

2 个样本的 jump target 与 fallthrough 完全相同：

```text
LOAD_FAST lock
POP_JUMP_FORWARD_IF_NONE to 20
20 LOAD_CONST True
```

```text
LOAD_FAST default
POP_JUMP_FORWARD_IF_NONE to 8
8 PUSH_NULL
```

这类跳转不改变 CFG 路径，但会弹出测试值。当前 condition planner 无法构造两个不同 endpoint，随后 straight-line parser 将其视为未支持 Opcode。

### 6.2 修复方案

在 CFG/normalized instruction 层增加 degenerate conditional 规则：

1. 若条件跳转的语义 target 等于下一条语义指令，则两条边合并为同一个 successor。
2. 仍然执行条件表达式所需的副作用；只有纯 load 才能直接丢弃 AST 值。
3. 对有副作用的测试表达式，应输出一次 `ast.Expr(test)`，不能把整个表达式删除。
4. 不生成 `if test: pass`，因为它会改变源码结构且没有必要。

预计修复：2 个。

## 7. 根因五：`FOR_ITER` 的 break/外层循环转移没有显式跳转

### 7.1 影响范围

4 个样本的 `FOR_ITER` 没有直接回到自身 header 的 `JUMP_BACKWARD`，也没有当前实现要求的 `JUMP_FORWARD` break：

- `DynamicConfigData.<module>`
- `avatarmembers.HeroGrowthMember.get_hero_set_other_reward_cb`
- `com.RPCCache.RpcCacheManager.update`
- `module.xinhuiliu.logic.HLLogic.get_jiechu_hero_index`

最小代表形状：

```text
GET_ITER
FOR_ITER      to 50
STORE_FAST    key
LOAD_FAST     key
STORE_FAST    indexId
POP_TOP                 # 移除 iterator，相当于 break
50 LOAD_FAST  indexId
RETURN_VALUE
```

嵌套循环还可能是：

```text
inner FOR_ITER
...
POP_TOP                 # 清理 inner iterator
JUMP_BACKWARD outer_for # 回到外层循环
```

### 7.2 修改位置

- `decompyle3/controlflow/structures.py::_for_loop()`
- `decompyle3/controlflow/structures.py::_jump_control()`
- iterator cleanup helper

### 7.3 修复方案

1. `latch is None` 时，不要只搜索 `JUMP_FORWARD` break。
2. 识别 body 尾部的 iterator-removal `POP_TOP`：
   - 必须位于活动 FOR 的 CFG 路径；
   - 栈效果必须对应移除 iterator；
   - successor 必须是该 FOR 的 exit，或 enclosing loop 的合法 continue/latch。
3. 对 AST body 追加 `ast.Break()`；物理 `POP_TOP` 不作为源表达式输出。
4. 嵌套循环中，inner iterator cleanup 和 outer loop transfer 必须分别归属，不能把 outer latch 误认为 inner latch。
5. 保留当前“无 latch、无 break、无终止指令则 fail-closed”的保护。

预计修复：4 个。

## 8. 根因六：lambda `_FunctionValue` 没有统一转成 AST 表达式

### 8.1 影响范围

4 个失败分为两种：

1. 字典增量构造：

```text
LOAD_CONST <lambda code>
MAKE_FUNCTION
MAP_ADD
```

2. 短路赋值：

```text
LOAD_DEREF resolve
JUMP_IF_TRUE_OR_POP target
LOAD_CONST <lambda code>
MAKE_FUNCTION
target: STORE_DEREF resolve
```

`_pop_exprs()` 已经会通过 `_expression_value()` 把 lambda `_FunctionValue` 转为 `ast.Lambda`，但 `_pop_expr()` 仍只接受 `ast.expr`。因此 `MAP_ADD`、boolean resolve 或 `STORE_DEREF` 路径会拒绝合法 lambda。

### 8.2 修改位置

- `decompyle3/parsers/p311/base.py`
  - `_pop_expr()`
  - `_expression_value()`
  - `_collection_add()`
  - `_resolve_booleans()`

### 8.3 修复方案

统一单值和多值表达式转换：

```python
def _pop_expr(self):
    return self._expression_value(self._pop())
```

其中 `_expression_value()` 仍必须只允许：

- 普通 `ast.expr`；
- `co_name == '<lambda>'` 的 `_FunctionValue`。

普通 `def`、class、import transaction 等 parser-only value 仍必须 fail-closed，不能被错误转成表达式。

预计修复：4 个。

## 9. 根因七：嵌套条件表达式被当作 statement region

### 9.1 `re._parser._parse`

失败指令位于一次函数调用参数中的多级条件表达式：

```text
this == '-' ? 'difference'
this == '&' ? 'intersection'
this == '~' ? 'symmetric difference'
              'union'
join:
LOAD_FAST source
...
CALL warnings.warn
```

各分支都在同一 join 上产生一个字符串值。当前 `_try_inline_if_expression()` 没有完整接管多臂 if-expression，statement structurer 分别捕获分支后留下字符串栈值，最终触发：

```text
Structured statement region left stack values
```

修复方式：

1. 在 statement condition plan 之前，识别多个 `JUMP_FORWARD` 汇合到同一 join 的 value-producing CFG。
2. 使用 `recover_expression311()` 恢复整个闭合表达式切片，并向外层 AST stack 只压入一个值。
3. 所有分支在 join 处必须具有相同栈高度和兼容的表达式值，否则继续 fail-closed。

### 9.2 `module.quick_msg.QuickMsgMgr.add_invite`

该样本是 `for + try/except + 条件 return`：成功分支先 `POP_TOP` 清理 iterator，再 `return None`；异常分支回到循环 header。修复时需要让 loop context 进入异常 body/callback region，并复用第 4、7 节的 iterator cleanup ownership。

预计修复：2 个。

## 10. 根因八：推导式语义回边和跳转 trampoline

### 10.1 `EXTENDED_ARG` 导致回边比较错误

`module.valentine_2021.Valentine2021Logic` 的 list comprehension：

```text
8   EXTENDED_ARG
10  FOR_ITER       to 802
...
798 EXTENDED_ARG
800 JUMP_BACKWARD  to 8
```

`ComprehensionDecompiler311._sync_loop()` 当前要求：

```python
instruction_target(jump) == for_iter_token.offset
```

实际 jump target 是 `EXTENDED_ARG` 的偏移 8，而 `FOR_ITER` 是偏移 10，所以错误报告“no back edge”。

修复方式：比较语义 target，而不是物理偏移：

```python
self._resolved_target_offset(jump) == token.offset
```

或统一使用跳过 `_IGNORED_INTERNAL` 后的 semantic header offset。

### 10.2 filter trampoline

`avatarmembers.JieJieMember` 的 list comprehension 包含：

```text
POP_JUMP_FORWARD_IF_FALSE  to trampoline
POP_JUMP_BACKWARD_IF_FALSE to loop
JUMP_FORWARD               to output
trampoline:
JUMP_BACKWARD              to loop
```

表达式 CFG 的 `_target_index()` 把 trampoline 的 `JUMP_BACKWARD` 当成“跳出表达式”，而 comprehension filter 语义上它只是 false endpoint。

修复方式：

1. 在 comprehension filter CFG 中先折叠只含无条件跳转的 trampoline block。
2. trampoline 最终到 loop header 时，将它标记为 filter false endpoint。
3. 到 output block 时标记为 true endpoint。
4. 不要全局允许 expression 跳出 region；规则只能用于已证明属于 comprehension loop 的 header/output。

预计修复：2 个。

## 11. 根因九：异常 handler `None` return 和 `SWAP` 所有权

### 11.1 except handler return ownership

`module.chat.PlayerMsg.create_season_dye_lapiao_msg`：

```text
PUSH_EXC_INFO
POP_TOP
... traceback.print_exc()
POP_EXCEPT
LOAD_CONST None
RETURN_VALUE
... cleanup/reraise
LOAD_FAST msgLayer
RETURN_VALUE
```

当前 `exception_structures.py::_has_unresolved_handler_none_return()` 无法证明 `None` return 属于 handler，因此 fail-closed。

修复应同时使用：

- exception table 的 handler target/range；
- normal edge 是否能到达 continuation；
- `POP_EXCEPT` 后 return block 的 predecessor；
- source position 仅作为辅助证据，不作为唯一依据。

只有 handler return block 被异常 handler 支配、且 normal continuation 不会进入该 block 时，才能把它归属于 handler。

### 11.2 loop + try 中的 `SWAP 2 / POP_TOP / RETURN_VALUE`

`cinematic.datacommon.ensure_str`：

```text
FOR_ITER
...
LOAD_FAST ss
SWAP 2
POP_TOP
RETURN_VALUE
```

物理栈包含 `[iterator, ss]`；AST stack 只包含 `ss`。因此普通 `_dispatch(SWAP_STACK)` 认为深度 2 非法。现有 `_for_iterator_return_cleanup()` 已处理类似协议，但 try region 在 `SWAP` 前切断了返回表达式所有权。

修复方式：

1. exception region 恢复必须把 `LOAD_FAST ss` 与紧随其后的 iterator cleanup/return 作为一个返回 frontier。
2. 在活动 loop context 下消费 `SWAP 2 / POP_TOP`，生成 `return ss`。
3. 普通表达式中的 `SWAP 2` 仍执行严格栈深度校验。

预计修复：2 个。

## 12. 推荐实施顺序

建议拆成独立提交，便于确认每组修改没有扩大错误接受范围。

### 第一阶段：建立失败语料基线

1. 为 51 个样本保存模块、code object、Opcode、offset 和错误类型清单。
2. 增加批量回归命令，记录：总数、成功数、失败类型计数、源码语法结果。
3. 设置成功数不得低于 2,374 的 monotonic gate。
4. 每类至少提取一个可提交的最小源码/bytecode fixture；私有完整 Marshal 可作为本地扩展语料。

### 第二阶段：低风险表达式和推导式修复

1. lambda `_FunctionValue` 统一转换：预计 +4。
2. comprehension `EXTENDED_ARG` 语义回边：预计 +1。
3. comprehension trampoline：预计 +1。

目标：2,380 / 2,425。

### 第三阶段：循环退出和短路 cleanup ownership

1. 无 latch 的 iterator cleanup/break：预计 +4。
2. 分支局部 terminal short-circuit：预计 +23。

目标：2,407 / 2,425。

### 第四阶段：条件 CFG 扩展

1. degenerate conditional：预计 +2。
2. 普通多级条件、terminal branch 和 guard clause：预计 +9。

目标：2,418 / 2,425。

### 第五阶段：异常和物理栈协议

1. held return / iterator return：预计 +3。
2. 嵌套 if-expression 和 try-loop region：预计 +2。
3. except handler return ownership：预计 +1。
4. loop/try `SWAP` cleanup：预计 +1。

最终目标：2,425 / 2,425。

阶段目标是按当前错误首次触发点估算的；前置修复后，个别文件可能暴露下一层错误，因此必须以每轮全量结果为准。

## 13. 必须增加的测试层级

### 13.1 结构单元测试

直接验证 AST 形状：

- `BoolOp`、`IfExp`、`If`、`Break`、`Continue`、`Try`、`Return`。
- branch-local region 的 owned offsets。
- iterator cleanup 不产生多余 `Expr` 或 `POP`。

### 13.2 栈和 CFG 不变量测试

- 每个 join 的抽象栈高度一致。
- region 内所有 normal edge 都有明确所有权。
- region 外 predecessor 不得被静默吞掉。
- exception edge 不得作为普通 join edge。
- parser-only value 只能通过明确转换进入 AST。

### 13.3 行为等价测试

对最小 fixture 同时执行原函数和反编译后重新编译的函数，对比：

- 返回值和异常类型。
- 短路表达式的调用次数、调用顺序和副作用。
- loop 的迭代次数、break/continue 路径。
- try/except/finally 的事件顺序。
- comprehension 的元素顺序和 filter 副作用。
- lambda 的捕获变量和延迟执行结果。

### 13.4 全量覆盖测试

每次修改后重新处理全部 2,425 个 Marshal：

1. Opcode 修复仍为 2,425 / 2,425。
2. 成功数不得低于当前基线 2,374。
3. 所有输出源码必须通过 Python 3.11 `compile(source, filename, 'exec')`。
4. 失败项必须有明确 code object、Opcode、offset 和拒绝原因。
5. 最终目标为 2,425 个全部输出源码且语法通过。

## 14. 禁止采用的修法

以下修改虽然可能提高“成功数量”，但会掩盖语义错误：

- 空栈时直接忽略 `POP_TOP`。
- `RETURN_VALUE` 无值时默认返回 `None`。
- structured region 有残留栈值时直接清空。
- 遇到未知条件跳转时默认当作普通 `if` 或无条件跳转。
- 所有 `_FunctionValue` 都转成 lambda。
- expression CFG 全局允许跳到 region 外。
- 仅依靠源码行号判断隐式/显式 `return None`。
- 为 51 个模块名或函数名增加硬编码特判。

decompile3 当前的 fail-closed 原则应保留；正确修复方式是增加可证明的 CFG、栈效果和异常表所有权规则。

## 15. 验收标准

修复完成至少应满足：

- 全量反编译：2,425 / 2,425。
- Python 3.11 语法编译：2,425 / 2,425。
- 现有 decompile3 测试全部通过。
- 新增 9 组根因测试和相应负向 corruption 测试。
- 关键代表 fixture 通过行为等价测试。
- 不通过忽略栈错误、清空残留值或硬编码模块名提高覆盖率。

完整失败文件、函数、Opcode 和偏移可参见：

- `dump/dump_marshal_decompiled/COVERAGE_SUMMARY.md`
- `dump/dump_marshal_decompiled/report.json`
- `dump/dump_marshal_decompiled/logs/`
