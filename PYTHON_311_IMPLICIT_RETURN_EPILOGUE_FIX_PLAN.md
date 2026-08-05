# Python 3.11 隐式 `None` 函数尾声合并修复计划

## 1. 目标

修复 CPython 3.11 在函数末尾编译普通 `if`、短路条件和嵌套条件时，为多个
物理控制流出口分别生成 `LOAD_CONST None; RETURN_VALUE`，而反编译器把这些
编译器尾声恢复成多个显式 `return` 或 `return None` 的问题。

目标案例：

```python
def SunShine_Update():
    if g37_plugin_obj and debugMode:
        g37_plugin_obj.update()
```

当前可能恢复为：

```python
def SunShine_Update():
    if g37_plugin_obj and debugMode:
        g37_plugin_obj.update()
        return
    return None
```

修复后应恢复为普通函数自然结束：

```python
def SunShine_Update():
    if g37_plugin_obj and debugMode:
        g37_plugin_obj.update()
```

本修复只合并可由 CFG 严格证明为同一个函数自然结束点的隐式 None-return
出口。不得在 AST 或源码输出阶段全局删除 `return`，不得改变异常 handler、
循环、`with`、`finally` 或其他控制关键 return 的语义。

## 2. 当前基线

### 2.1 仓库基线

计划固化时：

```text
仓库：/Users/ice/Desktop/Custom/WorkCode_github/python-decompile3
提交：5f9d7a9773e20c564d471e7683a03d8375673666
提交说明：修复：保留 Python 3.11 except handler 控制关键 return
工作区：干净
最近已记录全量测试：926 passed，6 skipped
最近已记录真实语料：604/604 decompile/syntax success
```

阶段 0 必须重新执行以下检查并记录实际值：

```bash
git status --short --branch
git log -1 --format='%H%n%s'
.venv311/bin/python -m pytest -q
```

如果提交、工作区状态或测试数量发生变化，必须在本文执行记录中写入实际
基线，不能静默沿用上述历史结果。

### 2.2 外部只读对照

Python 2.7 参考源码：

```text
/Users/ice/Desktop/Custom/WorkCode_github/py3disassembly/dump/test/Globals.py
```

自定义 Opcode marshal：

```text
/Users/ice/Desktop/Custom/WorkCode_github/py3disassembly/dump/test/
Globals.original.marshal
```

当前 Python 3.11 恢复源码：

```text
/Users/ice/Desktop/Custom/WorkCode_github/py3disassembly/dump/test/
Globals.original.decompyle3.py
```

外部文件只允许：

- 读取字节和计算摘要；
- 静态 Opcode 转换、扫描、反汇编和反编译；
- 使用 `marshal.loads()` 构造 code object 供静态分析；
- 对恢复文本执行 `ast.parse()` 或 `compile(..., "exec")`；
- 比较 AST、token、异常表和 CFG。

禁止：

- import 目标模块；
- `exec`、`eval` 或调用外部 code object；
- 执行恢复源码；
- 把外部业务源码、marshal 或 pyc 提交到本仓库；
- 为单个外部样本放宽通用 fail-closed 条件。

### 2.3 已确认的目标差异

| 函数 | Python 3.11 物理 None-return | 参考源码显式 return | 当前问题 |
|---|---:|---:|---|
| `SunShine_AddEntity` | 3 | 0 | if 内和函数尾可能生成冗余 return |
| `SunShine_DelEntity` | 4 | 0 | 外层及嵌套 if 出口可能生成冗余 return |
| `SunShine_Update` | 3 | 0 | if 内和函数尾可能生成冗余 return |

这些冗余 return 对三个目标函数当前不改变返回值；`return`、`return None` 和
自然到达函数末尾都返回 `None`。本问题主要影响源码结构准确性和可读性，但错误
的通用清理方案会破坏真实早退，因此仍需在控制流层修复。

## 3. 已确认的字节码形态

### 3.1 `SunShine_AddEntity`

外层条件为 `g37_plugin_obj and debugMode`。标准化后的关键出口为：

```text
180 LOAD_CONST None
182 RETURN_VALUE

184 LOAD_CONST None
186 RETURN_VALUE

188 LOAD_CONST None
190 RETURN_VALUE
```

三个出口分别对应：

