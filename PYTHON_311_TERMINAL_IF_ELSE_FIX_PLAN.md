# Python 3.11 终止型 `if/else/elif` 修复计划

## 1. 目标

修复 CPython 3.11 在函数末尾编译 `if/else`、`if/elif/else` 时，各分支直接以
`RETURN_VALUE` 终止、因而不生成 `JUMP_FORWARD` 汇合跳转的控制流形态。

当前反编译器会把这种互斥分支错误恢复成连续语句，例如把：

```python
def terminal_if_else(flag):
    if flag:
        left()
    else:
        right()
```

错误恢复为：

```python
def terminal_if_else(flag):
    if flag:
        left()
    right()
```

目标恢复结果必须满足：

- True/False 分支保持互斥；
- `if/else` 恢复为带 `orelse` 的 `ast.If`，或者在原始结构无法唯一确定时恢复为
  语义等价、保留控制转移的早退形式；
- 连续的终止型 False 分支可以恢复为嵌套 `ast.If`，并由源码输出器生成
  `elif`；
- 控制关键的 `RETURN_VALUE` 不能因“隐式 `return None` 清理”而被静默删除；
- 无法从 CFG 严格证明分支归属时继续 fail-closed，不能把任意缺少
  `JUMP_FORWARD` 的条件都当成 `else`。

本计划只处理 Parser311 使用的 CPython 3.11 canonical control-flow shape。未知、
损坏、人工编辑、具有交叉入口或无法证明边界的字节码不进行猜测恢复。

## 2. 当前基线

### 2.1 仓库基线

开始执行本计划时必须重新确认：

```text
仓库：/Users/ice/Desktop/Custom/WorkCode_github/python-decompile3
基线提交：50fef6560de72b87e52ceadc4c426ef21331c4f5
提交说明：修复：支持无正常后继的 except* cleanup
已知全量测试基线：880 passed，6 skipped
```

执行阶段 0 前必须记录：

```bash
git status --short --branch
git log -1 --format='%H%n%s'
```

如果当前提交、工作区状态或全量测试基线发生变化，应在本文“执行记录”中记录
实际值，不能静默沿用旧基线。

### 2.2 外部对照文件

预期结构参考源码：

```text
/Users/ice/Desktop/Custom/WorkCode_github/py3disassembly/dump/test/Globals.py
```

自定义 Opcode 原始 marshal：

```text
/Users/ice/Desktop/Custom/WorkCode_github/py3disassembly/dump/test/
Globals.original.marshal
```

当前反编译结果：

```text
/Users/ice/Desktop/Custom/WorkCode_github/py3disassembly/dump/test/
Globals.original.decompyle3.py
```

外部文件只允许：

- 读取字节；
- 使用 `py311tool` 静态转换自定义 Opcode；
- 使用 `marshal.loads()` 构造 code object 供扫描、反汇编和反编译；
- 对恢复文本执行 `ast.parse()` 或 `compile()`；
- 比较 AST 和控制流结构。

禁止：

- `import` 目标模块；
- `exec`、`eval` 或调用目标 code object；
- 执行恢复源码；
- 把外部 marshal、修复后的 pyc 或业务源码复制进本仓库；
- 为了让样本通过而放宽通用 fail-closed 边界。

### 2.3 已确认的目标差异

参考源码中的目标方法：

```python
def registerOnAttrChanged(self, attrName, callback):
    if attrName not in self.cbPropChanged:
        self.cbPropChanged[attrName] = set([callback])
    else:
        self.cbPropChanged[attrName].add(callback)
```

当前恢复结果：

```python
def registerOnAttrChanged(self, attrName, callback):
    if attrName not in self.cbPropChanged:
        self.cbPropChanged[attrName] = set([callback])
    self.cbPropChanged[attrName].add(callback)
```

