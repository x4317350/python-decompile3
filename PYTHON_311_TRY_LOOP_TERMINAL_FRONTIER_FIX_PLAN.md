# Python 3.11 try/except 循环终止前沿修复计划

## 1. 文档目的

本文根据以下两份分析报告，复核 `logoutput_bw.in_list` 的真实字节码和当前
decompile3 实现，固化问题根因、fail-closed 安全边界、分阶段修复步骤、动态
回归测试和真实样本验收方法：

```text
/Users/ice/Desktop/Custom/WorkCode_github/py3disassembly/docs/python311-vs-python27-source-functional-comparison.md
/Users/ice/Desktop/Custom/WorkCode_github/py3disassembly/docs/decompyle3-try-loop-terminal-frontier-fix.md
```

本轮只生成修复计划，不修改反编译器源码。

## 2. 当前基线

- 仓库：`/Users/ice/Desktop/Custom/WorkCode_github/python-decompile3`
- 分支：`master`
- 当前提交：`7526711fe7f414f387aaef273999efec63778ec1`
- 提交说明：`修复：恢复 Python 3.11 功能差异控制流`
- 运行时：CPython 3.11.9
- decompyle3：3.9.4.dev0
- 分析开始时工作树干净

真实输入：

```text
/Users/ice/Desktop/Custom/WorkCode_github/py3disassembly/dump/dump_marshal_only_use_decompiled/fixed_pyc/logoutput_bw.original.fixed.pyc
```

当前反编译输出：

```text
/Users/ice/Desktop/Custom/WorkCode_github/py3disassembly/dump/dump_marshal_only_use_decompiled/source/logoutput_bw.original.py
```

SHA-256：

```text
185c5a1d8083c31396f969f435cb30a8dc0355367f230eb405b086635a88ad27  logoutput_bw.original.fixed.pyc
fca516a1cbac6e703531ef558c544b55449edb324721f5f4c170423e02d58309  logoutput_bw.original.py
```

外部 pyc 只允许读取、扫描和反编译，不执行其中代码。

## 3. 问题复核

### 3.1 当前错误结果

当前提交可以成功反编译真实 pyc，生成源码可以解析和重新编译，原始与重建
code object 均为 16 个、限定名集合一致，但 `in_list` 被恢复为：

```python
def in_list(filename, f_list):
    for f_ptn in f_list:
        try:
            if f_ptn not in ptns:
                ptns[f_ptn] = re.compile(f_ptn)
            if ptns[f_ptn].search(filename):
                return True
        except Exception as e:
            C_debug.message('[Logger Error]: %s' % str(e))
            continue
        else:
            return True
            continue
    return False
```

这里的 `else: return True` 使“正则成功执行”和“正则实际匹配”混为一谈；随后
的 `continue` 同时成为不可达代码。

### 3.2 动态最小复现

标准 CPython 3.11 源码即可复现，不依赖自定义 Opcode：

```python
def match_any(filename, patterns, compile_pattern, report_error):
    cache = {}
    for pattern in patterns:
        try:
            if pattern not in cache:
                cache[pattern] = compile_pattern(pattern)
            if cache[pattern].search(filename):
                return True
        except Exception as error:
            report_error(error)
            continue
    return False
```

当前提交的动态差分结果：

| 输入 | 原函数 | 重建函数 | 副作用差异 |
| --- | --- | --- | --- |
| `miss1, miss2` | `False` | `True` | 重建函数只搜索 `miss1` |
| `miss, hit` | `True` | `True` | 重建函数没有搜索 `hit` |

因此这不是格式问题，也不能以 `ast.parse()`、`compile()` 或 code object 数量一致
作为通过标准。

### 3.3 同类结构均受影响

当前版本对以下标准源码也会生成虚假的 try-else：

```text
conditional_return -> else: return True; continue
conditional_break  -> else: break
normal_loop_tail   -> else: continue
```

