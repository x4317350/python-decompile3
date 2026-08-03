# Python 3.11 `except*` 空主体修复计划

## 1. 目标

修复 CPython 3.11 `except*` 子句主体在编译后为空时，Parser311 错误要求
主体必须具有 depth >= 4 异常表保护区的问题。

目标源码形态：

```python
def exception_group_ops():
    try:
        raise ExceptionGroup("eg", [ValueError(1)])
    except* ValueError:
        pass
```

目标恢复结果必须至少语义等价于：

```python
def exception_group_ops():
    try:
        raise ExceptionGroup("eg", [ValueError(1)])
    except* ValueError:
        pass
```

本计划只处理 CPython 3.11 canonical compiler 生成的空 `except*` 主体协议。
未知、损坏、人工编辑或无法证明安全的协议继续 fail-closed。

## 2. 已确认基线

### 2.1 目标文件

外部验证文件：

```text
/Users/ice/Desktop/Custom/WorkCode_github/py3disassembly/
py3Tool/map_opcode/fixed_output_repaired.pyc
```

该文件已经完成以下修复和检查：

- 具有标准 CPython 3.11 `.pyc` 文件头；
- 41 个代码对象均通过 Scanner311；
- opcode 映射和异常表可以正常读取；
- 完整反编译在 `exception_group_ops` 上 fail-closed；
- 验证期间不执行该文件中的代码。

当前错误：

```text
Python311ParseError: except* clause body has no protected region
('exception_group_ops', offset 66)
```

### 2.2 关键异常表差异

第一个子句源码为：

```python
except* ValueError:
    pass
```

其主体没有实际指令，因此不存在 depth = 4 的零长度异常表项。处理器仅有
外围 depth = 1 区域：

```text
68..118 -> 128 [depth=1, lasti]
```

第二个子句源码为：

```python
except* TypeError:
    raise
```

该主体包含 `RAISE_VARARGS`，因此具有正常的 depth = 4 区域：

```text
258..260 -> 260 [depth=4, lasti]
```

### 2.3 当前错误位置

当前实现位于：

```text
decompyle3/controlflow/exception_structures.py
ExceptionStructureDecompiler311._try_except_star()
```

现有逻辑无条件要求：

```python
region.start == self.tokens[body_start].offset
and region.depth >= 4
```

找不到区域时立即报错。这个条件对非空主体成立，但对编译后没有指令的
空主体不成立。

错误消息中的 offset 66 来自外层 `current_token`，实际缺少保护区的主体
入口是 offset 96。错误 offset 不影响根因判断，但需要一并改善。

## 3. 根因和修复边界

### 3.1 根因

CPython 3.11 使用 zero-cost exception table。异常表只能描述非空指令范围，
不能创建零长度保护区。以下主体会被编译器优化成没有源语义指令的形态：

```python
except* ValueError:
    pass

except* ValueError:
    ...

except* ValueError:
    assert True

except* ValueError:
    if False:
        action()
```

它们只保留 `except*` 分组、清理和合流协议。反编译器必须通过该协议确认
这是一个空主体，而不能依赖不存在的 depth = 4 区域。

### 3.2 不需要修改的层

本问题不需要修改：

- Python 3.11 opcode 映射；
- Scanner311；
- Normalizer311；
- exception table decoder；
- legacy Spark grammar；
- 其他 Python 版本解析器。

修复点只位于 Parser311 使用的 CPython 3.11 异常结构恢复器。

### 3.3 安全边界

禁止采用以下宽松处理：

```python
if clause_region is None:
    clause_body = [ast.Pass()]
```

保护区缺失也可能意味着字节码损坏、未知编译器形态或错误的控制流边界。
只有完整匹配已知 CPython 3.11 空主体协议时，才能恢复 `ast.Pass()`；否则
继续抛出 `Python311ParseError`。

## 4. 预计修改范围

| 文件 | 修改内容 |
| --- | --- |
| `decompyle3/controlflow/exception_structures.py` | 增加空主体协议识别并接入 `_try_except_star()`；改善错误 offset |
| `pytest/test_syntax311.py` | 增加 `TryStar` 和 `ast.Pass` 结构断言 |
| `pytest/test_reliability311.py` | 增加 ExceptionGroup 差分行为测试 |
| `pytest/test_exceptiontable311.py` | 增加空主体异常表和协议边界测试 |
| `test/bytecode_3.11/shape_matrix.json` | 增加 `except_star_empty_body` shape |
| `PYTHON_311_SHAPE_COVERAGE.md` | 记录新增 shape 和支持边界 |
| `PYTHON_311_SUPPORT.md` | 必要时同步 `except*` 支持说明 |