这不是单纯的排版差异。恢复结果允许 `.add(callback)` 在 True 分支执行之后继续
执行，破坏了原本的分支互斥关系。虽然本例使用 `set`，重复添加通常不改变集合
内容，但仍可能重复触发对象的 `__hash__`、`__eq__` 或其他副作用。

同一文件中已确认：

| 函数 | 参考源码 | 当前恢复结果 |
| --- | ---: | ---: |
| `registerOnAttrChanged` | 1 个带 `else` 的 `if` | 1 个无 `else` 的 `if` |
| `returnToLastMainScene` | 7 个带 orelse，含 5 个 `elif` | 7 个连续、无 orelse 的 `if` |
| `returnToLastMainScene2` | 5 个带 orelse，含 4 个 `elif` | 5 个连续、无 orelse 的 `if` |

### 2.4 已确认的标准化字节码

`Globals.original.marshal` 使用自定义 Opcode。通过 `py311tool` 静态解码成标准
CPython 3.11 Opcode 后，`registerOnAttrChanged` 的关键指令为：

```text
18  POP_JUMP_FORWARD_IF_FALSE  -> 74

20  ... self.cbPropChanged[attrName] = set([callback])
70  LOAD_CONST None
72  RETURN_VALUE

74  ... self.cbPropChanged[attrName].add(callback)
138 LOAD_CONST None
140 RETURN_VALUE
```

CFG 为：

```text
B0 condition
├── true  -> B1 [20, 74)  -> RETURN_VALUE
└── false -> B2 [74, 142) -> RETURN_VALUE
```

关键事实：

- B1 与 B2 都是正常出口块；
- B1 没有到 B2 的 fallthrough 边；
- 两个分支直接退出函数；
- 不存在从 B1 跳过 B2 的 `JUMP_FORWARD`；
- True 分支末尾的 `RETURN_VALUE` 是维持互斥控制流所必需的边界。

## 3. 根因

### 3.1 CPython 3.11 的终止型分支布局

普通、具有正常汇合点的 `if/else` 通常可以通过 True 分支末尾的
`JUMP_FORWARD` 找到 `else` 边界和 join offset。

当整个条件语句位于函数末尾时，CPython 3.11 可以把每个分支直接编译成：

```text
分支主体
LOAD_CONST None
RETURN_VALUE
```

这时没有正常 join，也没有 `JUMP_FORWARD`。`if/elif/else` 会表现为一串条件
块，每个命中的主体独立 `RETURN_VALUE`，最终 else 也独立返回。

### 3.2 当前 `_if_statement()` 只识别正常汇合跳转

当前实现位于：

```text
decompyle3/controlflow/structures.py
StructuredDecompiler311._if_statement()
```

现有路径使用 `_last_forward_jump()` 搜索 True/False endpoint 之间跳过 else 的
`JUMP_FORWARD`。找不到时，会恢复为无 else 的 `ast.If`，并把 False endpoint
返回给外层 `_parse_region()` 作为普通顺序代码继续解析。

该逻辑隐含了一个错误假设：

```text
没有 JUMP_FORWARD == 没有 else
```

对终止型分支，正确含义可能是：

```text
没有 JUMP_FORWARD == 两个分支各自终止，不需要正常汇合
```

### 3.3 隐式 `return None` 清理删除了控制边

当前实现位于：

```text
decompyle3/parsers/p311/base.py
_StraightLineDecompiler._return()
```

当 `RETURN_VALUE` 返回 `None`，且前一条 `LOAD_CONST None` 没有独立源码行号时，
当前逻辑可能把它当作可省略的隐式函数返回。

在 code object 真正末尾省略一次隐式返回是安全的；在条件分支内部省略却可能
删除该分支的唯一控制终止边。两处逻辑叠加后形成当前错误：

1. `_if_statement()` 没有建立 orelse；
2. True 分支的 `RETURN_VALUE` 又被省略；
3. False 分支被输出为 True 分支之后的普通顺序代码。