真正包含业务调用的 try-else 当前可以恢复为 `else: callback(item); continue`，
修复时必须保留 callback 的异常边界。

## 4. 字节码与 CFG 证据

### 4.1 `match_any` 的物理间隙

最小样例的主 exception-table entry 为：

```text
[16, 106) -> handler 114
```

protected fragments 结束位置与 handler 之间为：

```text
106  POP_TOP
108  LOAD_CONST True
110  RETURN_VALUE
112  JUMP_BACKWARD -> 10
114  PUSH_EXC_INFO
```

对应 CFG：

```text
protected condition block B5
  true  -> B6 [POP_TOP, LOAD_CONST True, RETURN_VALUE]
  false -> B7 [JUMP_BACKWARD -> FOR_ITER 10]

B6: normal successor = none
B7: normal successor = loop header
```

B6、B7 都只由 protected condition block 进入。它们虽然因为不会产生普通 Python
异常而不在 exception table 中，但源码结构和 CFG 所有权仍属于 try body 的条件
终止前沿。

### 4.2 三种需支持的间隙形态

CPython 3.11 最小样例的真实 gap 分别为：

```text
conditional_return:
    POP_TOP; LOAD_CONST True; RETURN_VALUE
    JUMP_BACKWARD -> current continue target

conditional_break:
    POP_TOP; JUMP_FORWARD -> current break target
    JUMP_BACKWARD -> current continue target

normal_loop_tail:
    JUMP_BACKWARD -> current continue target

real_try_else:
    PUSH_NULL; LOAD callback; LOAD item; PRECALL; CALL; POP_TOP
    JUMP_BACKWARD -> current continue target
```

前三种只含循环清理和终止控制转移；第四种含业务调用，必须拒绝“终止前沿”
认领并继续恢复为真正的 `Try.orelse`。

## 5. 根因

问题位于：

```text
decompyle3/controlflow/exception_structures.py
```

重点函数：

```text
ExceptionStructureDecompiler311._try_except
ExceptionStructureDecompiler311._protected_terminal_return_frontier_end
```

上一轮修复的 `_protected_terminal_return_frontier_end` 只接受完整 gap 由一个或多个
如下块组成：

```text
LOAD_CONST; RETURN_VALUE
```

它有意保持了严格的 fail-closed 边界，但没有覆盖循环内返回之前的物理迭代器
清理 `POP_TOP`，也没有覆盖条件为假时回到当前 loop header 的
`JUMP_BACKWARD`。

helper 拒绝 gap 后，`_try_except` 只把 handler 前的 `JUMP_FORWARD` 视为显式
正常完成跳转。目标样本以 `JUMP_BACKWARD` 结束，因此 `_capture_before_handler()`
把整个 gap 当作 orelse，最终扩大 `return` 或 `break` 的执行条件。

问题本质是 exception-table 边界之外的 CFG 块所有权遗漏，不是：

- `POP_JUMP_FORWARD_IF_FALSE` 不受支持；
- handler 中的 `continue` 识别失败；
- return 美化；
- 源码打印器格式错误。

## 6. 总体修复策略

保留现有常量返回 helper 的窄边界，新增独立的循环终止前沿证明：

```python
_protected_loop_terminal_frontier(
    fragments,
    body_end,
    handler_index,
    loop,
)
```

建议返回一个只读证据对象，而不是单独返回 index：

```python
@dataclass(frozen=True)
class _ProtectedLoopTerminalFrontier:
    end_index: int
    owned_blocks: FrozenSet[int]
    return_blocks: FrozenSet[int]
    continue_blocks: FrozenSet[int]
    break_blocks: FrozenSet[int]
    cleanup_offsets: FrozenSet[int]
```

证据对象便于单元测试直接验证所有权集合，也避免后续源码清理再次猜测 block
类型。

## 7. fail-closed 所有权算法

### 7.1 前置条件

只有同时满足以下条件才进入候选分析：

