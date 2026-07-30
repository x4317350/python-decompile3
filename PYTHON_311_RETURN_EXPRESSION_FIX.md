# Python 3.11 混合短路返回表达式修复方案

## 1. 问题说明

CPython 3.11 源码：

```python
def make_offset(base):
    def apply(value):
        return (value and value + base) or base

    return apply
```

当前工程会将内部函数错误地恢复为：

```python
def apply(value):
    if value:
        pass
    return value + base or base
```

两者并不语义等价。当 `base=10`、`value=None` 时：

- 原函数利用短路求值，不执行加法，返回 `10`；
- 恢复函数执行 `None + 10`，抛出 `TypeError`。

显式语句版本当前可以正确恢复：

```python
def apply(value):
    if not value:
        return base

    result = value + base
    if not result:
        return base

    return result
```

本次修复必须保持显式 `if`、普通条件语句、循环和异常结构的现有行为。

## 2. 根因

目标表达式在 CPython 3.11 中生成的关键指令为：

```text
LOAD_FAST value
POP_JUMP_FORWARD_IF_FALSE -> fallback
LOAD_FAST value
LOAD_DEREF base
BINARY_ADD
JUMP_IF_TRUE_OR_POP -> return
fallback:
LOAD_DEREF base
return:
RETURN_VALUE
```

`ExpressionDecompiler311` 已经可以将完整指令区域恢复为语义等价表达式：

```python
(value + base or base) if value else base
```

问题发生在 `StructuredDecompiler311._parse_region()`：

1. 首个 `POP_JUMP_FORWARD_IF_FALSE` 被 `_condition_plan()` 识别；
2. `_try_if_expression()` 不能识别这种混合 `POP_JUMP` 与
   `JUMP_IF_TRUE_OR_POP` 的返回表达式；
3. `_if_statement()` 将前半部分错误输出为 `if value: pass`；
4. 剩余指令被单独输出成无条件加法。

因此修复点位于 Python 3.11 结构解析和表达式解析之间的路由，不需要修改
Scanner、Token 规范化或源码打印器。

## 3. 修复原则

- 仅修改 CPython 3.11 Parser；
- 异常、with、match、生成器协议和循环保持更高优先级；
- 只尝试包含条件跳转并以单一 `RETURN_VALUE` 结束的候选区域；
- 所有跳转目标必须闭合在候选区域内；
- 第一版拒绝反向跳转和异常保护区域；
- 只有完整表达式恢复成功后才修改父解析器状态；
- `Python311ParseError` 必须回退到现有语句解析器；
- 不使用源码文本替换修复 AST；
- 不要求恢复文本与原源码相同，但必须保证行为、类型、异常和副作用顺序一致。

## 4. 实现步骤

### 步骤 1：增加受限 return 表达式恢复

在 `decompyle3/controlflow/structures.py` 的
`StructuredDecompiler311` 中增加 `_try_return_expression()`。

候选检查：

1. 当前 operand stack、pending assignment、pending boolean 和 pending
   keyword 状态必须为空；
2. 从当前索引寻找第一个 `RETURN_VALUE`；
3. 候选区域必须包含 `JUMP_IF_*` 或 `POP_JUMP_*`；
4. 候选区域不得包含反向跳转；
5. 每个跳转目标必须位于候选区域的物理 offset 集合中；
6. 候选区域不得位于 exception-table 保护范围；
7. 调用 `recover_expression311()` 恢复完整表达式；
8. 只捕获 `Python311ParseError` 并返回 `None`；
9. 成功后追加 `ast.Return`，返回 `RETURN_VALUE` 后的 token 索引。

### 步骤 2：调整条件控制流优先级

在 `_parse_region()` 中保持下列顺序：

```text
异常、with、match 和协议结构
        ↓
循环恢复
        ↓
受限 return 表达式恢复
        ↓
现有三元表达式恢复
        ↓
现有 if 语句恢复
```

显式 `if` 的首个条件跳转会跨过第一个 `RETURN_VALUE`，不满足目标闭合
条件，因此必须回退到现有语句解析器。

### 步骤 3：增加行为回归测试

