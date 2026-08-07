# decompile3 Python 3.11 条件表达式公共后缀规范化说明

## 1. 问题结论

decompile3 当前会把一部分原本清晰的短路布尔条件恢复成嵌套条件表达式 `IfExp`。

真实样本的 Python 3.11 反编译结果为：

```python
if len(replace) == 2 if _is_show_model(model) else force_use_high_peishi and len(replace) == 2:
    hide_socket_name = replace[1]
else:
    hide_socket_name = replace[0]
```

Python 2.7 原版为：

```python
if (_is_show_model(model) or force_use_high_peishi) and len(replace) == 2:
    hide_socket_name = replace[1]
else:
    hide_socket_name = replace[0]
```

两个版本在返回值真值、短路路径、函数调用次数、求值顺序和异常顺序方面均等价，因此当前结果不是执行语义错误。

但 Python 3.11 结果存在明显的可读性问题：

- 把普通的 `or + and` 短路条件恢复成了三元表达式；
- 公共条件 `len(replace) == 2` 被重复输出；
- 阅读者容易误认为两个版本存在边界差异；
- 后续维护和人工语义检查成本增加。

建议将问题命名为：

> CPython 3.11 条件决策 DAG 展开时未提取公共布尔后缀，导致等价短路条件被输出为重复 `IfExp`。

该问题应归类为条件表达式规范化和源码可读性优化。

## 2. 测试环境

- decompile3 工程：`/Users/ice/Desktop/Custom/WorkCode_github/python-decompile3`
- 真实输入：`dump/dump_marshal_only_use/com.utils.helpers.original.marshal`
- 当前输出：`dump/dump_marshal_only_use_decompiled/source/com.utils.helpers.py`
- Python 2.7 对照：`dump/decopile_with_python2.7/com.utils.helpers.py`
- 问题函数：`special_effect_model_change`
- 当前问题位置：`hide_socket_name` 选择逻辑

## 3. 逻辑等价证明

定义：

```python
A = _is_show_model(model)
B = force_use_high_peishi
C = len(replace) == 2
```

当前 Python 3.11 表达式为：

```python
C if A else B and C
```

Python 2.7 表达式为：

```python
(A or B) and C
```

### 3.1 分支展开

当 `A` 为真时：

```text
C if A else B and C
=> C

(A or B) and C
=> A 为真，不计算 B
=> C
```

当 `A` 为假时：

```text
C if A else B and C
=> B and C

(A or B) and C
=> B and C
```

因此两者完全等价。

### 3.2 真值表

| A | B | C | `C if A else B and C` | `(A or B) and C` |
|---:|---:|---:|---:|---:|
| False | False | False | False | False |
| False | False | True | False | False |
| False | True | False | False | False |
| False | True | True | True | True |
| True | False | False | False | False |
| True | False | True | True | True |
| True | True | False | False | False |
| True | True | True | True | True |

### 3.3 求值顺序

两个表达式都执行：

1. 先计算 `A`；
2. `A` 为真时不计算 `B`，随后计算 `C`；
3. `A` 为假时计算 `B`；
4. `B` 为假时不计算 `C`；
5. `B` 为真时计算 `C`。

因此以下行为一致：

- `_is_show_model(model)` 的调用次数；
- `force_use_high_peishi` 的读取条件；
- `len(replace)` 的调用条件和次数；
- 任一表达式抛出异常时的先后顺序。

### 3.4 不仅限于布尔值

以下恒等式对普通 Python 对象的返回值也成立：

```python
E if A else B and E
```

等价于：

```python
(A or B) and E
```

原因是：

- `A` truthy 时，两者都返回 `E`；
- `A` falsy 且 `B` falsy 时，两者都返回原始的 falsy `B` 对象；
- `A` falsy 且 `B` truthy 时，两者都返回 `E`。

该规则不要求把参与表达式的对象强制转换成 `bool`。

## 4. 稳定最小复现

建议新增测试源码：

```python
def choose(model, force_use_high_peishi, replace, show):
    if (show(model) or force_use_high_peishi) and len(replace) == 2:
        return replace[1]
    return replace[0]
```

使用 CPython 3.11 编译后，当前 decompile3 可稳定输出：