1. `loop is not None`；
2. `0 <= body_end < handler_index`；
3. gap 非空；
4. 当前 entry 是普通 try/except，而不是 try/finally 或 except* 协议；
5. 分析次数受 `max(32, len(cfg.blocks) * 2)` 之类的上限约束。

### 7.2 完整块分类

对 `[body_end, handler_index)` 内的每个 semantic token 和 CFG block 做完整覆盖，
只接受以下形状：

1. 常量返回块：

   ```text
   POP_TOP*; LOAD_CONST; RETURN_VALUE
   ```

   `POP_TOP*` 只作为循环迭代器清理；块的 terminator 必须是 `RETURN_VALUE`，且
   不得存在普通 successor。

2. continue 块：

   ```text
   JUMP_BACKWARD[_NO_INTERRUPT] -> loop.continue_targets
   ```

   必须只有一个普通 jump successor，目标必须是当前 loop context 的 continue
   target。

3. break 块：

   ```text
   POP_TOP*; JUMP_FORWARD -> loop.break_target
   ```

   必须只有一个普通 jump successor，目标严格等于当前 loop context 的
   `break_target`。

4. `_IGNORED_INTERNAL`，但它必须依附于上述已分类 block，不能单独形成带未知
   控制流的块。

gap 中出现以下任一内容立即拒绝：

- `CALL`、`LOAD_METHOD`、业务 `LOAD_ATTR`；
- STORE/DELETE 等业务状态修改；
- 条件跳转或无法解释的 fallthrough；
- RAISE/RERAISE 或异常清理协议；
- 任意没有被上述形状完整消费的 token。

### 7.3 CFG 所有权

构造 `protected_blocks` 与 `frontier_blocks` 后必须证明：

1. 每个 frontier block 不存在 exception incoming edge；
2. 每条普通 incoming edge 的 source 都在 `protected_blocks` 或
   `frontier_blocks`；
3. 至少存在一条 `protected -> frontier` 普通边；
4. 从所有 `protected -> frontier` 入口做有界遍历，必须恰好访问全部
   frontier blocks；
5. 不允许 foreign predecessor，也不允许只解释 gap 的一部分；
6. return block 无普通出口；
7. continue block 唯一出口是当前 continue target；
8. break block 唯一出口是当前 break target；
9. 除上述三类出口外，不允许任何 normal edge 离开 frontier；
10. 至少包含一个 continue 或 break block，使它与已有纯常量 return helper
    保持职责分离。

源码位置只能作为诊断或附加证据，不能代替 CFG、exception table 和 loop target
证明。位置缺失但 CFG 证据完整时可以恢复；CFG 证据不足时必须拒绝。

### 7.4 与 `_try_except` 集成

在捕获 try body 之前按以下顺序处理：

```python
terminal_end = self._protected_terminal_return_frontier_end(
    fragments,
    body_end,
    handler_index,
)

if terminal_end is None:
    loop_frontier = self._protected_loop_terminal_frontier(
        fragments,
        body_end,
        handler_index,
        loop,
    )
    if loop_frontier is not None:
        terminal_end = loop_frontier.end_index

if terminal_end is not None:
    body_end = terminal_end
```

`body_end == handler_index` 后，现有 `_capture_protected_fragments()` 会在 loop
context 中结构化：

- 条件真分支的 `Return` 或 `Break`；
- 条件假分支的 `Continue`；
- except handler 自身的 `Continue`。

此时 `_capture_before_handler()` 不再接收这些块，`Try.orelse` 自然为空。不应在
AST 生成后删除 else 或搬移 statement。

### 7.5 可行性验证

分析阶段通过仅在进程内临时把三个标准最小样例的 `body_end` 推进到 handler
（没有修改仓库源码），验证现有 region capture 已能生成：

```python
try:
    if predicate(item):
        return True       # 或 break
    continue
except Exception as error:
    report(error)
    continue
```

