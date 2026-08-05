# Python 3.11 普通 except handler 控制关键 return 修复计划

## 1. 目标

修复 CPython 3.11 普通 `try/except` 结构中，异常处理分支的显式裸
`return` 或 `return None` 被错误清理成 `pass`，导致 handler 落入
try/except 之后正常 continuation 的问题。

目标案例：

```python
try:
    delta_time = next(my_fun)
    if delta_time is not None:
        next_time = next_time + delta_time
except StopIteration:
    return

gameWorld.yield_fun_list.append((my_fun, next_time, owner_checker))
```

当前错误结果：

```python
try:
    delta_time = next(my_fun)
    if delta_time is not None:
        next_time = next_time + delta_time
except StopIteration:
    pass

gameWorld.yield_fun_list.append((my_fun, next_time, owner_checker))
```

修复必须恢复 handler 的控制转移，确保 `StopIteration` 路径不会把已经结束的
生成器加入 `gameWorld.yield_fun_list`。

本计划只处理普通 `except` handler 中可严格证明的 None-return 控制转移。
不得通过全局保留或全局删除所有 `LOAD_CONST None; RETURN_VALUE` 解决。

## 2. 当前基线

### 2.1 仓库基线

计划固化时：

```text
仓库：/Users/ice/Desktop/Custom/WorkCode_github/python-decompile3
提交：6b5cd50e3b5cf3dd43e47f40ed1ee61938809ff0
提交说明：修复：支持 Python 3.11 终止型 if/else 恢复
工作区：干净
最近全量测试：900 passed, 6 skipped
真实语料：604/604 成功反编译，0 unexpected crash
```

阶段 0 必须重新运行全量测试并记录当时的实际结果，不能只引用上述历史结果。

### 2.2 外部目标

只读目标：

```text
/Users/ice/Desktop/Custom/WorkCode_github/py3disassembly/dump/test/
Globals.original.marshal
```

Python 2.7 参考源码：

```text
/Users/ice/Desktop/Custom/WorkCode_github/py3disassembly/dump/test/Globals.py
```

当前 Python 3.11 恢复源码：

```text
/Users/ice/Desktop/Custom/WorkCode_github/py3disassembly/dump/test/
Globals.original.decompyle3.py
```

已记录外部 marshal SHA-256：

```text
6aafa80c3cb1747df7586c060c0fb6442198a59d11390d7f1568c807e7ab3693
```

外部 marshal/pyc 只允许读取、静态转换、扫描、反编译、`ast.parse()` 和
`compile(..., "exec")`。不得 import、exec 或调用其中任何函数。

### 2.3 已确认的行为差异

最小原生 CPython 3.11 fixture：

```python
def schedule(iterator, events):
    try:
        next(iterator)
    except StopIteration:
        return
    events.append("scheduled")
```

当前恢复结果：

```python
def schedule(iterator, events):
    try:
        next(iterator)
    except StopIteration:
        pass
    events.append("scheduled")
```

已确认差分行为：

```text
iterator=[]
original events=[]
recovered events=['scheduled']

iterator=[1]
original events=['scheduled']
recovered events=['scheduled']
```

这不是源码美化问题，而是异常路径错误落入正常 continuation 的实际行为错误。

### 2.4 `yield_fun_new.wrapper` 的字节码证据

目标 code object：

```text
yield_fun_new.<locals>.wrapper
```

相关异常表：

```text
274..320 -> 322 depth=0 lasti=False
322..342 -> 350 depth=1 lasti=True
348..350 -> 350 depth=1 lasti=True
```

正常 try 路径：

```text
274 ... next(my_fun)
306 ... if delta_time is not None
320 JUMP_FORWARD 356
```

`StopIteration` handler：

```text
322 PUSH_EXC_INFO
324 LOAD_GLOBAL StopIteration
336 CHECK_EXC_MATCH
338 POP_JUMP_FORWARD_IF_FALSE 348
340 POP_TOP
342 POP_EXCEPT                 line 812
344 LOAD_CONST None
346 RETURN_VALUE
348 RERAISE 0
350 COPY_STACK 3
352 POP_EXCEPT
354 RERAISE 1
```