```python
def choose(model, force_use_high_peishi, replace, show):
    if len(replace) == 2 if show(model) else force_use_high_peishi and len(replace) == 2:
        return replace[1]
    return replace[0]
```

期望输出：

```python
def choose(model, force_use_high_peishi, replace, show):
    if (show(model) or force_use_high_peishi) and len(replace) == 2:
        return replace[1]
    return replace[0]
```

## 5. 原始字节码证据

真实 `special_effect_model_change` 中相关指令为：

```text
1686  PUSH_NULL
1688  LOAD_GLOBAL              _is_show_model
1700  LOAD_DEREF               model
1702  PRECALL
1706  CALL
1716  POP_JUMP_FORWARD_IF_TRUE  to 1722
1718  LOAD_FAST                force_use_high_peishi
1720  POP_JUMP_FORWARD_IF_FALSE to 1780
1722  PUSH_NULL
1724  LOAD_GLOBAL              len
1736  LOAD_FAST                replace
1738  PRECALL
1742  CALL
1752  LOAD_CONST               2
1754  COMPARE_OP               ==
1760  POP_JUMP_FORWARD_IF_FALSE to 1780
1762  LOAD_FAST                replace
1764  LOAD_CONST               1
1766  BINARY_SUBSCR
1776  STORE_FAST               hide_socket_name
1778  JUMP_FORWARD             to 1796
1780  LOAD_FAST                replace
1782  LOAD_CONST               0
1784  BINARY_SUBSCR
1794  STORE_FAST               hide_socket_name
```

关键控制流为：

```text
A 为真  ---------------------> C
A 为假 -> B 为真 -----------> C
A 为假 -> B 为假 -----------> false 分支
C 为假 ----------------------> false 分支
```

`C` 是两个成功入口共享的公共判断节点。该 CFG 更自然的源码表达是：

```python
(A or B) and C
```

## 6. 根因定位

主要相关文件：

```text
decompyle3/controlflow/structures.py
decompyle3/parsers/p311/expressions.py
decompyle3/parsers/p311/comprehensions.py
```

重点函数：

```text
_combine_decision
StructuredDecompiler311._if_expression_condition_plan
StructuredDecompiler311._bounded_condition_plan
```

当前条件恢复会从 CFG 决策图递归构造 AST。公共后继节点在递归结果中被展开到两个分支：

```python
ast.IfExp(
    test=A,
    body=C,
    orelse=ast.BoolOp(
        op=ast.And(),
        values=[B, C],
    ),
)
```

`ast.unparse()` 随后忠实输出为：

```python
C if A else B and C
```

因此问题不在 `ast.unparse()`，也不应通过最终源码字符串替换修复。真正需要处理的是条件 DAG 到 AST 的规范化过程。

## 7. 推荐的最小修复规则

第一阶段只增加本次真实样本所需、能够完整证明的恒等式：

```python
E if A else B and E
```

规范化为：

```python
(A or B) and E
```

如果 `else` 分支包含多个 `and` 前缀：

```python
E if A else B1 and B2 and E
```

可以规范化为：

```python
(A or (B1 and B2)) and E
```

该规则保持：

- 求值顺序；
- 短路边界；
- 表达式返回对象；
- 调用次数；
- 异常顺序。

## 8. 参考实现

建议先在 `decompyle3/controlflow/structures.py` 中实现，因为真实问题由 statement-level 条件恢复产生。

增加 AST 结构比较：

```python
def _same_expression(left: ast.AST, right: ast.AST) -> bool:
    return ast.dump(
        left,
        include_attributes=False,
    ) == ast.dump(
        right,
        include_attributes=False,
    )
```

增加公共后缀提取：

```python
def _factor_ifexp_common_and_suffix(
    predicate: ast.expr,
    when_true: ast.expr,
    when_false: ast.expr,
) -> Optional[ast.expr]:
    if not (
        isinstance(when_false, ast.BoolOp)
        and isinstance(when_false.op, ast.And)
        and len(when_false.values) >= 2
        and _same_expression(when_true, when_false.values[-1])
    ):
        return None

    prefix_values = when_false.values[:-1]
    if len(prefix_values) == 1:
        alternate_guard = prefix_values[0]
    else:
        alternate_guard = ast.BoolOp(
            op=ast.And(),
            values=list(prefix_values),
        )

    combined_guard = ast.BoolOp(
        op=ast.Or(),
        values=[predicate, alternate_guard],
    )

    return ast.BoolOp(
        op=ast.And(),
        values=[combined_guard, when_true],
    )
```