外部 `fixed_output_repaired.pyc` 只作为端到端验证输入，不提交到本仓库，也不
执行其中的代码。

## 5. 阶段 0：冻结最小失败基线

任务：

- [ ] 增加无名称空主体最小源码；
- [ ] 增加 `except* ValueError as error: pass` 最小源码；
- [ ] 固定 Scanner311 产生的标准化 token；
- [ ] 固定 `dis.Bytecode(...).exception_entries`；
- [ ] 断言空主体没有 depth = 4 区域；
- [ ] 断言非空主体继续具有 depth = 4 区域；
- [ ] 固定当前 `Python311ParseError`；
- [ ] 确认现有 `except*` 非空主体测试继续通过。

建议最小源码：

```python
def empty_handler(group):
    try:
        raise group
    except* ValueError:
        pass


def empty_named_handler(group):
    try:
        raise group
    except* ValueError as error:
        pass
```

退出标准：

```text
空主体失败可稳定复现
Scanner、Normalizer 和异常表解码无错误
失败只发生在 _try_except_star() 的主体区域判断
非空主体基线不回退
```

## 6. 阶段 1：实现严格的空主体协议识别器

在 `ExceptionStructureDecompiler311` 中增加私有方法，例如：

```python
def _match_empty_except_star_clause(
    self,
    body_start: int,
    false_index: int,
    prep_index: int,
    name: Optional[str],
) -> bool:
    ...
```

实现必须基于标准化 opcode、相对 token 索引和实际跳转目标，不硬编码物理
offset。

### 6.1 无名称空主体

规范形态包含：

```text
POP_TOP                         # 无 as-name 绑定
JUMP_FORWARD -> normal_join     # 空主体正常路径
LIST_APPEND 3                   # 主体抛异常时的收集路径
POP_TOP
JUMP_FORWARD -> continuation
normal_join:
JUMP_FORWARD -> continuation
false_match:
POP_TOP
continuation:
下一 except* 类型表达式或 LIST_APPEND 1
```

识别器必须验证：

- `body_start` 是前向跳转；
- 正常路径跳过异常收集协议；
- 异常收集路径包含正确深度的 `LIST_APPEND`；
- 两条路径汇合到同一个 continuation；
- `false_index` 是 subgroup 不匹配路径；
- continuation 位于 `PREP_RERAISE_STAR` 之前；
- 路径中没有其他源语义指令。

### 6.2 有名称空主体

`except* ValueError as error: pass` 还必须验证名称清理：

```text
LOAD_CONST None
STORE_* error
DELETE_* error
```

正常路径和异常收集路径中的名称清理必须同时满足：

- `STORE_*` 和 `DELETE_*` 操作同一个名称；
- 该名称与 handler 的 binding 名称一致；
- 清理顺序符合 CPython 3.11 canonical protocol；
- 清理后跳转仍汇合到相同 continuation。

### 6.3 识别失败

出现以下任一情况时返回不匹配，并由调用方继续 fail-closed：

- 缺少清理指令；
- `LIST_APPEND` 深度错误；
- 跳转目标不一致；
- 跳转越过 `PREP_RERAISE_STAR`；
- 名称清理对象不一致；
- 出现未知 token；
- 存在反向跳转；
- 找不到合法 continuation。

退出标准：

```text
已知无名称和有名称空主体协议均可被唯一识别
非空主体不会命中空主体识别器
任一协议字段被破坏后均不匹配
```

## 7. 阶段 2：接入 `TryStar` 恢复

将 `_try_except_star()` 中的主体处理调整为：

```python
if clause_region is not None:
    body_end = self.offset_to_index[clause_region.end]
    clause_body = self._capture_optional(
        body_start,
        body_end,
        loop,
    )
elif self._match_empty_except_star_clause(
    body_start,
    false_index,
    prep_index,
    name,
):
    clause_body = [ast.Pass()]
else:
    self._error(
        "except* clause body has neither a protected region "
        "nor a valid empty-body protocol",
        offset=self.tokens[body_start].offset,
    )
```

要求：

- [ ] 保留现有非空 depth >= 4 路径；
- [ ] 空主体生成 `ast.Pass()`；
- [ ] 保留 `except* ... as name` 的名称；
- [ ] 不消费下一 `except*` 子句的类型表达式；
- [ ] 不消费 `LIST_APPEND 1` 和 `PREP_RERAISE_STAR`；
- [ ] 多子句 cursor 继续从 `false_index + 1` 进入下一子句；
- [ ] `else` 和 `finally` 的现有恢复顺序保持不变；
- [ ] 未知形态继续抛出结构化错误。