`normal_loop_tail` 也能恢复为 try body 尾部 `continue`。这证明实际缺口集中在
“是否允许认领 gap”的证据层；第一阶段不需要改写 AST printer 或新增跳转 opcode
处理。若正式实现的严格证明通过后仍出现 capture 差异，再按阶段 3 的门禁评估
`structures.py`，不能预先扩大修改面。

## 8. 输出策略

第一阶段只保证语义正确，允许恢复为：

```python
for pattern in patterns:
    try:
        ...
        if matched:
            return True
        continue
    except Exception as error:
        report_error(error)
        continue
return False
```

循环尾 `continue` 与自然回边等价。只有在 CFG 已证明它是当前 loop 的自然尾部、
且后面没有同层 statement 时，第二阶段才可复用已有 loop normalization 删除它。

语义修复和源码美化必须分开提交或至少分开测试，不能为了输出接近原源码而放宽
终止前沿所有权。

纯终止型 try-else（例如 `else: continue`）可能与“try body 的非抛异常终止块”
产生相同字节码。此时允许恢复为动态等价结构；含 callback、赋值或其他业务操作
的真正 try-else 必须保持 `orelse` 和原异常边界。

## 9. 不允许的修复方式

禁止采用：

1. 针对 `logoutput_bw`、`in_list` 或某个 offset 的特判；
2. 看到 `else: return/break/continue` 就删除 else；
3. 无条件删除 return 后的 continue；
4. 在 `_dispatch()` 中忽略跳转 opcode；
5. 捕获结构恢复异常后输出 `pass`、空函数或占位函数；
6. 只依据 `co_positions` 或源码行号移动 statement；
7. 手工修改真实反编译输出；
8. 只以源码可解析、可编译或 code object 数量一致作为验收。

## 10. 分阶段实施计划

### 阶段 0：基线和快照

1. 检查 `git status`、分支和 HEAD；要求基线为本计划记录的提交，或明确记录新
   基线。
2. 保存 `logoutput_bw.original.py` 修复前快照，文件名包含短 commit。
3. 记录真实 pyc、当前输出和两份问题文档 SHA-256。
4. 重新反编译真实 pyc 到临时目录，确认错误仍存在。
5. 记录完整 pytest、相关异常表测试和真实世界归档基线。

### 阶段 1：先增加失败回归

新增：

```text
pytest/test_try_loop_terminal_frontier311.py
```

同一 fixture 编译标准 Python 3.11 最小源码、调用 decompile3、执行
`ast.parse()` 和 `compile()`，再分别执行原始与重建函数。至少包含：

- `match_any`；
- `conditional_return`；
- `conditional_break`；
- `normal_loop_tail`；
- `real_try_else`。

在修复前必须稳定表现为目标用例失败、真正 try-else 负向用例通过。

### 阶段 2：实现循环前沿证据

1. 新增 `_ProtectedLoopTerminalFrontier` 和候选 helper。
2. 先实现 block/token 完整分类，不改变 `_try_except`。
3. 为 helper 写直接单元测试，验证 observed gap 的 return、break、continue 分类。
4. 加入所有 incoming/outgoing、可达性、目标和工作量证明。
5. 确认任何未知 token 或部分覆盖都返回 `None`，不抛出未包装内部异常。

### 阶段 3：接入 `_try_except`

1. 在纯常量 return helper 之后调用循环 helper。
2. 只有完整证据存在时才将 `body_end` 推进到 handler。
3. 让现有 region capture 产生 Return/Break/Continue AST，不新增输出层特判。
4. 检查 `deferred_return_body`、`normal_jump`、`normal_body_start` 和 handler join
   没有继续把前沿重复捕获为 orelse。
5. 若证据对象与实际 capture 消费范围不一致，应 fail-closed，而不是降级输出。

### 阶段 4：动态语义与负向边界

逐场景比较：

- 返回值和值类型；
- pattern 编译、predicate、search、work、callback 的调用次数与顺序；
- 错误日志次数；
- 异常类型和消息；
- cache、visited 等最终状态。