### 3.4 `elif` 丢失是同一问题的递归表现

Python AST 中，`elif` 本质上是：

```python
ast.If(..., orelse=[ast.If(...)])
```

当第一个条件没有建立 `orelse` 时，后续条件只能成为函数体中的兄弟
`ast.If`，源码输出阶段已经无法安全判断这些兄弟节点原来是否属于同一条
`elif` 链。因此修复点在 CFG 结构恢复层，而不是源码格式化层。

## 4. 修复范围和非目标

### 4.1 需要修改的层

核心修改预计位于：

- `decompyle3/controlflow/structures.py`；
- 必要时修改 `decompyle3/parsers/p311/base.py`，为控制关键的隐式 return 增加
  明确的保留上下文；
- Python 3.11 控制流、语法、可靠性和 fail-closed 测试；
- shape matrix、支持文档和生成报告。

### 4.2 不需要修改的层

本问题不需要修改：

- 自定义 Opcode 映射；
- Scanner311 的物理指令扫描；
- Normalizer311；
- exception table decoder；
- legacy Spark grammar；
- Python 3.7/3.8 解析器；
- Python 2.7 反编译逻辑；
- 源码输出器的 `elif` 格式化规则。

### 4.3 非目标

本计划不保证恢复原始源码的精确排版。以下两段源码可能产生行为等价或非常
接近的字节码：

```python
if condition:
    return
continuation()
```

```python
if condition:
    return
else:
    continuation()
```

无法唯一确定原始写法时，优先级为：

1. 保持行为；
2. 保持控制流结构；
3. 保持 fail-closed；
4. 最后才是源码形式接近原文。

## 5. 安全边界

禁止采用以下宽松方案：

```python
if join_jump is None:
    treat_everything_after_false_endpoint_as_else()
```

也禁止全局保留或全局删除所有 `return None`。必须根据候选区域在 CFG 中的
控制作用决定。

终止型 `if/else` matcher 至少应证明：

1. 条件块的两个正常 successor 与 `_ConditionPlan` endpoint 一致；
2. True 和 False successor 都在当前 `_parse_region()` 边界内；
3. 第一分支不能通过正常边到达第二分支入口；
4. 候选分支的所有正常出口都是明确 terminator；
5. 分支没有跳转到候选区域之外；
6. False 分支没有来自候选条件之外的未知正常入口；
7. 跨越异常保护区、循环边、异常 cleanup 或其他特殊协议时，必须能够证明
   归属，否则拒绝；
8. 任一跳转目标、block ownership、region end 或 terminator 不满足预期时，
   不生成猜测的 `orelse`。

如果 matcher 不命中，但第一分支确实以 `RETURN_VALUE` 终止，则恢复结果必须
至少保留一个裸 `return`，确保后续代码不能错误落入该分支。

如果连控制转移也无法安全表达，应抛出带稳定 code name 和 offset 的
`UnsupportedPython311ControlFlow` 或现有等价的 Parser311 控制流错误。

## 6. 目标判定表

| 形态 | 处理 |
| --- | --- |
| 存在 canonical `JUMP_FORWARD` join | 保持现有 if/else 路径 |
| 无 join，两个 successor 都严格终止，两个分支都有实际主体 | 恢复终止型 `ast.If(..., orelse=...)` |
| 无 join，False successor 以另一个终止型条件开始 | 恢复嵌套 `ast.If`，输出为 `elif` |
| True 分支是显式早退，False 是普通后续区域 | 保留裸/带值 return；允许输出早退形式 |
| False 分支只有 compiler-generated `return None` | 恢复普通无 else 的 `if`，消费安全的末尾返回 |
| True/False 物理顺序反转 | 只有完整验证 endpoint 和条件极性后才交换/取反 |
| 含无法归属的交叉边、外部入口或协议跳转 | fail-closed |

## 7. 预计修改范围