退出标准：

```text
空主体恢复为 ast.TryStar + ast.ExceptHandler + ast.Pass
非空主体走原有分支
协议 token 没有泄漏为伪源码语句
异常合流位置保持正确
```

## 8. 阶段 3：改善错误位置

扩展 `ExceptionStructureDecompiler311._error()`，允许调用方传入可选 offset：

```python
def _error(self, message, offset=None):
    if offset is None:
        token = self.owner.current_token
        offset = token.offset if token is not None else "?"
    raise Python311ParseError(
        f"{message} ({self.owner.code.co_name!r}, offset {offset})"
    )
```

要求：

- [ ] 不传 offset 的现有调用行为不变；
- [ ] 空主体协议失配报告 `body_start` 的真实 offset；
- [ ] 错误中继续保留 code object 名称和 Python 版本上下文；
- [ ] 不改变现有异常类型。

退出标准：

```text
合法空主体不报错
损坏协议报告真实主体入口
现有错误消息测试没有无关回退
```

## 9. 阶段 4：测试矩阵

### 9.1 语法和 AST 测试

- [ ] 单个无名称空主体；
- [ ] 单个有名称空主体；
- [ ] 第一个子句为空、第二个非空；
- [ ] 第一个非空、第二个为空；
- [ ] 多个连续空子句；
- [ ] 编译器优化为空的 `...`；
- [ ] 编译器优化为空的常量真断言或死分支；
- [ ] 恢复结果可以 `ast.parse()` 和重新编译；
- [ ] 恢复 AST 使用 `ast.TryStar`；
- [ ] 空 handler 主体包含 `ast.Pass`；
- [ ] `as name` 保持不变。

### 9.2 组合结构测试

- [ ] 空主体加 `else`；
- [ ] 空主体加 `finally`；
- [ ] 空主体加 `else + finally`；
- [ ] 空主体位于外层普通 `try`；
- [ ] 空主体与非空 `except*` 混合；
- [ ] 空主体之后有正常 continuation 和 return。

### 9.3 差分行为测试

使用测试源码自行编译，不执行外部目标 `.pyc`。比较原源码和恢复源码：

- [ ] ExceptionGroup 完全匹配；
- [ ] ExceptionGroup 部分匹配；
- [ ] 未匹配 subgroup 重新抛出；
- [ ] 多种异常类型的分组顺序；
- [ ] `else` 是否只在正常 try 路径执行；
- [ ] `finally` 是否始终执行；
- [ ] `as name` 在 handler 结束后被正确清理；
- [ ] 返回值、异常类型和 subgroup 组成一致。

### 9.4 负向 fail-closed 测试

对 Scanner 生成的测试 token 副本进行最小破坏：

- [ ] 修改正常路径跳转目标；
- [ ] 删除一个 `POP_TOP`；
- [ ] 修改 `LIST_APPEND` 深度；
- [ ] 修改异常路径 continuation；
- [ ] 让 `STORE_*` 和 `DELETE_*` 名称不一致；
- [ ] 让跳转越过 `PREP_RERAISE_STAR`；
- [ ] 插入未知源语义 token。

每个负向用例都必须断言：

```text
抛出 Python311ParseError
不输出猜测的 ast.Pass
错误 offset 指向主体协议入口
```

退出标准：

```text
空主体语法、组合、行为和负向测试全部通过
现有 except* 非空主体测试全部通过
没有通过删除或放宽 fail-closed 测试实现支持
```

## 10. 阶段 5：目标文件端到端验证

输入：

```text
/Users/ice/Desktop/Custom/WorkCode_github/py3disassembly/
py3Tool/map_opcode/fixed_output_repaired.pyc
```

验证要求：

- [ ] 只读取和反编译，不执行其中代码；
- [ ] 标准 `.pyc` 加载继续通过；
- [ ] 41 个代码对象 Scanner311 检查继续通过；
- [ ] 不再出现 `except* clause body has no protected region`；
- [ ] `exception_group_ops` 第一个 handler 恢复为 `pass`；
- [ ] 第二个裸 `raise` handler 保持正确；
- [ ] 恢复输出通过 `ast.parse()`；
- [ ] 恢复输出通过 `compile()`；
- [ ] 记录是否出现下一个独立的 fail-closed shape。

如果目标文件在当前问题修复后暴露新的解析错误：