1. 条件为真、suite 执行后的函数结束；
2. 第一个短路条件为假的函数结束；
3. 第二个短路条件为假的函数结束。

### 3.2 `SunShine_DelEntity`

外层短路条件和内层 `if plugin_key` 共同产生四个物理 None-return：

```text
132/134
136/138
140/142
144/146
```

四个出口都没有正常 continuation，并且都返回 `None`。

### 3.3 `SunShine_Update`

关键出口为：

```text
80/82
84/86
88/90
```

三个 code object 的 exception table 都为空。目标出口不是异常 cleanup、循环
跳转或带值 return，而是同一个源码级函数 fallthrough 的物理复制。

## 4. 根因

### 4.1 CPython 3.11 复制函数尾声

对位于函数末尾的：

```python
if left and right:
    action()
```

CPython 3.11 不一定让 true、left-false 和 right-false 路径汇合到同一个
`RETURN_VALUE`。编译器可以为每条路径复制完整的 None-return 尾声，因此一个
源码级自然结束点会变成三个物理 basic block。

### 4.2 条件 endpoint 合并丢失物理所有权

当前 `StructuredDecompiler311` 中：

- `_condition_endpoint_signature()` 为条件出口生成表达式签名；
- `_coalesce_condition_endpoints()` 把签名相同的出口映射到一个 canonical
  endpoint；
- `_ConditionPlan` 最终只保留两个逻辑 endpoint；
- 被合并的其他物理 return block 没有作为“同一尾声的别名集合”保留在计划中。

因此条件表达式可以恢复成 `left and right`，但后续 terminal-if 结构器不知道
还有哪些物理 return block 已经属于这个条件。

### 4.3 terminal-if 只识别两个连续区间

当前 `_terminal_if_plan()` 把两个逻辑 endpoint 之间以及第二个 endpoint 到
`region_end` 看作两个 terminal interval。这适合真正的 terminal
`if/else`，但不能完整表达“一条有实际 suite 的分支，加多个等价隐式尾声块”。

`_is_implicit_none_return_only()` 只接受一个精确的：

```text
LOAD_CONST None
RETURN_VALUE
```

当区间里连续存在两到三个物理 None-return block 时，该判定返回 False。

### 4.4 return 保留逻辑按安全方向工作，但缺少尾声计划

`_emit_terminal_if()` 和 `_preserve_terminal_none_return()` 为避免删除控制关键
return，会把尚未被结构计划消费的 None-return 恢复成 `ast.Return`。这是安全的
fallback，但在当前目标 shape 上产生冗余 return。

因此问题不在源码打印器，也不能通过文本美化解决；缺少的是一个能够证明并拥有
全部物理尾声块的 CFG 计划。

## 5. 修复范围与非目标

### 5.1 初始支持范围

第一版只支持同时满足以下条件的 CPython 3.11 函数：

- 普通同步函数；
- 当前 parse region 到达 code object 末尾；
- 普通 `if`、短路 `and/or` 或它们的有限嵌套；
- 所有待合并出口严格为 `LOAD_CONST None; RETURN_VALUE`；
- 候选区域没有 exception edge、循环 back edge 或外部入口；
- 所有路径都没有正常 continuation。

### 5.2 初始拒绝范围

第一版主动拒绝：

- module 和 class body；
- generator、coroutine 和 async generator；
- `try/except`、`except*`、`finally`、`with` 和 `async with` 覆盖的区域；
- 循环体、`break`、`continue` 和反向跳转；
- `RETURN_VALUE` 返回非 None 的出口；
- `RAISE_VARARGS`、`RERAISE`、`YIELD_VALUE` 等不同 terminator；
- 具有未知 predecessor、交叉入口、越界目标或损坏栈协议的 CFG。

这些形态只有在独立计划和回归证明完成后才能逐项开放。

### 5.3 非目标

本计划不保证区分以下两个在函数末尾语义相同、部分字节码形态也可能无法唯一
区分的源码：

```python
def f():
    action()
```

```python
def f():
    action()
    return None
```

优先级为：

1. 保持控制流和返回语义；
2. 保持副作用和异常顺序；
3. 保持 fail-closed；
4. 在可证明范围内减少编译器生成的冗余 return；
5. 最后才是原始文本形式。

## 6. 安全边界

禁止使用以下方案：