| 文件 | 预计修改内容 |
| --- | --- |
| `decompyle3/controlflow/structures.py` | 增加终止分支计划、CFG 严格验证、terminal if/else 接入和 region end 传递 |
| `decompyle3/parsers/p311/base.py` | 必要时增加控制关键 implicit-None return 的上下文保留机制 |
| `pytest/test_controlflow311.py` | 增加 CFG、AST 结构和行为回归 |
| `pytest/test_syntax311.py` | 增加终止型 if/else/elif 的语法和 AST 断言 |
| `pytest/test_reliability311.py` | 增加事件序列差分行为测试 |
| 新增或现有 fail-closed 测试文件 | 增加损坏目标、交叉入口、尾部破坏等负向测试 |
| `test/fixtures311/terminal_if_else.py` | 集中保存 canonical 终止型条件 fixture |
| `pytest/behavior_cases311.py` | 为新增 shape 增加独立行为探针 |
| `test/bytecode_3.11/shape_matrix.json` | 增加 terminal if/else 和 terminal if/elif/else shape |
| `PYTHON_311_SUPPORT.md` | 记录支持范围和 fail-closed 边界 |
| 生成的 coverage/release/realworld 报告 | 在门禁通过后更新 |

外部 `Globals*` 文件不提交到本仓库。

## 8. 阶段 0：冻结失败基线

任务：

- [x] 检查 `git status` 和当前提交；
- [x] 运行当前全量测试，记录实际基线；
- [x] 增加最小 terminal if/else fixture；
- [x] 增加最小 terminal if/elif/else fixture；
- [x] 固定 normalized token 和 CFG block/edge 形态；
- [x] 固定当前错误恢复结果；
- [x] 确认测试在修复前因分支重复执行或 AST 缺少 orelse 而失败；
- [x] 记录 `Globals.original.marshal` 的 SHA-256，但不提交样本。

最小 fixture 至少包含：

```python
def terminal_if_else(flag, events):
    if flag:
        events.append("left")
    else:
        events.append("right")


def terminal_if_elif(value, events):
    if value == 1:
        events.append("one")
    elif value == 2:
        events.append("two")
    else:
        events.append("other")
```

基线测试必须同时断言：

- 恢复 AST 的 `If.orelse`；
- 每次调用只记录一个事件；
- 条件只求值一次；
- 最终 else 不是无条件语句。

阶段完成标准：

- [x] 失败能够稳定复现；
- [x] 失败原因明确指向 terminal branch structuring，而不是 scanner 或
  normalizer；
- [x] 没有修改生产实现。

## 9. 阶段 1：建立终止分支计划模型

在 `StructuredDecompiler311` 中增加局部、只服务于 CPython 3.11 结构恢复的
终止分支分析模型。建议使用私有 dataclass，例如：

```python
@dataclass(frozen=True)
class _TerminalIfPlan:
    test: ast.expr
    body_start: int
    body_end: int
    orelse_start: int
    orelse_end: int
    body_exit_kind: str
    orelse_exit_kinds: FrozenSet[str]
```

实际字段可根据实现调整，但必须显式携带 region 边界和退出类型，不能只返回
一个布尔值。

建议增加以下私有 helper：

- `_normal_successors(block)`：只返回正常控制边，不把 exception edge 当作
  fallthrough；
- `_reachable_blocks_within(start, lower, upper)`：有界、迭代式遍历候选分支；
- `_terminal_region_exits(...)`：返回候选区域的所有正常出口和 terminator；
- `_terminal_if_plan(plan, loop, region_end)`：组合严格验证并返回计划。

实现约束：

- [x] 使用 normalized instruction offset 和 CFG block，不使用原始字节偏移猜测；
- [x] 所有遍历有明确 work limit，避免恶意 CFG 导致无限循环；
- [x] 不使用 Python 递归遍历不可信大图；
- [x] region end 在 `end == len(tokens)` 时使用稳定的 code-end sentinel；
- [x] True/False endpoint 反转时显式记录条件极性；
- [x] 不改变现有正常 join 型 if/else 行为。