try/except 之后的正常 continuation：

```text
356 ... gameWorld.yield_fun_list.append(...)
424 LOAD_CONST None
426 RETURN_VALUE
```

offset 346 的 `RETURN_VALUE` 没有正常后继，明确阻止异常路径到达 offset 356。

### 2.5 真正 pass handler 的对照

同一外部文件的 `yield_fun.<locals>.wrapper` 中，真正的：

```python
except StopIteration:
    pass
```

对应协议为：

```text
174 POP_EXCEPT
176 JUMP_FORWARD 186
```

因此两类 handler 可以通过控制流严格区分：

| 源码语义 | handler 尾协议 | 到 continuation |
|---|---|---:|
| `pass` | `POP_EXCEPT; JUMP_FORWARD join` | 是 |
| `return` / `return None` | `POP_EXCEPT; LOAD_CONST None; RETURN_VALUE` | 否 |

## 3. 根因

### 3.1 显式 return 的行号落在 POP_EXCEPT

CPython 3.11 将 handler 中显式 `return` 的源码行号标记在异常状态清理指令：

```text
342 POP_EXCEPT line=812
344 LOAD_CONST None line=None
346 RETURN_VALUE line=None
```

不能根据 `LOAD_CONST None` 自身没有 `linestart`，判断它是编译器隐式函数返回。

### 3.2 异常协议层正确隐藏 POP_EXCEPT，但同时丢失 return 上下文

`decompyle3/controlflow/exception_structures.py` 中：

- `_handler_protocol_offsets()` 将 `POP_EXCEPT` 标记为非源码异常协议；
- `_capture_handler_clause()` 在 suppress 该协议后捕获 clause body；
- 对命名 handler，还会 suppress 编译器生成的名称清理序列。

隐藏 `POP_EXCEPT` 本身是正确的，但当前没有把“该 POP_EXCEPT 后紧跟一个控制关键
None-return”作为结构信息传递给 region parser。

### 3.3 通用 return 清理误删 handler return

`decompyle3/parsers/p311/base.py::_return()` 当前对 None-return 使用以下近似规则：

1. 整个 code object 最后一个 `RETURN_VALUE` 可省略；
2. 非末尾 None-return 只有在前一 `LOAD_CONST None` 有 `linestart` 时才认为显式；
3. 否则省略。

handler 中的 `LOAD_CONST None` 没有行号，因此 offset 346 的控制关键 return 被
错误省略。

### 3.4 空 handler 被补成 pass

return 被省略后，`_parse_handlers()` 得到空 body，并按合法 AST 要求补充：

```python
ast.Pass()
```

最终把真实的提前退出错误恢复成正常落入 continuation。

### 3.5 append 后的 return 不能补偿

当前最新恢复结果还可能在 append 后输出一个冗余裸 `return`。该 return 属于外层
terminal-if 的保守 fallback，执行位置已经晚于 append，无法替代 handler 中
offset 346 的提前退出。

## 4. 修复范围和非目标

### 4.1 需要修改的层

主要修改：

```text
decompyle3/controlflow/exception_structures.py
```

可能增加只读上下文 hook：

```text
decompyle3/parsers/p311/base.py
decompyle3/controlflow/structures.py
```

优先在异常结构层识别并显式生成 `ast.Return(value=None)`，避免改变所有普通函数、
循环、async generator 和 terminal-if 的通用 return 策略。

### 4.2 不需要修改的层

初始修复不应修改：

- Python 3.11 scanner；
- exception table decoder；
- CFG builder；
- opcode normalizer；
- Python 2.x/3.7 grammar；
- `except*` 空主体和 cleanup matcher；
- 源码打印器。

当前 normalized token、exception table 和 CFG 已准确表达 handler return，错误
发生在结构恢复和 return 省略阶段。