必须保留真正 try-else：正常 work 后调用 callback；work 抛目标异常时只调用
recover；callback 自身抛出的异常不能被 try 的 except 捕获。

### 阶段 5：CFG 拒绝测试

使用复制 token/CFG 的测试对象注入以下破坏：

- foreign predecessor；
- exception incoming edge；
- continue 跳到其他循环或任意反向地址；
- break 跳到非当前 `break_target`；
- gap 中插入业务 CALL、STORE 或条件跳转；
- return block 增加普通 successor；
- 删除一个 gap block，形成部分覆盖；
- 构造超过工作量上限的循环图。

所有破坏都必须拒绝候选前沿。拒绝后如果整体结构无法安全恢复，应抛出带
version、code name 和 offset 的结构化错误，不能静默成功。

### 阶段 6：真实样本门禁

1. 只读取并反编译 `logoutput_bw.original.fixed.pyc`。
2. 确认 `in_list` 不再包含虚假 `Try.orelse`。
3. 确认 `return True` 仍受 search 成功条件保护。
4. 确认条件为假和异常路径都回到循环。
5. 对生成源码执行 AST parse 和 Python 3.11 compile。
6. 递归比较原始/重建 code object 数量与限定名；预期仍为 16/16。
7. 不执行外部真实 pyc；运行时语义由标准最小样例完成。
8. 保留修复前输出，并生成修复后 diff。

### 阶段 7：全量回归

至少运行：

```bash
.venv311/bin/pytest -q \
  pytest/test_try_loop_terminal_frontier311.py \
  pytest/test_except_continue311.py \
  pytest/test_source_functional_differences311.py

.venv311/bin/pytest -q \
  pytest/test_exceptiontable311.py \
  pytest/test_exception_cleanup311.py \
  pytest/test_controlflow311.py \
  pytest/test_terminal_cleanup_regression311.py

.venv311/bin/pytest -q
```

随后重新反编译 17 个 fixed pyc，逐文件执行 parse/compile 和 code object 数量
比较；重点 diff `SubPatch`、helpers、ClientEntities 和 `logoutput_bw`，防止循环
前沿规则影响上一轮修复。

新增测试会改变真实世界输入摘要，应使用项目生成器更新归档，不能手工修改摘要：

```bash
.venv311/bin/python test/bytecode_3.11/run_realworld_regression.py
```

最后运行 `flake8`、`git diff --check` 和归档一致性测试。

### 阶段 8：文档和提交

1. 将本计划追加实施结果：最终根因、证明规则、测试结果和残余风险。
2. 更新功能对比报告中的 `logoutput_bw.in_list` 结论。
3. 更新真实世界回归 JSON/Markdown。
4. 只提交反编译器源码、测试和仓库内文档；不提交临时反编译产物。
5. 审计 staged diff，确认没有 opcode 跳过、异常吞并或真实输出手工修补。
6. 创建独立 Git commit；未经要求不 push。

## 11. 自动化测试矩阵

### 11.1 `match_any`

| 场景 | 返回 | 关键副作用 |
| --- | --- | --- |
| 空 patterns | `False` | compile/search 都不调用 |
| 第一条匹配 | `True` | 只访问第一条 |
| 第一条不匹配、第二条匹配 | `True` | 严格访问两条 |
| 全部不匹配 | `False` | 访问全部 |
| 第一条编译异常、第二条匹配 | `True` | 日志一次，继续第二条 |
| 第一条搜索异常、第二条不匹配 | `False` | 日志一次，继续第二条 |
| report_error 抛异常 | 相同异常外抛 | 不被原 except 再次吞并 |

### 11.2 控制转移家族

| 函数 | 条件假 | 条件真 | 条件抛异常 |
| --- | --- | --- | --- |
| conditional_return | 继续遍历 | 返回 `True` | report 后继续 |
| conditional_break | 继续遍历 | 只在真时 break | report 后继续 |
| normal_loop_tail | 每项执行一次 | 不适用 | report 后继续 |