初始支持范围建议只接受函数级 `RETURN_VALUE` 终止。`RAISE_VARARGS`、
`RERAISE`、循环 `break/continue` 和异常 cleanup 可以在核心回归稳定后逐项增加，
不能一次性按“任意 terminator”放宽。

阶段完成标准：

- [x] `registerOnAttrChanged` 的 CFG 能生成唯一 `_TerminalIfPlan`；
- [x] 普通 join 型 if/else 不生成 terminal plan；
- [x] 损坏目标、交叉入口和越界分支不生成 terminal plan；
- [x] helper 本身还不改变输出。

## 10. 阶段 2：收紧隐式 return 清理

目标：只有在控制作用已经被安全表达时，才能省略 `LOAD_CONST None;
RETURN_VALUE`。

推荐方案：

1. 保持 code object 真正末尾的 implicit-None return 省略规则；
2. 给 `_capture_region()` 增加明确上下文，区分：
   - 当前 return 是整个函数最终返回；
   - 当前 return 是 terminal-if matcher 已消费的结构尾；
   - 当前 return 是必须保留的分支控制转移；
3. matcher 已建立 orelse 时，可在各分支尾省略 compiler-generated None return；
4. matcher 未建立 orelse而外层会继续解析 False endpoint 时，必须输出
   `ast.Return(value=None)`，源码中表现为裸 `return`；
5. 不以 `linestart is None` 单独决定是否删除 return。

如果需要修改 `base.py`，应采用默认行为不变、由 StructuredDecompiler311 显式
开启的 hook 或上下文，避免影响 expression/lambda/class body 和其他既有路径。

必须补充以下对照：

```python
def early_return(flag, events):
    if flag:
        return
    events.append("after")


def plain_terminal_if(flag, events):
    if flag:
        events.append("hit")
```

阶段完成标准：

- [x] 早退分支不会落入后续代码；
- [x] 普通末尾 if 不被强制生成有副作用的 else；
- [x] async generator 的合法 return 限制不回退；
- [x] 现有 explicit return 测试继续通过。

## 11. 阶段 3：接入 terminal if/else

修改 `_if_statement()` 接口，使其获得当前 parse region 的真实结束位置：

```python
_if_statement(condition, loop, region_end)
```

接入顺序必须为：

1. 现有 true/false endpoint 顺序处理；
2. 现有 canonical `JUMP_FORWARD` join 路径；
3. 严格 `_terminal_if_plan()`；
4. 保留控制转移的普通 if fallback；
5. 无法证明安全时 fail-closed。

terminal plan 命中后：

- [x] 分别捕获 body 和 orelse；
- [x] 只移除计划明确拥有的 compiler-generated implicit return；
- [x] 生成一个 `ast.If`；
- [x] 返回整个 orelse region 的结束 index；
- [x] 防止外层 `_parse_region()` 再次线性解析 orelse；
- [x] 保证 pending assignment、stack 和 active-region 状态在成功和异常路径都恢复；
- [x] 保持 region work limit 和 cycle detection 生效。

对于 False 分支只有隐式 None return 的情况，可以恢复为 `orelse=[]`，但必须
正确消费该最终返回，不能留下重复解析或栈残留。

阶段完成标准：

- [x] 最小 terminal if/else 恢复出非空 `If.orelse`；
- [x] 两个事件分支保持严格互斥；
- [x] 普通 if、正常 join 型 if/else 和显式早退不回退。

## 12. 阶段 4：恢复 terminal `elif` 和嵌套结构

terminal if/elif/else 不需要单独发明 `ast.Elif`。False region 的第一次结构恢复
如果得到另一个 `ast.If`，应保持：

```python
outer_if.orelse == [inner_if]
```

源码输出器会把它打印为 `elif`。