```python
# 禁止：全局删除末尾 Return
if isinstance(statement, ast.Return) and statement.value is None:
    remove(statement)
```

也禁止：

- 在源码输出器中根据缩进或相邻文本删除 return；
- 仅根据 `linestart is None` 判断隐式 return；
- 将所有返回 None 的 block 视为等价；
- 跨异常表、cleanup、循环或 region 边界合并出口；
- matcher 失败后继续消费未证明归属的 token。

隐式尾声 matcher 至少必须证明：

1. 当前作用域是允许的函数作用域；
2. 当前结构位于函数的 terminal parse region；
3. 每个候选出口的终止语义后缀严格为 `LOAD_CONST None; RETURN_VALUE`；
   第一个出口 block 可以在该后缀前包含已归属 suite 的语句，其余复制尾声
   block 必须只有该返回协议；
4. 每个候选 block 没有正常 outgoing edge；
5. 每个候选 block 没有 exception incoming/outgoing edge；
6. 每个候选 block 的正常 predecessor 只来自候选条件图、候选 suite 或同一
   尾声计划拥有的 block；
7. 不存在从候选区域之外跳入尾声 block 的 predecessor；
8. 所有相关跳转目标都能映射到真实 normalized offset；
9. 所有被跳过或消费的 token 都由计划显式拥有；
10. 有界遍历在 work limit 内完成；
11. 任一条件不能证明时不生成尾声计划，保持现有安全输出或抛出稳定的
    `UnsupportedPython311ControlFlow`。

line table 和 `co_positions()` 只可作为辅助证据，不能单独授权删除 return。
异常 handler 的显式 return 行号可能落在 cleanup opcode 上，之前的 handler
return 修复不得回退。

## 7. 目标判定表

| 字节码/CFG 形态 | 处理 |
|---|---|
| 函数尾单一隐式 None-return | 保持现有自然结束清理 |
| 末尾 `if flag: action()` 的所有路径均到等价 None-return | 合并为普通无 else 的 if |
| 末尾 `if left and right: action()` 产生多个 None-return | 合并所有已证明尾声块 |
| 嵌套末尾 if 的所有叶子均为等价 None-return | 递归恢复嵌套 if，不输出叶子 return |
| `if flag: return; continuation()` | 保留控制关键 return |
| `except E: return; continuation()` | 保留 handler return |
| 任一路径返回非 None | 不进入隐式 None 尾声合并 |
| 任一路径抛出异常、yield 或跳出循环 | 不进入本 matcher |
| terminal `if/else` 两边都有实际 suite | 保持现有 terminal-if/else 计划 |
| 外部入口、异常边、反向边或损坏目标 | fail-closed |

## 8. 计划模型

建议增加独立私有计划，而不是继续给通用 `_return()` 增加启发式。示例：

```python
@dataclass(frozen=True)
class _ImplicitReturnEpiloguePlan:
    test: ast.expr
    body_start: int
    region_end: int
    condition_blocks: FrozenSet[int]
    exit_blocks: FrozenSet[int]
    owned_offsets: FrozenSet[int]
```

也可以把这些字段加入 `_TerminalIfPlan`，但必须明确区分：

- 真正的 source-level terminal `if/else`；
- 普通无 else 的 terminal `if`；
- 被多个物理 block 复制的函数 fallthrough。

计划对象必须携带全部 owned block/offset，不能只返回一个布尔值。emit 阶段只能
消费计划拥有的 offsets。

## 9. 预计修改范围

| 文件 | 预计修改 |
|---|---|
| `decompyle3/controlflow/structures.py` | 建立 endpoint alias/尾声计划、严格 CFG 验证和 terminal-if 接入 |
| `test/fixtures311/terminal_if_else.py` | 增加无 else 的短路与嵌套 terminal-if fixture |
| `pytest/test_controlflow311.py` | 增加 token、CFG、计划、AST 和负向测试 |
| `pytest/test_reliability311.py` | 增加事件序列和条件求值次数差分测试 |
| `pytest/behavior_cases311.py` | 增加独立行为探针 |
| `test/bytecode_3.11/shape_matrix.json` | 增加 `implicit_none_epilogue` shape |
| `PYTHON_311_SUPPORT.md` | 记录支持范围和主动拒绝边界 |
| coverage/release/realworld 报告 | 门禁通过后更新 |
| 本计划 | 填写各阶段执行记录 |