在 `pytest/test_expressions311.py` 增加 exec 模式源码恢复辅助函数以及以下
测试：

- 混合 `(value and value + base) or base`；
- `value=0`；
- `value=5`；
- `value=-10`，验证加法结果为假值时的 fallback；
- `value=None`；
- `value=False`；
- `value=0.0`，同时比较返回类型；
- 自定义假值对象，验证不会调用 `__add__`；
- 断言恢复 AST 不包含 `if value: pass`。

### 步骤 4：锁定显式 if 和语句控制流

增加显式 `if` 版本的行为测试，确保：

- 仍然生成两个真正的 `ast.If`；
- 不被重写成单个 return 表达式；
- 所有边界输入行为一致。

现有 `pytest/test_controlflow311.py`、`pytest/test_exceptiontable311.py`
继续覆盖多 return、循环和异常控制流。

### 步骤 5：加强端到端样例

在 `PYTHON_311_E2E_VALIDATION_PLAN.md` 中将 offset 调用扩展为：

```python
"offset": [
    offset(0),
    offset(5),
    offset(None),
    offset(-10),
]
```

预期输出：

```json
"offset": [10, 15, 10, 10]
```

## 5. 验证命令

定向测试：

```bash
.venv311/bin/python -m pytest \
  pytest/test_expressions311.py \
  pytest/test_controlflow311.py \
  pytest/test_exceptiontable311.py -q
```

全量测试：

```bash
.venv311/bin/python -m pytest -q
```

端到端验证：

```text
按照 PYTHON_311_E2E_VALIDATION_PLAN.md 的步骤重新生成：
sample311.py -> sample311.pyc -> sample311_recovered.py
```

## 6. 验收标准

- [x] 混合短路表达式不再生成 `if value: pass`；
- [x] `None` 和自定义假值对象不会执行加法；
- [x] 返回值和返回类型与原函数一致；
- [x] 加法结果为假值时仍返回 `base`；
- [x] 显式 `if` 版本恢复结果和行为不变；
- [x] 普通条件、循环和异常结构没有新增回归；
- [x] 定向测试全部通过；
- [x] 全量测试没有新增失败或跳过；
- [x] Python 3.11 端到端行为对比通过；
- [x] Git 工作区只包含本方案涉及的预期文件。

## 7. 风险和回退

主要风险是将真正的语句控制流误识别为表达式。防护方式：

- 跳转目标闭合检查；
- 反向跳转和异常区域拒绝；
- 表达式恢复器的 CFG、栈深和 merge 检查；
- 失败时不修改父解析器状态；
- 显式 `if`、多 return、循环和异常测试。

如果出现回归，只需要撤销：

1. `StructuredDecompiler311._try_return_expression()`；
2. `_parse_region()` 中对它的调用；
3. 对应新增测试和端到端样例更新。

Scanner、Normalizer、legacy Spark grammar 和其他 Python 版本不会受到影响。

## 8. 执行记录

- 实现提交：本文件所在 Git 提交，提交说明为
  `fix: preserve Python 3.11 mixed short-circuit returns`；
- 表达式测试：`19 passed in 0.14s`；
- 定向测试：`35 passed in 0.35s`；
- 全量测试：`110 passed, 6 skipped in 2.26s`；
- 静态检查：`flake8` 通过，`git diff --check` 通过；
- 端到端临时目录：
  `/tmp/python-decompile3-return-fix-e2e.5T6jQR`；
- `.pyc` magic number：`a70d0d0a`；
- `.pyc` 大小：`3010` 字节；
- 恢复表达式：
  `return value + base or base if value else base`；
- 端到端输出：
  `{"accumulator": 9, "division": [[4, "done"], [null, "done"]], "message": "large:27", "offset": [10, 15, 10, 10], "selected": [3, 9, 15]}`；
- 端到端结果：原源码、`.pyc` 和恢复源码输出完全一致；
- 已知限制：第一版主动拒绝异常保护区域和反向跳转中的候选表达式；
- 源码形态：恢复结果保证语义等价，不保证还原成原始 `and/or` 文本。