在 `_combine_decision()` 返回通用 `ast.IfExp` 之前调用：

```python
factored = _factor_ifexp_common_and_suffix(
    predicate,
    when_true,
    when_false,
)
if factored is not None:
    return factored

return ast.IfExp(
    test=predicate,
    body=when_true,
    orelse=when_false,
)
```

若使用现有 `_boolean_operation()`，可以让同类 `and`、`or` 节点自动扁平化，但必须保持值的原始顺序。

## 9. 多个 `_combine_decision` 的处理建议

当前工程至少在以下位置存在相似实现：

```text
decompyle3/controlflow/structures.py
decompyle3/parsers/p311/expressions.py
decompyle3/parsers/p311/comprehensions.py
```

建议分两步处理。

第一步：

- 只修复 `controlflow/structures.py`；
- 使用真实样本和最小复现验证；
- 确认 statement-level `if` 输出已经规范化；
- 避免一次改动影响表达式、lambda 和 comprehension 恢复。

第二步：

- 将经过验证的恒等式提取到公共 AST 规范化模块；
- 让三个 `_combine_decision` 复用同一实现；
- 分别增加普通表达式、返回值、lambda 和 comprehension 测试；
- 消除不同恢复路径之间的输出差异。

不建议一次性合并三个实现后再测试，因为它们目前对布尔常量和值语义的处理并不完全相同。

## 10. 不安全的化简方式

### 10.1 不得交换求值顺序

不要把原条件改写为：

```python
C and (A or B)
```

虽然对纯布尔值的真值表相同，但它会先计算 `C`，而原字节码先计算 `A`。

在真实代码中，这会改变：

- `_is_show_model(model)` 和 `len(replace)` 的调用顺序；
- 当 `replace` 不支持 `len()` 时抛出异常的时机；
- 带副作用表达式的执行路径。

正确输出必须保持：

```python
(A or B) and C
```

### 10.2 不得只比较源码字符串

以下表达式可能只是括号或引号不同：

```python
left
(left)
```

公共表达式判断应使用：

```python
ast.dump(node, include_attributes=False)
```

不能使用 `ast.unparse(left) == ast.unparse(right)` 作为唯一结构证明。

### 10.3 不得对任意 `IfExp` 强行转成 `and/or`

例如：

```python
X if A else Y
```

通常不能改写为：

```python
A and X or Y
```

当 `X` 为 falsy 时，后者会错误返回 `Y`。

只有经过严格证明的公共后缀模式才能规范化。

### 10.4 不得通过文本替换修复

不能对最终源码执行类似：

```text
"C if A else B and C" -> "(A or B) and C"
```

真实表达式可能存在嵌套、不同括号、相同变量名或不同优先级。修复必须作用于 AST。

## 11. 回归测试设计

建议新增：

```text
pytest/test_conditional_factoring311.py
```

### 11.1 AST 形状测试

```python
def test_common_and_suffix_is_factored():
    recovered = recover(SOURCE)
    tree = ast.parse(recovered)
    function = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "choose"
    )
    condition = next(
        node.test
        for node in ast.walk(function)
        if isinstance(node, ast.If)
    )

    assert isinstance(condition, ast.BoolOp)
    assert isinstance(condition.op, ast.And)
    assert not any(
        isinstance(node, ast.IfExp)
        for node in ast.walk(condition)
    )
```

还应断言 AST 形状为：

```text
And
├── Or
│   ├── A
│   └── B
└── C
```

### 11.2 真值表测试

至少覆盖 `A × B × C` 的 8 组布尔组合，比较原始 code object 与反编译后重编译 code object 的返回结果。

### 11.3 求值事件测试

```python
def probe(events, name, value):
    events.append(name)
    return value


def target(a, b, c, events):
    return (
        probe(events, "c", c)
        if probe(events, "a", a)
        else probe(events, "b", b)
        and probe(events, "c", c)
    )
```

规范化前后应逐项比较：

- 返回值；
- 返回值类型；
- `events` 调用顺序；
- 每个 probe 的调用次数。

期望事件顺序：