原则上不需要修改 Scanner311、Normalizer311、legacy Spark grammar、Python 2.7
路径或源码输出器。如果实现需要修改 `decompyle3/parsers/p311/base.py`，必须是
默认关闭、由结构计划显式授权的上下文，禁止全局放宽 return 清理。

## 10. 阶段 0：冻结失败基线

任务：

- [x] 检查 git status 和当前提交；
- [x] 运行全量测试并记录实际基线；
- [x] 新增最小单条件 terminal-if fixture；
- [x] 新增无 else 的 `and`、`or` 和嵌套条件 fixture；
- [x] 固定 normalized token、line position、CFG block/edge；
- [x] 固定修复前 AST 中冗余 `Return` 的数量和位置；
- [x] 固定原始/恢复源码的事件序列与返回值一致；
- [x] 固定显式早退和 except-handler-return 对照；
- [x] 记录外部 marshal 的 SHA-256，不提交外部文件。

最小 fixture：

```python
def terminal_and(left, right, events):
    if left and right:
        events.append("both")


def terminal_nested(left, right, key, events):
    if left and right:
        if key:
            events.append("hit")


def real_early_return(flag, events):
    if flag:
        return
    events.append("after")
```

阶段完成标准：

- [x] 三类冗余 return 可稳定复现；
- [x] 根因稳定定位到 endpoint/terminal-if structuring；
- [x] 真实早退对照当前保持正确；
- [x] 尚未修改生产实现。

## 11. 阶段 1：保留 endpoint 等价类和物理所有权

目标：条件表达式合并相同 endpoint 时，不再丢失被合并的物理 CFG block。

建议步骤：

1. 扩展 `_ConditionPlan`，记录 canonical endpoint 到所有 physical endpoint 的
   alias 集合；或者由 node 的原始 jump outcome 构建等价信息；
2. `_coalesce_condition_endpoints()` 继续为布尔表达式选择 canonical endpoint，
   但同时返回稳定、不可变的 alias ownership；
3. alias 只能来自同一次有界 condition graph 收集；
4. alias 相同签名仍不足以授权合并，最终授权必须由阶段 2 的 CFG 证明完成；
5. 保持现有 `and/or`、chained comparison、if-expression 和 terminal-if/else
   输出不变。

实际实现选择由 condition node 的真实 jump offset 和最终 CFG 可达出口重新建立
物理所有权，不修改 `_ConditionPlan` 的公共形态。这样 canonical endpoint 仍只
服务布尔表达式，是否消费物理尾声由独立计划二次证明。

实现约束：

- [x] 不通过重新扫描任意相同返回表达式吸收无关 block；
- [x] alias 中每个 offset 必须是真实 instruction/block 起点；
- [x] alias 集合顺序和 canonical 选择稳定；
- [x] endpoint 扩展、reduction 和 multiline condition 路径都保留 ownership；
- [x] 所有图遍历有 work limit 和 cycle detection。

阶段完成标准：

- [x] `terminal_and` 的多个 None-return endpoint 形成一个可审计等价类；
- [x] `SunShine_*` 的物理出口数量可由计划完整表示；
- [x] helper 尚未改变最终 AST；
- [x] 现有条件表达式测试不回退。

## 12. 阶段 2：建立严格的隐式尾声计划

增加 `_implicit_return_epilogue_plan()` 或等价 helper，对阶段 1 的 endpoint
等价类进行 CFG 证明。

推荐验证顺序：

1. 验证函数类型、loop context 和 terminal region；
2. 从 condition blocks 和 suite terminal blocks 有界遍历所有候选出口；
3. 验证每个出口都以 `LOAD_CONST None; RETURN_VALUE` 结束，并验证从最早
   None-return 到函数末尾只包含连续的返回协议；
4. 验证没有正常 successor 和 exception edge；
5. 验证所有 predecessor 的所有权；
6. 验证候选 blocks 两两不重叠且覆盖待消费 token；
7. 验证候选区间中没有其他语义指令；
8. 返回包含 owned blocks/offsets 的不可变计划。

不要复用 `_is_implicit_none_return_only()` 的单一区间布尔结果作为最终授权。
可新增：

```python
_implicit_none_return_block(block_index)
_implicit_epilogue_exit_blocks(...)
_implicit_return_epilogue_plan(...)
```