### 4.3 非目标

本计划不同时处理：

- terminal `if a and b` 产生的冗余 `return`；
- `return` 与 `return None` 的文本级精确区分；
- 任意缺少行号的 None-return 全局恢复；
- 任意异常 cleanup 的宽松猜测；
- `except*`、`with`、`finally` 的新形态扩展；
- 外部 `Globals` 的完整文本一致性。

`return` 与 `return None` 在当前目标上行为等价。初始实现统一恢复为裸
`ast.Return(value=None)`，优先修复控制流语义。

## 5. 安全边界

必须保持 fail-closed：

1. 不得仅因 clause 中出现 `LOAD_CONST None; RETURN_VALUE` 就恢复 return；
2. 必须证明 try 的正常路径存在 handler 之后的 continuation；
3. 必须证明目标 handler 的 None-return 没有正常边到该 continuation；
4. 必须验证 `POP_EXCEPT`、可选名称清理、`LOAD_CONST None` 和
   `RETURN_VALUE` 的完整顺序；
5. return block 不得有正常后继；
6. return block 不得有 foreign predecessor；
7. candidate 不得跨越当前 handler clause 或异常表边界；
8. exception cleanup target 必须与当前 handler state 匹配；
9. 所有图遍历必须迭代、有 work limit；
10. 无法证明时不得静默输出 `pass`；如果空 body 中存在未解释的 terminal
    transfer，必须抛出带 code name 和 offset 的稳定 Parser311 错误。

特别需要区分函数末尾真正的隐式返回：

```python
def terminal_pass(iterator):
    try:
        next(iterator)
    except StopIteration:
        pass
```

该函数的 handler 也可能以：

```text
POP_EXCEPT; LOAD_CONST None; RETURN_VALUE
```

结束，但 try 正常路径没有独立的后续 continuation，handler return 与函数自然
结束语义等价。初始 matcher 不得将它强制认定为显式早退。

## 6. 目标判定表

| 形态 | continuation | handler 尾部 | 目标结果 |
|---|---:|---|---|
| `except E: return` 后有语句 | 有 | `POP_EXCEPT; None; RETURN` | 恢复裸 `return` |
| `except E: return None` 后有语句 | 有 | 同上 | 恢复裸 `return` |
| `except E as e: return` 后有语句 | 有 | name cleanup + `None; RETURN` | 恢复裸 `return` |
| `except E: pass` 后有语句 | 有 | `POP_EXCEPT; JUMP join` | 保持 `pass`/正常继续 |
| handler 返回非 None | 有/无 | value cleanup + `RETURN` | 保持现有 return value |
| terminal `except E: pass` | 无独立 continuation | `POP_EXCEPT; None; RETURN` | 不强制生成 return |
| cleanup 形态不完整 | 任意 | 未知 | fail-closed |
| `except*` handler | 任意 | PREP_RERAISE_STAR 协议 | 不进入本 matcher |

## 7. 预计修改范围

| 文件 | 计划修改 |
|---|---|
| `decompyle3/controlflow/exception_structures.py` | 建立 handler terminal-transfer 计划并在 clause 捕获时恢复 Return |
| `decompyle3/parsers/p311/base.py` | 仅在确有需要时增加默认关闭的上下文 hook；禁止全局放宽 |
| `pytest/test_exceptiontable311.py` | 增加 AST、协议、fail-closed 和行为回归 |
| `pytest/test_reliability311.py` | 增加最小差分行为或真实形态回归 |
| `test/fixtures311/except_handler_return.py` | 保存 canonical 普通 except-return fixture |
| `pytest/behavior_cases311.py` | 增加独立行为探针 |
| `test/bytecode_3.11/shape_matrix.json` | 增加 `except_handler_return` shape |
| coverage/release/realworld 报告 | 门禁通过后更新 |
| 本计划 | 填写执行记录 |

外部 `Globals*` 文件不得提交到本仓库。