1. 本阶段只确认已经越过 `exception_group_ops` 当前错误；
2. 记录新错误的 code name、offset、opcode 和错误签名；
3. 不扩大本次空主体修复的匹配范围；
4. 将新问题作为独立 shape 诊断和修复。

退出标准：

```text
目标文件越过当前 except* 空主体错误
空主体恢复结果正确
没有执行目标字节码
后续独立失败有明确记录
```

## 11. 阶段 6：覆盖矩阵和全量回归

### 11.1 Shape 记录

在 `test/bytecode_3.11/shape_matrix.json` 中增加：

```text
name: except_star_empty_body
category: exception_group
status: pass
fixture: 对应的最小 3.11 fixture
```

同时更新：

- `PYTHON_311_SHAPE_COVERAGE.md`；
- `PYTHON_311_SUPPORT.md` 中 `except*` 支持边界；
- 必要的 release gate 或行为覆盖记录；
- 本文档执行记录。

opcode 覆盖数量不因本次修复变化，因为 `CHECK_EG_MATCH`、
`PREP_RERAISE_STAR` 等 opcode 已经覆盖。本次新增的是组合 shape 覆盖。

### 11.2 定向测试

```bash
.venv311/bin/python -m pytest \
  pytest/test_syntax311.py \
  pytest/test_exceptiontable311.py \
  pytest/test_reliability311.py -q
```

如果新增独立测试文件，将其加入定向命令。

### 11.3 全量测试

```bash
.venv311/bin/python -m pytest -q
```

### 11.4 静态检查

```bash
.venv311/bin/python -m flake8 decompyle3 pytest
git diff --check
git status --short
```

退出标准：

```text
定向测试全部通过
全量 pytest 无新增失败或跳过
静态检查通过
shape、支持说明和实现状态一致
Git 工作区只包含本计划范围内的文件
```

## 12. 最终验收标准

- [ ] 无名称空 `except*` 主体恢复为 `pass`；
- [ ] 有名称空 `except*` 主体保留 `as name`；
- [ ] 多个 `except*` 子句边界正确；
- [ ] `else` 和 `finally` 组合行为正确；
- [ ] ExceptionGroup 分割、处理和重新抛出语义一致；
- [ ] 非空主体恢复结果没有回退；
- [ ] 损坏或未知协议继续 fail-closed；
- [ ] 失败信息报告真实主体 offset；
- [ ] 目标 `.pyc` 越过当前 `exception_group_ops` 错误；
- [ ] 恢复源码通过语法解析和重新编译；
- [ ] 定向测试全部通过；
- [ ] 全量测试没有新增失败或跳过；
- [ ] Shape 和支持文档已经同步；
- [ ] 没有修改 Scanner、Normalizer、legacy grammar 或其他 Python 版本；
- [ ] 没有执行外部目标 `.pyc`。

## 13. 风险和回退

### 13.1 主要风险

最大的风险是把损坏或尚未支持的 `except*` 控制流误识别成空主体。防护
措施：

- 精确匹配 canonical CPython 3.11 normalized token；
- 校验全部跳转目标和 continuation；
- 校验 `LIST_APPEND` 深度；
- 校验 `as name` 的绑定和清理名称；
- 不允许未知 token；
- 匹配失败继续 fail-closed；
- 增加逐字段破坏的负向测试。

第二个风险是错误消费下一 `except*` 子句或 cleanup token。防护措施：

- continuation 必须显式返回或验证；
- 多子句空/非空排列全部测试；
- 断言 `PREP_RERAISE_STAR` 仍由现有协议逻辑处理。

### 13.2 回退范围

如果出现回归，只需要撤销：

1. `_match_empty_except_star_clause()`；
2. `_try_except_star()` 中的空主体分支；
3. `_error()` 的可选 offset 扩展；
4. 对应新增测试和 shape 文档。

Scanner、Normalizer、exception table decoder、legacy grammar 和其他 Python
版本不在回退范围内。

## 14. 建议提交顺序

建议在完整阶段产物通过后提交，避免把预期失败测试单独留在主分支：

1. 实现、最小 fixture、语法测试和负向测试；
2. 差分行为测试、组合测试和目标文件验证记录；
3. shape、支持说明、本文档执行记录和全量回归结果。

提交说明建议使用中文，例如：

```text
修复：支持 Python 3.11 except* 空主体恢复
```

提交说明正文应包含：

- 根因是零长度主体没有 depth = 4 异常表项；
- 仅接受严格匹配的 CPython 3.11 空主体协议；
- 未知形态继续 fail-closed；
- 定向测试、全量测试和目标 `.pyc` 验证结果。