阶段完成标准：

- [x] 两个、三个、四个和五个物理尾声均能生成唯一计划；
- [x] 真正 terminal if/else 不被误分类；
- [x] 非 None return、异常边、循环边和外部 predecessor 返回无计划；
- [x] 损坏 CFG 不泄漏 `KeyError`、`RecursionError` 或无限循环。

## 13. 阶段 3：接入 terminal-if 发射

接入顺序建议为：

1. 现有 canonical join 型 if/else；
2. 现有真正 terminal if/else；
3. 严格 implicit-return epilogue plan；
4. 当前保留 return 的普通 fallback；
5. 无法安全表达时 fail-closed。

计划命中后：

- 捕获实际 suite，但不把 owned epilogue token 放进 suite；
- 生成 `ast.If(..., orelse=[])`；
- 消费全部计划拥有的复制尾声；
- 返回整个计划的 `region_end`，防止外层线性重复解析；
- 将 owned offsets 传给 `_preserve_terminal_none_return()`，禁止它重新补回
  `ast.Return`；
- 成功、失败和异常路径都恢复 body、stack、pending assignment 和 active-region
  状态。

阶段完成标准：

- [x] `terminal_and` AST 中没有冗余 `ast.Return`；
- [x] 条件只求值一次；
- [x] False 路径不执行 suite；
- [x] 返回值仍为 None；
- [x] `real_early_return` 的 `ast.Return` 仍存在；
- [x] except-handler return 回归仍通过。

## 14. 阶段 4：嵌套、短路和布局扩展

在核心单层 shape 稳定后覆盖：

- [x] `left and right`；
- [x] `left or right`；
- [x] `not flag`；
- [x] 多项 `a and b and c`；
- [x] 混合 `(a and b) or c`；
- [x] 内层普通 `if key`；
- [x] 两层短路条件嵌套；
- [x] suite 包含一条和多条语句；
- [x] 条件具有调用副作用；
- [x] 条件物理极性反转；
- [x] 函数末尾条件之前存在普通语句。

每增加一种 shape 都必须同时有 AST 断言和行为差分。不能通过把任意连续
None-return 序列统一消费来扩大支持。

阶段完成标准：

- [x] `SunShine_AddEntity` 对应最小 shape 无 Return；
- [x] `SunShine_DelEntity` 对应嵌套 shape 无 Return；
- [x] `SunShine_Update` 对应单调用 shape 无 Return；
- [x] 现有 terminal if/else/elif 测试不回退。

## 15. 阶段 5：负向和 fail-closed 测试

使用现有 code replacement/CFG 测试辅助方法，每个用例只破坏一个条件：

- [x] 把一个 `LOAD_CONST None` 替换成非 None 常量；
- [x] 删除一个 `RETURN_VALUE`；
- [x] 为尾声 block 增加正常 successor；
- [x] 从区域外增加 predecessor；
- [x] 让条件目标指向 block 中部；
- [x] 让两个候选尾声区间重叠；
- [x] 增加反向跳转；
- [x] 增加 exception incoming/outgoing edge；
- [x] 让候选跨越 exception region；
- [x] 在 return block 中插入额外语义指令；
- [x] 构造超过 work limit 的大图；
- [x] 构造 generator/coroutine 标志；
- [x] 构造 module/class body。

必须保留的语义对照：

```python
def early_return(flag, events):
    if flag:
        return
    events.append("after")


def explicit_value(flag):
    if flag:
        return 1


def handler(iterator, events):
    try:
        next(iterator)
    except StopIteration:
        return
    events.append("scheduled")
```

断言：

- 不生成未经证明的尾声计划；
- 不静默删除控制关键 return；
- 可安全 fallback 时保留现有 AST；
- 协议损坏时抛出包含 code name 和真实 offset 的稳定 Parser311 错误；
- 不出现 unexpected crash 或无限遍历。

## 16. 阶段 6：行为、语法和外部静态验收

### 16.1 仓库 fixture

对仓库内可安全执行的最小 fixture：

- [x] `ast.parse(recovered)` 通过；
- [x] `compile(recovered, ..., "exec")` 通过；
- [x] 目标函数 AST 不包含冗余 Return；
- [x] True/False 路径事件序列一致；
- [x] 条件调用次数一致；
- [x] 返回值和返回类型一致；
- [x] 条件或 suite 抛出的异常类型和顺序一致。