## 8. 阶段 0：冻结失败基线

任务：

- [ ] 检查 `git status` 和当前提交；
- [ ] 运行全量 pytest，记录实际基线；
- [ ] 新增最小 bare-return fixture；
- [ ] 新增 named-return 和 real-pass 对照；
- [ ] 固定 normalized token、exception table 和 CFG；
- [ ] 固定恢复 AST 中 handler 为 `Pass` 的当前错误；
- [ ] 固定空 iterator 错误执行 continuation 的行为差异；
- [ ] 记录外部 marshal SHA-256，不提交外部文件。

最小 fixture：

```python
def bare_return(iterator, events):
    try:
        next(iterator)
    except StopIteration:
        return
    events.append("after")


def real_pass(iterator, events):
    try:
        next(iterator)
    except StopIteration:
        pass
    events.append("after")
```

修复前必须稳定断言：

- bare-return 的 handler body 错误为 `ast.Pass`；
- real-pass 的 handler body 正确为 `ast.Pass`；
- 空 iterator 在 recovered bare-return 中错误记录 `after`；
- 非空 iterator 在两者中均记录 `after`；
- scanner、exception table 和 CFG 与 `dis` 一致。

阶段完成标准：

- [ ] 失败由最小原生 CPython 3.11 fixture 稳定复现；
- [ ] 根因定位到 handler return 清理，不是 scanner/normalizer；
- [ ] 阶段 0 不修改生产实现。

## 9. 阶段 1：建立 handler terminal-transfer 计划

在 `ExceptionStructureDecompiler311` 中增加私有、不可变计划模型，例如：

```python
@dataclass(frozen=True)
class _HandlerTerminalTransfer:
    kind: str
    body_start: int
    body_end: int
    cleanup_start: int
    return_load_index: int
    return_index: int
    continuation_offset: int
    protocol_offsets: FrozenSet[int]
```

实际字段可调整，但必须显式携带：

- clause 边界；
- cleanup 边界；
- return 指令位置；
- 正常 continuation；
- matcher 已证明拥有的协议 offset。

建议 helper：

```text
_handler_none_return_plan(...)
_handler_normal_continuation(...)
_validate_handler_return_ownership(...)
```

判定条件：

- [ ] 当前结构是普通 `except`，不是 `except*`；
- [ ] `_try_except()` 已得到正常路径的 `normal_join`；
- [ ] `normal_join` 位于 handler cleanup 之后；
- [ ] handler success path 不跳向 `normal_join`；
- [ ] handler success path 以 `RETURN_VALUE` 结束；
- [ ] return value 精确为常量 `None`；
- [ ] return 前异常状态和名称清理协议完整；
- [ ] return block 无正常后继；
- [ ] 无 foreign predecessor 或跨 clause exception edge；
- [ ] matcher 使用 normalized offset，不猜原始字节位置；
- [ ] matcher 有明确 work limit。

阶段 1 只建立并单测 plan，不改变最终输出。

阶段完成标准：

- [ ] bare-return 生成唯一计划；
- [ ] explicit-None-return 生成同类计划；
- [ ] named-return 生成计划并记录名称清理；
- [ ] real-pass 不生成计划；
- [ ] terminal-pass 不生成需要强制恢复的计划；
- [ ] 损坏 cleanup 或未知 continuation 不生成计划。

## 10. 阶段 2：在异常 clause 层恢复控制关键 return

优先实现方案：异常结构层显式处理，不改变通用 `_return()` 默认行为。

推荐流程：

1. `_try_except()` 在解析 handler 前确定 `normal_join`；
2. 将只读 continuation 上下文传给 `_parse_handlers()`；
3. `_clause_body()` 在普通捕获前请求 `_HandlerTerminalTransfer`；
4. plan 命中时只捕获 return 协议之前的真实 handler 语句；
5. suppress plan 明确拥有的 `POP_EXCEPT` 和名称清理；
6. 在 handler body 尾部追加 `ast.Return(value=None)`；
7. handler body 非空，不再触发 `ast.Pass` fallback；
8. continuation 仍由 try 正常路径和真正 pass handler 使用。