## 15. 执行记录

当前状态：阶段 0 到阶段 6 的空主体修复已完成。外部目标已越过原来的空主体
错误，并暴露一个独立的终止位置 `except*` cleanup shape；该问题随后通过
独立的严格 terminal cleanup matcher 修复，没有放宽空主体 matcher。

### 阶段 0

```text
完成日期：2026-08-03
修改文件：pytest/test_exceptiontable311.py；test/fixtures311/except_star_empty_body.py
最小 fixture：无名称空主体、有名称空主体、非空主体对照
定向测试：5 passed（冻结基线时）
结果：空主体无 depth >= 4 区域；非空对照具有 36..78 depth=4 区域
备注：冻结的旧错误为 no protected region，旧外层报告 offset 为 6
```

### 阶段 1 到阶段 3

```text
完成日期：2026-08-03
修改文件：decompyle3/controlflow/exception_structures.py
实现：严格匹配无名称/有名称 canonical CPython 3.11 空主体协议
安全边界：校验全部相对 token、三条前向跳转、共同 continuation、
          LIST_APPEND 3、LIST_APPEND 1、false-match POP_TOP、
          PREP_RERAISE_STAR 边界和两条名称清理路径
接入：仅在不存在 depth >= 4 区域且 matcher 完整命中时生成 ast.Pass
错误位置：协议失配使用真实 body_start offset；结构化上下文保留 3.11 和 code name
```

### 阶段 4

```text
完成日期：2026-08-03
修改文件：pytest/test_syntax311.py；pytest/test_exceptiontable311.py；
          pytest/test_reliability311.py
覆盖：无名称/有名称、空/非空排列、连续空子句、ellipsis、assert True、
      死分支、else、finally、else+finally、外层 finally、正常 continuation
差分：完全匹配、部分匹配、未匹配 subgroup、嵌套分组、处理顺序、
      as-name 清理、返回值和异常结构
负向：正常路径目标、POP_TOP、LIST_APPEND 深度、异常 continuation、
      清理名称、越过 PREP_RERAISE_STAR、未知源语义 token
结果：全部负向用例抛出 Python311ParseError，offset=36，不生成猜测的 Pass
```

### 阶段 5

```text
完成日期：2026-08-03
目标：/Users/ice/Desktop/Custom/WorkCode_github/py3disassembly/py3Tool/
      map_opcode/fixed_output_repaired.pyc
SHA-256：b65497e7a855fecd71067f07ba6c53b636f5d9aff105665cc9deb22a790d87b0
安全：只加载、扫描和反编译；未 import、exec 或调用目标代码
Scanner：41/41 code objects 通过
空主体：exception_group_ops offset 96 严格命中，恢复为 TryStar handler Pass
非空主体：offset 258..260、depth=4、RAISE_VARARGS 0 保持可识别
目标结果：已越过 except* clause body has no protected region
新独立 shape：except* cleanup has no normal continuation
新错误：exception_group_ops，offset 258，RAISE_VARARGS 0
备注：按计划未扩大空主体 matcher；因此完整目标输出尚不能 ast.parse/compile
```

### 阶段 6

```text
完成日期：2026-08-03
Shape：except_star_empty_body；inventory 41，pass 40，fail-closed 1
定向测试：63 passed
全量测试：863 passed，6 skipped
真实语料：604/604 成功反编译，6/6 行为探针一致
静态检查：本次改动 Python 文件 flake8 通过；git diff --check 通过；
          覆盖报告、真实语料归档和 release gate 时效检查通过
仓库级 flake8：仍报告未改动 legacy 文件的既有风格错误，本次文件无新增错误
目标 .pyc 验证：越过空主体错误；随后在独立 terminal cleanup shape fail-closed
Git 提交：本次修复提交
```

### 后续 terminal cleanup 修复

```text
完成日期：2026-08-03
根因：终止位置的 canonical except* cleanup 直接进入隐式
      LOAD_CONST None；RETURN_VALUE，不生成 JUMP_FORWARD
实现：严格匹配 13-token implicit-return/reraise 后缀和 depth-1 cleanup 目标；
      匹配成功后消费到 normalized token 流末尾
安全边界：任一 opcode、参数、跳转、异常表目标或尾随 token 不符均 fail-closed；
          terminal except* + else 继续作为独立双出口 shape 拒绝
外部目标：完整反编译和 syntax verification 通过；exception_group_ops
          恢复两个 TryStar，handler 分别为 ValueError/pass 和 TypeError/raise
```