### 16.2 外部 `Globals` 静态验收

外部文件不得执行。只进行：

- 静态转换、扫描和反编译；
- `ast.parse()` 和 `compile(..., "exec")`；
- 提取三个目标函数 AST；
- 断言三个目标函数不存在冗余 `ast.Return`；
- 对比参考源码的条件和 suite 结构；
- 复查 `yield_fun_new` 的 except-handler `Return` 仍然存在；
- 复查输入文件 SHA-256 前后不变。

阶段完成标准：

- [x] 三个 `SunShine_*` 函数恢复成自然 fallthrough；
- [x] 外部目标静态语法通过；
- [x] 未 import、exec、eval 或调用外部代码；
- [x] 外部文件没有被修改或复制进仓库。

## 17. 阶段 7：完整回归和发布门禁

推荐验证顺序：

1. 新增 implicit epilogue 定向测试；
2. `pytest/test_controlflow311.py`；
3. `pytest/test_exceptiontable311.py`；
4. `pytest/test_reliability311.py`；
5. opcode parser/behavior 定向测试；
6. 全量 pytest；
7. 604 文件真实语料回归；
8. Opcode 和 shape coverage；
9. release gate；
10. touched Python files flake8；
11. `git diff --check`；
12. 文档和生成报告时效检查。

必须更新：

- [x] `test/bytecode_3.11/shape_matrix.json`；
- [x] shape 数量断言和 release policy；
- [x] `PYTHON_311_SHAPE_COVERAGE.md`；
- [x] `PYTHON_311_SUPPORT.md`；
- [x] `PYTHON_311_RELEASE_GATE.md`；
- [x] 必要的 realworld regression 报告；
- [x] 本文执行记录。

Opcode inventory 预期不变化；`LOAD_CONST` 和 `RETURN_VALUE` 已被现有扫描器覆盖。

## 18. 最终验收标准

- [x] 三个最小 `SunShine_*` 对应 shape 不再输出冗余 return；
- [x] 外部三个目标函数静态 AST 不再包含冗余 Return；
- [x] 普通单条件、`and`、`or` 和嵌套条件均通过；
- [x] 条件求值次数、副作用顺序和返回值一致；
- [x] 显式早退后有 continuation 时 Return 保留；
- [x] 普通 except handler 的控制关键 Return 保留；
- [x] terminal if/else/elif 不回退；
- [x] 非 None return 不被合并；
- [x] exception、loop、with、finally 和 generator 边界不被放宽；
- [x] 损坏 CFG 继续 fail-closed；
- [x] 全量测试不少于阶段 0 基线且无新增 skip；
- [x] 604 文件真实语料不退化；
- [x] coverage 和 release gate 通过；
- [x] 工作区只包含计划授权的修改。

## 19. 风险和回退

### 19.1 主要风险

1. 把真实早退误当作自然函数结束；
2. 丢失 endpoint alias 后漏消费或重复消费 return block；
3. 把真正 terminal if/else 的两个实际分支误分类为无 else；
4. 破坏异常 handler、finally 或 with cleanup 的控制转移；
5. 仅凭 line table 在不同编译参数下产生不稳定结果；
6. 跨 region 消费无关 token，造成 AST 丢语句；
7. 嵌套条件导致结构递归或 work limit 回退；
8. 为通过外部样本过度放宽等价条件。

风险控制：

- 修复位于 CFG/结构计划层；
- endpoint equivalence 和 epilogue ownership 分离；
- 所有被消费 offsets 显式记录；
- 初始排除异常、循环、with/finally 和生成器协议；
- line position 只作辅助；
- paired early-return/handler-return 测试必须先于外部验收通过；
- matcher 不确定时保留冗余 return，优先保证语义。

### 19.2 回退范围

如果出现回归，应只回退：

1. endpoint alias ownership 字段及构造逻辑；
2. `_ImplicitReturnEpiloguePlan` 和 matcher；
3. terminal-if 的计划接入；
4. 对应 fixture、测试和 shape 文档。

不得通过恢复“全局删除 None-return”作为替代方案。Scanner、Normalizer、异常表
解码器、Python 2.7 路径和源码输出器原则上不在回退范围内。