### 11.3 反向保护

`real_try_else` 覆盖：

- work 成功：callback 调用一次；
- work 抛 `LookupError`：recover 调用一次，callback 不调用；
- work 抛非目标异常：原样外抛；
- callback 抛异常：原样外抛，不能被前面的 except 捕获。

## 12. 预计修改文件

源码：

```text
decompyle3/controlflow/exception_structures.py
```

测试：

```text
pytest/test_try_loop_terminal_frontier311.py
```

预计更新：

```text
PYTHON_311_TRY_LOOP_TERMINAL_FRONTIER_FIX_PLAN.md
PYTHON_311_REALWORLD_REGRESSION.md
test/bytecode_3.11/realworld_regression311.json
```

只有在现有 loop capture 无法消费已证明的 gap 时，才考虑修改
`decompyle3/controlflow/structures.py`；不得为了方便输出而在 parser dispatch 中
忽略跳转。

## 13. 风险与控制

### 高风险：吞掉真正 try-else

控制：gap token 必须全部属于无异常终止协议；任何业务 CALL、状态修改、未知
fallthrough 或外部前驱都拒绝。callback 抛异常的动态测试验证异常边界。

### 高风险：认错嵌套循环目标

控制：continue/break 必须严格匹配当前 `_LoopContext`，不能只根据跳转方向或地址
大小判断；嵌套循环和 foreign target 必须有拒绝测试。

### 中风险：POP_TOP 含义不唯一

控制：POP_TOP 不能单独作为证据，只能位于完整 return/break 终止块中，并同时
满足 CFG 所有权和 loop target 证明。gap 中的业务表达式仍会因为其 LOAD/CALL
无法分类而被拒绝。

### 中风险：冗余 continue 清理过度

控制：第一阶段允许保留自然尾 continue；只有独立证明后才美化，不能和语义修复
共享宽松启发式。

### 低风险：源码结构与原文不完全一致

终止型 try-else 可能恢复为动态等价的 try body transfer。验收以返回值、类型、
副作用顺序和异常边界为准，不要求文本完全相同。

## 14. 完成定义

只有同时满足以下条件才可宣布完成：

1. 标准最小样例不再产生虚假 `Try.orelse`。
2. `match_any` 的全部动态场景与原函数完全一致。
3. conditional return、break、continue 家族语义一致。
4. 含业务 callback 的真正 try-else 保持正确异常边界。
5. 所有 CFG 破坏样例都拒绝前沿所有权。
6. 没有跳过 opcode、吞掉结构异常、输出占位函数或手工修改结果。
7. 真实 `logoutput_bw.in_list` 的 return 条件和循环回边恢复正确。
8. 真实输出可重新编译，code object 和限定名保持 16/16。
9. 17 个真实 fixed pyc、上一轮三个功能修复和 ClientEntities 无回归。
10. 相关测试、完整 pytest、真实世界 604 文件归档和风格检查全部通过。
11. 修复前快照、实施报告、测试结果和 Git commit 均完整记录。

## 15. 实施结果（2026-08-06）

### 15.1 最终根因

本轮确认错误由两个连续的控制流判断缺口造成：

1. `_protected_terminal_return_frontier_end()` 只接受纯
   `LOAD_CONST/RETURN_VALUE` gap，没有覆盖循环迭代器清理 `POP_TOP`、当前循环
   continue 回边和 break 出边，因此拒绝了源码 try body 的非抛异常终止块。
2. `_try_except()` 在前沿证明失败后只特别识别 handler 前的
   `JUMP_FORWARD`。目标 gap 以 `JUMP_BACKWARD` 结束，于是整个 return/break/
   continue 前沿被交给 `_capture_before_handler()`，错误生成 `Try.orelse`。

### 15.2 实现

在 `exception_structures.py` 中新增只读证据对象
`_ProtectedLoopTerminalFrontier` 和
`_protected_loop_terminal_frontier()`：