| A | B | 事件 |
|---:|---:|---|
| True | 任意 | `a, c` |
| False | True | `a, b, c` |
| False | False | `a, b` |

### 11.4 非布尔返回值测试

使用自定义 truthy/falsy 对象，断言规范化前后返回的是相同对象，而不只是相同的 `bool()`：

```python
assert recovered_result is original_result
```

至少覆盖：

- truthy `A`；
- falsy `A` 与 falsy `B`；
- falsy `A` 与 truthy `B`；
- falsy `E`；
- truthy `E`。

### 11.5 异常顺序测试

分别让 `A`、`B`、`C` 抛出异常，确认：

- `A` 异常时不计算 `B` 和 `C`；
- `A` truthy 时不计算 `B`；
- `A` falsy、`B` 异常时不计算 `C`；
- 只有需要 `C` 的路径才传播 `C` 的异常。

### 11.6 拒绝测试

以下结构不能被该规则改写：

```python
X if A else B and Y
```

其中：

```python
ast.dump(X) != ast.dump(Y)
```

还应拒绝：

- `else` 不是 `ast.BoolOp(ast.And)`；
- `else` 没有至少两个 operand；
- 公共表达式不在 `and` 的最后一个位置；
- 只有文本相似、AST 不相同的表达式；
- 需要交换 `A`、`B`、`C` 求值顺序才能化简的结构。

## 12. 真实文件验收

修复后重新反编译：

```text
dump/dump_marshal_only_use/com.utils.helpers.original.marshal
```

在 `special_effect_model_change` 中应恢复为：

```python
if (_is_show_model(model) or force_use_high_peishi) and len(replace) == 2:
    hide_socket_name = replace[1]
else:
    hide_socket_name = replace[0]
```

验收时比较原始 code object 与新源码重编译结果，至少覆盖：

- `_is_show_model(model)` 返回真和假；
- `force_use_high_peishi` 返回真和假；
- `replace` 长度为 0、1、2、3；
- `replace` 不支持 `len()`；
- `_is_show_model` 抛出异常；
- 自定义对象的 truthy/falsy 行为；
- 调用顺序和调用次数。

## 13. 建议执行的测试

```bash
cd /Users/ice/Desktop/Custom/WorkCode_github/python-decompile3

PYTHONDONTWRITEBYTECODE=1 \
PYTHONPATH=. \
.venv311/bin/python -m pytest -q -p no:cacheprovider \
  pytest/test_conditional_factoring311.py \
  pytest/test_controlflow311.py \
  pytest/test_expressions311.py \
  pytest/test_controlflow311_short_circuit_values.py \
  pytest/test_source_functional_differences311.py \
  pytest/test_patch_helpers_regression311.py
```

同时重新执行真实 marshal 批量反编译，并确认：

- 17 个源码均可通过 AST 解析；
- 17 个源码均可通过 Python 3.11 编译；
- 不可达语句扫描没有新增结果；
- 函数和嵌套 code object 数量没有新增差异；
- 之前修复的 `SubPatch`、`get_loop_act_time`、`in_list` 等控制流没有回归。

## 14. 后续可扩展规则

在最小规则稳定后，可以分别研究以下对偶形式：

```python
(B or E) if A else E
```

可能规范化为：

```python
(A and B) or E
```

以及：

```python
(B and E) if A else E
```

可能规范化为：

```python
(not A or B) and E
```

这些规则必须分别证明：

- 返回对象保持一致；
- truthy/falsy 行为一致；
- 求值顺序一致；
- 异常传播顺序一致。

不建议在本次修复中一次加入全部代数规则。优先完成真实样本对应的：

```python
E if A else B and E
    ->
(A or B) and E
```

## 15. 最终验收标准

修复完成需要同时满足：

1. 最小复现不再生成 `IfExp`；
2. 真实 `special_effect_model_change` 恢复为清晰的 `(A or B) and C`；
3. 8 组布尔真值全部一致；
4. 求值事件顺序完全一致；
5. 非布尔对象的返回值及对象身份一致；
6. 异常发生顺序一致；
7. 不匹配的 `IfExp` 不被错误重写；
8. 不交换短路表达式的求值顺序；
9. 不通过源码字符串替换完成修复；
10. 真实 marshal、现有表达式测试和控制流测试全部通过。