必须覆盖：

- [x] 两分支 `if/else`；
- [x] 三分支 `if/elif/else`；
- [x] 多个 `elif`；
- [x] `elif` 内嵌套 terminal if/else；
- [x] 最终 else 中包含多条语句；
- [x] 条件带短路表达式；
- [x] 某一分支显式 return，其他分支隐式 return；
- [x] 条件极性反转的物理布局；
- [x] 函数体中 terminal chain 之前存在普通语句。

阶段完成标准：

- [x] AST 中形成嵌套 orelse 链；
- [x] 源码中生成 `elif`，而不是兄弟 `if`；
- [x] 最终 else 语句不再无条件执行；
- [x] 嵌套 matcher 不产生 region cycle 或重复消费 token。

## 13. 阶段 5：负向和 fail-closed 测试

使用 `CodeType.replace()` 或现有测试辅助方法构造损坏字节码。每个用例只改变
一个安全条件：

- [x] 条件跳转目标指向缺失 offset；
- [x] False endpoint 指向候选区域外；
- [x] True 分支移除 `RETURN_VALUE`，形成真实 fallthrough；
- [x] True 分支额外跳入 False 中部；
- [x] False 分支具有外部正常 predecessor；
- [x] 分支尾 implicit-return stack 协议被破坏；
- [x] terminator 后附加可达语句；
- [x] 构造交叉分支入口；
- [x] 构造循环 back edge；
- [x] 构造跨越候选边界的 exception region；
- [x] 构造超出 work limit 的大图；
- [x] endpoint 极性或条件块证明缺失。

断言：

- [x] 不生成猜测的 `orelse`；
- [x] 不静默删除控制关键 return；
- [x] matcher 级 CFG 损坏返回无计划，协议级损坏抛出稳定 Parser311 错误；
- [x] 协议错误包含 code name 和真实条件/分支 offset；
- [x] 大图不会泄漏 `RecursionError` 或无限循环。

## 14. 阶段 6：行为和语法验证

### 14.1 AST 验证

对每个正向 fixture：

- [x] `ast.parse(recovered)` 通过；
- [x] `compile(ast_tree, ..., "exec")` 通过；
- [x] `If.orelse` 数量符合预期；
- [x] `elif` 以嵌套 `ast.If` 表达；
- [x] 最终 else 的语句不出现在函数体顶层；
- [x] 控制关键的 `ast.Return` 在 fallback 路径存在。

### 14.2 差分行为验证

本仓库最小 fixture 可以安全执行，用事件序列验证：

- [x] True/False/每个 elif 分支只执行一次；
- [x] 条件求值次数一致；
- [x] 分支语句顺序一致；
- [x] 返回值和异常类型一致；
- [x] 自定义 `__bool__`、`__eq__`、`__hash__` 的副作用次数一致；
- [x] nested if/elif 的路径选择一致。

不得使用只比较最终 set 内容的测试，因为它无法发现重复 `.add()`。

## 15. 阶段 7：外部 `Globals` 静态验收

验证流程：

1. 计算并记录 `Globals.original.marshal` 的 SHA-256；
2. 使用 `py311tool` 在内存中把自定义 Opcode 转换成标准 Opcode；
3. 验证标准 marshal 可以被 Scanner311 和 `dis` 静态读取；
4. 包装成临时 `.pyc` 或通过现有安全适配层反编译；
5. 对恢复源码执行 `ast.parse()` 和 `compile()`；
6. 不 import、不 exec、不调用任何目标函数；
7. 静态比较以下函数的 AST：
   - `registerOnAttrChanged`；
   - `returnToLastMainScene`；
   - `returnToLastMainScene2`。

验收断言：