不得：

- [ ] 在 `_return()` 中全局保留所有非末尾 None-return；
- [ ] 仅根据 `linestart` 推断显式 return；
- [ ] 把 `POP_EXCEPT; JUMP_FORWARD` 识别成 return；
- [ ] 删除 handler 后的正常 continuation；
- [ ] 把 handler return 移到 try/except 之后；
- [ ] 对不完整协议生成猜测 AST。

阶段完成标准：

- [ ] bare-return handler body 为一个 `ast.Return`；
- [ ] named-return 正确清除绑定且 handler body 为 Return；
- [ ] real-pass 仍为 Pass；
- [ ] continuation 只在非异常或 pass 路径执行；
- [ ] 其他异常类型继续 re-raise；
- [ ] 非 None 返回值路径不回退。

## 11. 阶段 3：组合结构和命名 handler

必须覆盖：

- [ ] `except E: return`；
- [ ] `except E: return None`；
- [ ] `except E as error: return`；
- [ ] return 前有一条或多条语句；
- [ ] 多个 except 中只有一个 return；
- [ ] 多个 except 分别 return/pass/raise；
- [ ] nested try/except；
- [ ] try/except/else 后有 continuation；
- [ ] 外层 terminal if 包含 try/except；
- [ ] handler 位于循环中但 return 退出函数；
- [ ] handler 返回非 None；
- [ ] 真正 pass handler；
- [ ] terminal pass handler；
- [ ] handler 之后函数还有多条语句。

对命名 handler，必须验证编译器清理：

```text
POP_EXCEPT
LOAD_CONST None
STORE_FAST error
DELETE_FAST error
LOAD_CONST None
RETURN_VALUE
```

只 suppress 编译器生成的绑定清理，不得删除用户在 handler 中对其他变量的赋值或
删除。

阶段完成标准：

- [ ] `except ... as name` 的名称不泄漏；
- [ ] handler body 不输出 synthetic `name = None` 或 `del name`；
- [ ] Return 位于 handler 内，不位于 continuation 后；
- [ ] nested region 不重复消费 token；
- [ ] try/else/finally 现有结构无回退。

## 12. 阶段 4：负向和 fail-closed 测试

每个用例只破坏一个证明条件：

- [ ] 删除 handler `POP_EXCEPT`；
- [ ] 将 `LOAD_CONST None` 改为非 None；
- [ ] 删除 `RETURN_VALUE`；
- [ ] 给 return block 增加正常后继；
- [ ] 增加 foreign predecessor；
- [ ] 让 return 跳入正常 continuation；
- [ ] 删除 try 正常路径的 join；
- [ ] continuation 指向缺失 offset；
- [ ] 破坏 named-handler STORE/DELETE 名称一致性；
- [ ] 让 cleanup 跨越其他 handler clause；
- [ ] 增加跨 candidate 的 exception edge；
- [ ] 构造循环或超出 work limit 的 handler 图；
- [ ] 把真正 pass 的 JUMP_FORWARD 伪装成不完整 return；
- [ ] 构造未知 finally/with cleanup 交叉。

断言：

- [ ] 不把损坏形态恢复成 Return；
- [ ] 不把未解释 terminal transfer 静默恢复成 Pass；
- [ ] 抛出稳定的 `Python311ParseError` 或控制流子类；
- [ ] 错误包含 code name、真实 handler/return offset 和 version；
- [ ] 大图不泄漏 `RecursionError`，不无限循环。

## 13. 阶段 5：AST、语法和差分行为验证

### 13.1 AST 验证