## 20. 提交策略

建议拆分为三个可审查提交：

1. `测试：固化 Python 3.11 隐式 None 尾声失败基线`；
2. `修复：合并 Python 3.11 可证明的隐式 None 函数尾声`；
3. `文档：更新 Python 3.11 尾声 shape 和发布门禁`。

每个提交前必须执行对应定向测试和 `git diff --check`。生产实现提交不得混入
外部样本、临时 pyc、缓存文件或无关格式化。

## 21. 执行记录

### 计划固化

```text
日期：2026-08-05
基线提交：5f9d7a9773e20c564d471e7683a03d8375673666
工作区：固化前干净
已完成：根因、外部只读边界、阶段 0 到阶段 7、验收和回退方案
未执行：生产代码、fixture、测试、外部重新反编译和发布门禁修改
```

### 阶段 0

```text
状态：完成
实际提交：5f9d7a9773e20c564d471e7683a03d8375673666
git status：master ahead 2；仅计划文档为未跟踪文件
全量测试基线：926 passed，6 skipped in 32.93s
失败基线：terminal_and_no_else 和 terminal_nested_no_else 稳定生成冗余 Return
外部摘要：6aafa80c3cb1747df7586c060c0fb6442198a59d11390d7f1568c807e7ab3693
```

### 阶段 1 和阶段 2

```text
状态：完成
实现：新增 _ImplicitReturnEpiloguePlan；从 condition node 的真实 jump block
      有界遍历 CFG，证明全部物理 None-return 出口和 owned offsets
限制：仅同步函数 terminal region；拒绝异常边、反向边、循环、module/class、
      generator/coroutine/async-generator、非 None return 和外部 predecessor
条件图：仅在汇合谓词的全部 predecessor 都属于当前 condition graph 时允许
        多前驱扩展，支持 (a and b) or c；独立后续 if 保持独立
```

### 阶段 3 和阶段 4

```text
状态：完成
实现：terminal-if 在现有 join/terminal-if 计划失败后尝试严格 epilogue 计划；
      捕获 suite 时只 suppress 计划拥有的 offsets，并阻止 preserve helper 补回
覆盖：单条件、and、or、not、多项 and、混合 and/or、嵌套 if、两层短路、
      多语句 suite、条件副作用、极性反转、前置普通语句、连续独立 if
定向 control-flow：61 passed
```

### 阶段 5

```text
状态：完成
负向用例：20 项 epilogue ownership/协议破坏和 1 项 condition-extension
          exception predecessor 全部拒绝扩展或生成计划
覆盖：越界/缺失/块中部 endpoint、缺失 condition block、循环、class/module、
      三种 suspension flags、非 None/额外语义/缺失 return、重叠 block、
      正常后继、反向边、异常边、外部 predecessor、不可达出口、work limit
关键对照：early return、非 None return、terminal if/else、普通 except handler
```

### 阶段 6

```text
状态：完成
外部临时目录：/private/tmp/python-decompile3-implicit-epilogue.laWvRW
恢复源码：18725 bytes；ast.parse/compile 通过
SunShine_AddEntity：Return=0，top-level If
SunShine_DelEntity：Return=0，top-level If；内层 If 保留
SunShine_Update：Return=0，top-level If
yield_fun_new：except StopIteration handler Return 保留
外部 marshal SHA-256 前后不变；未 import、exec、eval 或调用外部代码
```

### 阶段 7

```text
状态：完成
高风险定向：179 passed
全量 release-gate pytest：964 passed，6 skipped in 28.71s
真实语料：604/604 decompile/syntax success；0 fail-closed；0 unexpected crash；
          6/6 behavior consistent
覆盖：Opcode 110/110；Behavior 110/110；Shape 45 pass、
      1 approved fail-closed、0 missing
release gate、报告时效、flake8、git diff --check：通过
```

## 22. 当前状态

```text
计划状态：阶段 0 到阶段 7 已执行完成
当前阶段：等待最终全量复跑、工作区审查和用户确认提交
核心修复点：由条件入口的 CFG 完整证明物理出口，建立严格的隐式 None 尾声所有权
安全原则：无法证明为同一个函数自然 fallthrough 时，保留 return 或 fail-closed
优先级：中；目标样本当前语义等价，但错误的通用美化会造成高风险控制流回退
```