- [x] `registerOnAttrChanged` 具有非空 `If.orelse`；
- [x] `.add(callback)` 只位于 False/else 分支；
- [x] `returnToLastMainScene` 恢复 5 个 `elif`；
- [x] `returnToLastMainScene` 最终 `print()` 位于最终 else；
- [x] `returnToLastMainScene2` 恢复 4 个 `elif`；
- [x] 三个函数均不存在由 terminal branch flattening 产生的顶层兄弟语句；
- [x] 外部文件未被修改；
- [x] 验证期间未执行目标代码。

外部参考源码来自此前 Python 2.7 反编译结果，因此只用来校验已知函数的控制流
结构，不要求整个文件文本或 AST 完全相同。

## 16. 阶段 8：覆盖矩阵、文档和完整门禁

新增 shape：

```text
terminal_if_else
terminal_if_elif_else
```

如果实现只在一个统一 shape 中维护，也必须在 notes 和测试列表中分别说明两种
结构均被覆盖。

完整验证顺序：

1. terminal if/else 定向测试；
2. terminal if/elif/else 定向测试；
3. return cleanup 和 explicit return 回归；
4. 控制流测试文件；
5. 全量 pytest；
6. 604 文件真实语料回归；
7. Opcode inventory 和 shape behavior；
8. release gate；
9. touched Python files 的 flake8；
10. `git diff --check`；
11. 生成报告的时效检查。

必须更新：

- [x] `test/bytecode_3.11/shape_matrix.json`；
- [x] shape 数量断言；
- [x] release policy 数量；
- [x] `PYTHON_311_SHAPE_COVERAGE.md`；
- [x] `PYTHON_311_SUPPORT.md`；
- [x] `PYTHON_311_RELEASE_GATE.md`；
- [x] 必要的 realworld regression 归档和报告；
- [x] 本文执行记录。

全量门禁不允许通过只更新数量绕过失败。每个新增 shape 必须同时具有 parser、
AST 或行为层的有效测试归属。

## 17. 验收标准

修复完成必须同时满足：

- [x] 最小 terminal if/else 恢复正确；
- [x] 最小 terminal if/elif/else 恢复正确；
- [x] True/False/elif 行为严格互斥；
- [x] `registerOnAttrChanged` 恢复非空 else；
- [x] `returnToLastMainScene*` 恢复 elif/else 链；
- [x] 普通末尾 if 不产生错误副作用；
- [x] 显式早退不会落入后续代码；
- [x] 正常 `JUMP_FORWARD` 型 if/else 不回退；
- [x] 损坏或未知 CFG 继续 fail-closed；
- [x] 外部 marshal 只进行静态处理；
- [x] 全量测试不少于阶段 0 的实际基线，且无新增 skip；
- [x] 604 文件真实语料回归不退化；
- [x] 覆盖报告和 release gate 通过；
- [x] 工作区只包含本计划授权的修改。

## 18. 提交策略

建议使用独立提交：

```text
修复：支持 Python 3.11 终止型 if/else 恢复
```

提交前必须：

- [x] 检查 `git diff --check`；
- [x] 检查 staged diff 不包含外部样本；
- [x] 记录定向测试和全量测试结果；
- [x] 记录外部样本静态验证结果；
- [x] 确认工作区无无关用户改动被暂存。

提交说明正文应包含：

- 根因是 terminal branches 没有 `JUMP_FORWARD` join；
- CFG matcher 的严格证明条件；
- 控制关键 implicit return 的处理；
- fail-closed 负向覆盖；
- `Globals` 外部样本只读验证结果。

## 19. 执行记录模板

### 阶段 0

```text
完成日期：2026-08-05
实际基线提交：50fef6560de72b87e52ceadc4c426ef21331c4f5
工作区状态：生产源码无修改；仅本计划文档未跟踪
全量测试基线：880 passed, 6 skipped in 31.26s
最小失败测试：3 failed；If.orelse 为空，True 路径重复记录 left/right，
              terminal condition True 路径重复记录 true/false
Globals.original.marshal SHA-256：
6aafa80c3cb1747df7586c060c0fb6442198a59d11390d7f1568c807e7ab3693
结论：scanner/normalizer 输出稳定；失败位于 terminal branch structuring
```