- [ ] `ast.parse(recovered)` 通过；
- [ ] `compile(tree, ..., "exec")` 通过；
- [ ] bare-return handler body 含 `ast.Return`，不含替代 Pass；
- [ ] Return 位于正确 `ExceptHandler.body`；
- [ ] continuation 仍位于 `ast.Try` 之后；
- [ ] real-pass handler body 保持 Pass；
- [ ] named-handler 不输出 synthetic cleanup；
- [ ] 非 None return 的 AST 不变。

### 13.2 差分行为验证

本仓库新增 fixture 可以安全执行：

- [ ] 空 iterator 不记录 `after`；
- [ ] 非空 iterator 记录一次 `after`；
- [ ] `__next__` 调用次数一致；
- [ ] continuation 副作用次数一致；
- [ ] handler 前置语句执行顺序一致；
- [ ] 返回值一致；
- [ ] 非匹配异常类型和消息一致；
- [ ] named exception binding 生命周期一致；
- [ ] 多 handler 路由一致；
- [ ] nested handler 路由一致。

不能只比较最终返回值，因为本问题中原始和错误恢复通常都返回 `None`，差异发生在
append 等副作用。

## 14. 阶段 6：外部 Globals 静态验收

静态流程：

1. 重新计算 `Globals.original.marshal` SHA-256；
2. 使用现有 `py311tool` 只读转换自定义 opcode；
3. 生成临时 fixed pyc 和恢复源码；
4. 使用 Scanner311/dis 检查目标 wrapper；
5. 对恢复源码执行 `ast.parse()` 和 `compile()`；
6. 不 import、不 exec、不调用外部函数；
7. 静态检查 `yield_fun` 和 `yield_fun_new` 的 handler AST。

验收断言：

- [ ] `yield_fun_new.<locals>.wrapper` 的 StopIteration handler 含 Return；
- [ ] 该 handler 不含替代 Pass；
- [ ] 三元组 append 位于 try/except 之后；
- [ ] Return 位于 append 之前的异常路径中；
- [ ] `yield_fun.<locals>.wrapper` 的真正 pass handler 保持 Pass；
- [ ] 二元组 append 仍可由 StopIteration pass 路径到达；
- [ ] `yield_fun_new` 的其他 checker/weakref 结构不回退；
- [ ] 外部 marshal/pyc 未修改；
- [ ] 验证期间未执行目标代码。

Python 2.7 恢复源码只作为目标控制流参考；最终判断以 Python 3.11 code object 的
exception table、token 和 CFG 为准。

## 15. 阶段 7：覆盖矩阵、文档和完整门禁

建议新增 shape：

```text
except_handler_return
```

shape notes 必须说明：

- 普通 handler 中 None-return 是控制转移；
- paired `except_handler_pass` 行为由同一 fixture 证明；
- matcher 依赖 normal continuation 和完整 cleanup 协议；
- 未知协议 fail-closed。

完整验证顺序：

1. bare/named/pass handler 定向测试；
2. handler token/exception-table/CFG 测试；
3. `pytest/test_exceptiontable311.py`；
4. `pytest/test_controlflow311.py`；
5. `pytest/test_reliability311.py`；
6. opcode parser/behavior 定向测试；
7. 全量 pytest；
8. 604 文件真实语料回归；
9. Opcode 和 shape 覆盖报告；
10. release gate；
11. touched Python files flake8；
12. `git diff --check`；
13. 生成报告时效检查。

必须更新：

- [ ] `test/bytecode_3.11/shape_matrix.json`；
- [ ] shape 数量断言；
- [ ] release policy shape 数量；
- [ ] `PYTHON_311_SHAPE_COVERAGE.md`；
- [ ] `PYTHON_311_SUPPORT.md`；
- [ ] `PYTHON_311_RELEASE_GATE.md`；
- [ ] 必要的 realworld regression 归档和报告；
- [ ] 本文执行记录。

Opcode inventory 不应因本修复改变；`RETURN_VALUE`、`POP_EXCEPT` 和异常表已被
现有 scanner/normalizer 覆盖。

## 16. 最终验收标准

修复完成必须同时满足：