- 完整分类 gap 中的常量 return、当前 loop continue 和当前 loop break block；
- `POP_TOP` 只能作为完整 return/break 终止块的前缀；
- 每个 block 禁止 exception incoming/outgoing edge；
- 所有普通前驱必须来自 protected fragments 或同一 frontier；
- 至少存在一个 protected-to-frontier 入口，并通过有界遍历恰好覆盖全部 gap；
- return 不得有普通后继，continue/break 唯一出口必须严格匹配当前
  `_LoopContext`；
- gap 出现 CALL、STORE、条件跳转、未知 fallthrough 或任何未消费 token 时拒绝。

只有完整证据成立时，`_try_except()` 才把 `body_end` 推进到 handler。随后继续
使用已有 region capture 恢复 Return/Break/Continue，没有修改 AST printer、
没有跳过 opcode，也没有捕获结构异常后输出 pass 或占位函数。

### 15.3 新增测试

新增 `pytest/test_try_loop_terminal_frontier311.py`，共 16 项：

- `match_any` 的空列表、首条命中、第二条命中、全部不匹配、编译异常、搜索异常
  和错误报告异常；
- conditional return、conditional break 和自然 loop tail；
- 含业务 callback 的真正 try-else 及 callback 异常边界；
- return/continue/break gap 的直接分类证据；
- 真正业务 try-else gap 的拒绝；
- foreign predecessor、exception edge、错误 continue target、错误 break target、
  业务指令、return 普通后继和部分 gap 共 7 类 CFG 破坏。

动态测试会执行标准最小源码和反编译后重建源码，比较返回值和值类型、异常类型和
消息、调用次数及顺序、错误报告和 visited 状态，不只比较文本或语法。

### 15.4 真实样本

`logoutput_bw.in_list` 当前恢复为：

```python
for f_ptn in f_list:
    try:
        ...
        if ptns[f_ptn].search(filename):
            return True
        continue
    except Exception as e:
        C_debug.message(...)
        continue
return False
```

尾部 `continue` 是当前循环的已证明自然回边，虽然可省略但语义正确。本轮没有把
源码美化和所有权证明混在同一启发式中。

- 真实 pyc 只读取和反编译，没有执行其中代码；
- 生成源码 AST parse 和 Python 3.11 compile 通过；
- 原始/重建 code object 与限定名保持 16/16；
- 修复前输出 SHA-256：
  `fca516a1cbac6e703531ef558c544b55449edb324721f5f4c170423e02d58309`；
- 修复后输出 SHA-256：
  `00e44fbd4cde9345b6e8352a322ae2088ab705959be078a27ff2e7e898265fa2`；
- 修复前快照：
  `logoutput_bw.original.decompyle3.before-7526711f-try-loop-frontier.py`。

### 15.5 回归结果

```text
新增 try-loop 动态与 CFG 测试                              16 passed
相关异常表、except-continue、terminal 和控制流测试         213 passed
真实 logoutput_bw                                          syntax/compile passed, 16/16
17 个真实 fixed pyc                                        17/17 passed
真实语料 code object                                       2465/2465
真实世界回归                                               604/604, fail-closed 0
完整 Python 3.11 pytest                                    1066 passed, 6 skipped
flake8                                                     passed
git diff --check                                           passed
```

17 文件修复前后只有 3 个输出发生变化：`logoutput_bw` 修复目标错误，
ClientEntities 将语义等价的虚假 `else: continue` 收回 try body，helpers 和
ClientEntities 的其余变化仅为集合显示顺序。SubPatch、helpers 的三处上一轮功能
修复以及其他 14 个真实输出没有新增控制流差异。

完整 pytest 在归档更新前为 1065 passed、6 skipped，唯一失败是自动归档输入摘要
随源码变化而过期。项目生成器重新生成真实世界 JSON/Markdown 后，最终完整复跑
为 1066 passed、6 skipped。