### 阶段 1 到阶段 4

```text
完成日期：2026-08-05
修改文件：decompyle3/controlflow/structures.py、
          test/fixtures311/terminal_if_else.py、pytest/test_controlflow311.py
终止分支计划：新增 _TerminalIfPlan，显式记录两个物理 region、退出类型和
              implicit-None-only 状态
CFG 安全条件：仅函数级、非循环、无 exception edge、分支块完全可达且互斥拥有、
             外部 predecessor 只能来自条件块、所有正常出口均为 RETURN_VALUE、
             RETURN_VALUE 不得具有正常后继；遍历使用有界迭代 work limit
implicit return 策略：已证明 orelse 时只消费计划拥有的 implicit None return；
                     未证明 orelse 的 fallback 保留控制关键裸 return；
                     module/class 不生成非法 return
if/else 结果：恢复单一 ast.If 且 orelse 非空，分支严格互斥
if/elif/else 结果：False region 递归恢复为 ast.If，源码输出为 elif 链
拒绝的未支持形态：loop、class/module terminal plan、exception region、交叉入口、
                  foreign predecessor、越界/缺失 endpoint、非 RETURN 出口
```

### 阶段 5 到阶段 6

```text
完成日期：2026-08-05
负向测试数量：12（11 类 CFG 所有权/边界损坏 + 1 类 operand stack 协议损坏）
稳定错误类型和 offset：CFG 不可证明时不建立 terminal plan；stack 损坏抛出
                       Python311ParseError，code=plain_terminal_if，offset=52
定向测试结果：pytest/test_controlflow311.py 26 passed
行为差分结果：覆盖 True/False、多 elif、嵌套、短路、反向条件、前置语句、
              多语句 final else、显式/隐式 return、异常传播；__bool__、__eq__、
              __hash__ 调用序列一致
普通 if/早退回归：plain terminal if、canonical JUMP_FORWARD join、early return、
                  explicit returns 均通过
```

### 阶段 7

```text
完成日期：2026-08-05
外部目标：/Users/ice/Desktop/Custom/WorkCode_github/py3disassembly/dump/test/
          Globals.original.marshal
SHA-256：6aafa80c3cb1747df7586c060c0fb6442198a59d11390d7f1568c807e7ab3693
安全限制：只读取、静态解码、扫描、反编译、ast.parse/compile；未执行
registerOnAttrChanged AST：函数体只有一个 ast.If；orelse 非空；.add(callback)
                          只位于 orelse
returnToLastMainScene AST：5 个嵌套 elif；最终 print 位于 final else
returnToLastMainScene2 AST：4 个嵌套 elif；final else 非空
结果：py311tool source_bytes=18840，syntax=OK，ast.parse/compile 通过；外部文件未修改
```

### 阶段 8

```text
完成日期：2026-08-05
新增 shape：terminal_if_else、terminal_if_elif_else
全量测试：900 passed, 6 skipped in 32.75s（基线 880 passed, 6 skipped）
真实语料：604/604 decompile/syntax success，0 fail-closed，0 crash，6/6 behavior
Opcode/shape coverage：Opcode 110/110；Behavior 110/110；Shape 43 pass、
                      1 unsupported_fail_closed、0 missing
release gate：通过；文档与归档时效检查通过
静态检查：touched Python files flake8 通过；git diff --check 通过
Git 提交：随本次修复一并创建独立提交
```

## 20. 当前状态

```text
计划状态：阶段 0 到阶段 8 已执行，全部验收门禁通过
当前阶段：修复完成，创建独立 Git 提交
已确认根因：终止型分支无 JUMP_FORWARD，加上 implicit None return 被错误省略
安全原则：CFG 可证明才恢复 orelse；否则保留控制转移或 fail-closed
```