- [ ] 最小 bare handler return 恢复正确；
- [ ] explicit `return None` 保持提前退出语义；
- [ ] named handler return 恢复正确；
- [ ] 真正 pass handler 不被改成 Return；
- [ ] terminal pass 不产生错误早退结构；
- [ ] 空 iterator 不执行 continuation；
- [ ] 非空 iterator 正常执行 continuation；
- [ ] 非匹配异常继续传播；
- [ ] 非 None handler returns 不回退；
- [ ] nested/multiple handlers 不回退；
- [ ] 损坏 cleanup 继续 fail-closed；
- [ ] `yield_fun_new` 静态 AST 恢复 Return；
- [ ] `yield_fun` 静态 AST 保持 Pass；
- [ ] 外部文件只读且未执行；
- [ ] 全量测试不少于阶段 0 基线且无新增 skip；
- [ ] 604 文件真实语料不退化；
- [ ] coverage 和 release gate 通过；
- [ ] 工作区只包含本计划授权修改。

## 17. 风险和回退

### 17.1 主要风险

1. 把 terminal pass 的函数自然返回误判成 handler 早退；
2. 删除或重复输出命名 handler 的绑定清理；
3. 把正常 pass 的 join 错误切断；
4. 将 finally/with 的 duplicated cleanup 当成普通 return；
5. return 移出 handler，仍然在 continuation 后执行；
6. 影响非 None return、break 或 continue；
7. 依赖 line table 导致不同优化/编译模式下不稳定；
8. 放宽后接受损坏 exception table。

风险控制：

- 修复局限在普通 handler clause；
- 以 CFG continuation 和完整协议为主，行号只作辅助；
- 计划对象显式记录所有 owned offsets；
- matcher 失败时不猜测；
- 对“空 body + 未解释 terminal transfer”升级为 fail-closed；
- paired pass/return fixture 必须同时通过。

### 17.2 回退范围

实现应集中在：

```text
ExceptionStructureDecompiler311 handler clause recovery
```

如果组合回归失败，可以独立回退 handler-return matcher，不回退已提交的 terminal
if/else、except*、scanner、normalizer 或公共错误层修复。

## 18. 提交策略

建议独立提交：

```text
修复：保留 Python 3.11 except handler 控制关键 return
```

提交正文应包含：

- 根因是显式 return 行号位于 `POP_EXCEPT`；
- 通用 implicit-None 清理错误删除 `RETURN_VALUE`；
- pass 与 return 的严格协议差异；
- named-handler cleanup 和 fail-closed 边界；
- `yield_fun_new` 只读静态验收结果；
- 定向、全量、真实语料和 release gate 结果。

提交前：

- [ ] `git diff --check` 通过；
- [ ] staged diff 不含外部 marshal/pyc/恢复文件；
- [ ] 记录阶段 0 和最终全量测试；
- [ ] 记录最小行为差分；
- [ ] 记录外部静态验收；
- [ ] 确认无无关用户修改被暂存。

## 19. 执行记录模板

### 阶段 0

```text
完成日期：2026-08-05
实际基线提交：6b5cd50e3b5cf3dd43e47f40ed1ee61938809ff0
工作区状态：生产源码无修改；仅本计划文档未跟踪
全量测试基线：900 passed, 6 skipped in 30.57s
最小失败 AST：bare/named None-return handler 被恢复为 Pass，return 离开 handler
最小失败行为：空 iterator 错误进入 continuation；可触发 UnboundLocalError 或业务 append
外部 SHA-256：6aafa80c3cb1747df7586c060c0fb6442198a59d11390d7f1568c807e7ab3693
结论：scanner、exception table 和 CFG 证据稳定；错误位于 handler clause 的
      implicit-None 清理与异常协议 suppress 交界
```

### 阶段 1 到阶段 3

```text
完成日期：2026-08-05
修改文件：decompyle3/controlflow/exception_structures.py、
          test/fixtures311/except_handler_return.py、pytest/test_exceptiontable311.py
计划模型：_HandlerTerminalTransfer；记录 body/cleanup/return/continuation owned offsets
continuation 证明：正常 try 路径必须以唯一 JUMP_FORWARD 到 handler 后 join；handler
                   return region 必须唯一入口、无 foreign predecessor、无正常后继
bare-return 结果：恢复 ast.Return(value=None)，不执行 continuation
named-return 结果：保留真实 handler 语句，隐藏 None/STORE/DELETE 名称清理并恢复 Return
real-pass 结果：POP_EXCEPT; JUMP_FORWARD 继续恢复 Pass，并可到达 continuation
terminal-pass 结果：无独立 normal continuation 时不强制生成显式 Return
受控补充清理：for-return 只接受 loop 上下文证明的单个 POP_TOP；with-return 只接受
              已由外层结构标记 owned/suppressed 的退出协议
拒绝形态：缺少 normal join、协议不完整、return 有后继、foreign predecessor、
          return exception edge、命名清理不一致、越界 continuation、work limit
```

### 阶段 4 到阶段 5

```text
完成日期：2026-08-05
负向测试数量：12 类单点破坏；另含 8 类直接 plan 正向证明和 loop/with owned cleanup
稳定错误类型和 offset：canonical 物理 None-return 未获所有权时抛出
                       Python311ParseError；bare_return 错误 offset=58
AST/compile 结果：Return 保持在 StopIteration handler 内；恢复源码 ast.parse/compile 通过
行为差分结果：bare/explicit/named/multiple/nested/else/terminal-if/while/for/with、
              非 None return 和 paired pass 的返回值、副作用与调用次数一致
异常传播结果：非匹配 RuntimeError 类型和消息保持；StopIteration 只调用 __next__ 一次
定向测试：pytest/test_exceptiontable311.py 57 passed；控制流、可靠性、opcode parser/
          behavior 组合 362 passed
```

### 阶段 6

```text
完成日期：2026-08-05
外部目标：/Users/ice/Desktop/Custom/WorkCode_github/py3disassembly/dump/test/
          Globals.original.marshal
SHA-256：6aafa80c3cb1747df7586c060c0fb6442198a59d11390d7f1568c807e7ab3693
安全限制：只读静态处理，未 import/exec/call
yield_fun handler AST：StopIteration handler=[Pass()]
yield_fun_new handler AST：StopIteration handler=[Return(value=None)]，无替代 Pass
append 相对位置：yield_fun_new 三元组 append 位于 try/except 后；异常 Return 在其之前终止
结果：py311tool source_bytes=18842，Scanner/dis 协议符合预期，ast.parse/compile 通过；
      外部 marshal SHA-256 前后不变
```

### 阶段 7

```text
完成日期：2026-08-05
新增 shape：except_handler_return
全量测试：926 passed, 6 skipped in 32.83s；release gate 复跑 32.46s
真实语料：604/604 decompile/syntax success，0 fail-closed，0 unexpected crash，
          6/6 behavior consistent
Opcode/shape coverage：Opcode 110/110；Behavior 110/110；Shape 44 pass、
                      1 approved fail-closed、0 missing
release gate：静态文档/归档时效、完整 pytest 和 6 项 skip 白名单全部通过
flake8：所有 touched Python files 通过
git diff --check：通过
Git 提交：尚未提交；等待用户单独确认提交
```

## 20. 当前状态

```text
计划状态：阶段 0 到阶段 7 已执行完成
当前阶段：修复、回归源码、外部静态验收和发布门禁均完成；尚未 Git 提交
已确认根因：handler 显式 None-return 的源码行落在 POP_EXCEPT，通用 return
            清理在协议被 suppress 后将其误判为隐式返回
安全原则：只有 normal continuation、cleanup 协议和 CFG 所有权均可证明时才恢复
          handler Return；未知形态不得静默生成 Pass
优先级：高；当前错误会让异常路径执行本应跳过的业务副作用
```
