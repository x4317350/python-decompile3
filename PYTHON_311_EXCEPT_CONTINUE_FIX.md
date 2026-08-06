# decompile3 Python 3.11 `except: continue` 控制流修复说明

## 1. 问题概述

decompile3 在处理 Python 3.11 循环内部的异常控制流时，会把原始的：

```python
try:
    value = int(key)
except:
    continue
```

错误反编译成：

```python
try:
    value = int(key)
finally:
    pass
```

这不是源码格式差异，而是明确的执行语义错误：

- 原始代码捕获异常并跳过当前循环项；
- 反编译代码不捕获异常，`finally` 执行完毕后重新抛出异常并终止函数。

建议将该问题归类为：

> Python 3.11 exception table 与循环控制流组合时，异常处理器出口被错误识别，导致 `except: continue` 被结构化为 `finally: pass`。

问题级别应视为语义错误，而不是输出质量优化。

## 2. 测试环境

- decompile3 项目：`/Users/ice/Desktop/Custom/WorkCode_github/python-decompile3`
- decompile3 版本：`3.9.4.dev0`
- 测试提交：`9324a1b4`
- 真实标准 PYC：`dump/testcfg/network.rpcentity.ClientEntities.original.fixed.pyc`
- 已知原始源码：`dump/testcfg/network.rpcentity.ClientEntities.py`
- 当前反编译结果：`dump/testcfg/network.rpcentity.ClientEntities.original.decompyle3.py`
- 问题函数：`ClientAvatar.checkBossShowTimeNotify`

## 3. 真实函数中的错误

### 3.1 原始执行流程

```python
for key, value in sixc.iteritems(params):
    try:
        eid = int(key)
    except:
        continue

    defs = NPC_EVENT_DATA.data.get(eid)
    if (
        eid == 999
        and defs
        and checkNeedNotify(
            key,
            int(value.get('mktime')),
            int(defs.get('life_time', 0)),
        )
    ):
        # 生成通知
        ...
```

当 `key` 不能转换成整数时：

1. `int(key)` 抛出异常；
2. 裸 `except` 捕获异常；
3. `continue` 跳过当前项目；
4. 循环继续处理下一个键值对。

### 3.2 当前错误反编译结果

```python
for key, value in sixc.iteritems(params):
    try:
        eid = int(key)
    finally:
        pass

    defs = NPC_EVENT_DATA.data.get(eid)
```

当 `key` 不能转换成整数时：

1. `int(key)` 抛出异常；
2. 执行 `finally: pass`；
3. 原异常被重新抛出；
4. 整个函数终止；
5. 后面的参数不再处理。

因此两个版本的执行流程不等价。

## 4. 稳定最小复现

将以下源码保存为 `decompyle3_except_continue_repro.py`：

```python
def parse_keys(items, accepted):
    for key in items:
        try:
            value = int(key)
        except:
            continue
        accepted(value)
```

使用 Python 3.11 编译：

```bash
python3 -m py_compile decompyle3_except_continue_repro.py
```

使用当前 decompile3 反编译：

```bash
/path/to/decompyle3 \
  __pycache__/decompyle3_except_continue_repro.cpython-311.pyc
```

当前错误输出可以稳定复现为：

```python
def parse_keys(items, accepted):
    for key in items:
        try:
            value = int(key)
        finally:
            pass
        accepted(value)
```

## 5. 字节码与异常表证据

真实原始标准字节码中，`int(key)` 对应的受保护区域为：

```text
ExceptionTable:
  116 to 146 -> 150 [1]
  150 to 152 -> 158 [2] lasti
```

异常处理器的核心指令为：

```text
150  PUSH_EXC_INFO
152  POP_TOP
154  POP_EXCEPT
156  JUMP_BACKWARD  to loop_header
158  COPY
160  POP_EXCEPT
162  RERAISE
```

关键语义是：

```text
POP_EXCEPT
JUMP_BACKWARD -> 循环头
```

这表示异常已经被捕获并清理，随后跳回循环继续目标，对应源码中的：

```python
except:
    continue
```

反编译源码重新编译后，异常路径变成：

```text
PUSH_EXC_INFO
RERAISE 0
```

它对应 `try/finally` 的异常传播语义，与原始字节码不同。

## 6. 可能的根因

Python 3.11 使用 exception table 描述异常区域，不再依赖旧版本的 `SETUP_EXCEPT`、`SETUP_FINALLY` 等显式指令。控制流结构化时需要同时分析：

- exception table 的受保护范围；
- handler 入口；
- `PUSH_EXC_INFO`、`POP_EXCEPT` 与 `RERAISE`；
- handler 的实际终止边；
- handler 是否跳向循环的 continue target。

当前实现可能出现以下误判：

1. 看到异常清理尾部中的 `RERAISE` 后，把整个异常区域分类成了 `finally`。
2. 没有识别 handler 主路径中的 `POP_EXCEPT -> JUMP_BACKWARD`。
3. 没有把 `JUMP_BACKWARD` 的目标与外层循环头、continue target 关联。
4. 异常清理路径和实际 handler 主路径被错误合并。
5. CFG 结构化阶段没有保留 handler 的终止类型，最终生成了空 `finally`。

## 7. 建议的修复方向

### 7.1 根据 handler 主出口分类异常结构

不能只根据异常表尾部是否存在 `RERAISE` 判断 `except` 或 `finally`。需要检查 handler 的可达主路径：

```text
handler entry
  -> PUSH_EXC_INFO
  -> 捕获处理逻辑
  -> POP_EXCEPT
  -> control-flow terminator
```

如果主路径在 `POP_EXCEPT` 后跳转到循环 continue target，应还原为：

```python
except:
    continue
```

异常清理失败路径中的 `COPY / POP_EXCEPT / RERAISE` 不应被当成源码 `finally` 主体。

### 7.2 识别循环 continue target

建议在控制流分析阶段为循环记录：

- loop header；
- loop body entry；
- continue target；
- break target；
- loop exit；
- `for...else` 入口。

handler 清理异常状态后跳向 continue target 时，应生成 `continue`，而不是普通跳转、`pass` 或 `finally`。

### 7.3 区分 `except` 和 `finally`

`except: continue` 的特征包括：

```text
异常仅进入 handler
handler 消费异常
handler 执行 POP_EXCEPT
handler 跳向循环继续目标
正常路径不会执行 handler 主体
```

真正的 `finally` 则通常要求：

```text
正常路径和异常路径都会执行同一清理主体
异常路径执行清理后可能重新抛出原异常
正常路径执行清理后继续正常控制流
```

只有同时满足 `finally` 的正常路径与异常路径特征时，才应输出 `try/finally`。

### 7.4 保留嵌套区域归属

修复时要避免把异常表的清理 entry 与用户可见 handler 合并成同一个结构节点。建议为 CFG 节点保留：

- exception region id；
- handler depth；
- `lasti` 标志；
- handler kind；
- normal successor；
- exceptional successor；
- terminator kind。

## 8. AST 级回归断言

反编译最小用例后，可以直接检查 AST：

```python
import ast


tree = ast.parse(recovered_source)
function = tree.body[0]
loop = function.body[0]
try_node = loop.body[0]

assert isinstance(loop, ast.For)
assert isinstance(try_node, ast.Try)

# 必须存在一个 except handler。
assert len(try_node.handlers) == 1

# 不能错误生成 finally。
assert try_node.finalbody == []

handler = try_node.handlers[0]
assert handler.type is None
assert len(handler.body) == 1
assert isinstance(handler.body[0], ast.Continue)
```

该检查用于确认反编译结构，不替代动态语义测试。

## 9. 动态差分测试

### 9.1 正常和异常输入混合

```python
def test_except_continue(function):
    accepted = []

    result = function(
        ['1', 'invalid', '2'],
        accepted.append,
    )

    assert result is None
    assert accepted == [1, 2]
```

修复前的反编译结果会在 `invalid` 处抛出 `ValueError`，并且只得到：

```python
accepted == [1]
```

修复后必须跳过非法项并继续处理 `'2'`。

### 9.2 第一项非法

```python
def test_first_item_invalid(function):
    accepted = []

    function(
        ['invalid', '3'],
        accepted.append,
    )

    assert accepted == [3]
```

### 9.3 全部非法

```python
def test_all_items_invalid(function):
    accepted = []

    result = function(
        ['invalid-1', 'invalid-2'],
        accepted.append,
    )

    assert result is None
    assert accepted == []
```

### 9.4 自定义 `__int__` 异常

```python
class InvalidInteger:
    def __int__(self):
        raise RuntimeError('conversion failed')


def test_custom_int_exception(function):
    accepted = []

    function(
        [InvalidInteger(), '4'],
        accepted.append,
    )

    assert accepted == [4]
```

由于原始代码使用裸 `except`，动态测试还应确认非 `ValueError` 转换异常同样会被捕获并继续。

## 10. 真实文件验收

修复最小用例后，必须重新反编译完整文件：

```bash
cd /Users/ice/Desktop/Custom/WorkCode_github/py3disassembly

PYTHONPATH=src python3 -m py311tool decompile \
  dump/testcfg/network.rpcentity.ClientEntities.original.marshal \
  -o dump/testcfg/network.rpcentity.ClientEntities.original.decompyle3.py \
  --decompiler /Users/ice/Desktop/Custom/WorkCode_github/python-decompile3/.venv311/bin/decompyle3 \
  --fixed-pyc dump/testcfg/network.rpcentity.ClientEntities.original.fixed.pyc \
  --log dump/testcfg/network.rpcentity.ClientEntities.original.decompyle3.log \
  --force
```

`checkBossShowTimeNotify` 必须恢复为：

```python
for key, value in sixc.iteritems(params):
    try:
        eid = int(key)
    except:
        continue

    defs = NPC_EVENT_DATA.data.get(eid)
```

并验证：

1. 非法 key 不会终止函数；
2. 非法 key 后面的有效 key 仍会处理；
3. `eid == 999` 的通知条件保持不变；
4. 内部 `checkNeedNotify` 的记录更新和返回值保持不变；
5. 反编译源码可以通过 Python 3.11 语法检查和重新编译。

## 11. 必须补充的边界测试

除了最小复现，还建议覆盖：

- `except: break`；
- `except: return`；
- `except: raise`；
- `except Exception: continue`；
- 多个 `except` handler；
- `try/except/else`；
- `try/except/finally`；
- `for...else` 中的 `continue`；
- `while` 中的 `except: continue`；
- 嵌套循环中的 `except: continue`；
- handler 中再次抛出异常；
- handler 中包含条件 `continue`。

这些测试用于避免修复 `except: continue` 时破坏真正的 `finally` 或其他异常终止结构。

## 12. 验收标准

修复后应同时满足：

1. 最小复现恢复为 `except: continue`。
2. AST 中存在 catch-all handler，且 handler 主体为 `continue`。
3. AST 中不出现错误的空 `finally`。
4. 非法转换输入不向调用者传播异常。
5. 非法项之后的合法项仍能继续处理。
6. 原始 code object 与反编译后代码的异常行为和外部调用顺序一致。
7. 真实 `checkBossShowTimeNotify` 的循环异常边界正确。
8. 真正的 `try/finally` 回归测试保持通过。
9. Python 3.11 控制流、循环和异常处理测试套件全部通过。

## 13. 一句话总结

> 原始 Python 3.11 字节码的异常处理器在 `POP_EXCEPT` 后跳回循环 continue target，decompile3 却忽略了该 handler 主出口，并根据异常清理路径中的 `RERAISE` 将其误判为 `finally`；修复时应结合 exception table、handler 主路径和循环 continue target 恢复 `except: continue`。

## 14. 实施结果

修复在 decompyle3/controlflow/exception_structures.py 的异常结构分类层完成，没有跳过 opcode、吞掉异常或修改反编译后的文本。

### 14.1 两个根因

1. _handler_is_bare() 将 PUSH_EXC_INFO / POP_TOP / POP_EXCEPT / JUMP_BACKWARD 一律排除为裸 except。该规则原本用于保护真正的 finally: continue，但也排除了具有相同异常清理前缀的 except: continue。
2. 分类失败后，try_statement() 默认进入 _try_finally()。handler 主路径已经消费异常并跳向循环 continue target，但该控制转移被当成 finally 的正常/异常复制，最终生成 finally: pass 或 finally: continue，丢失捕获语义。

### 14.2 修复方法与安全边界

对具有歧义的 handler，不再仅根据 handler 指令前缀分类，而是检查 exception table 受保护区域的正常出口：

- 裸 except 的正常路径通过 JUMP_FORWARD 跳过 handler，并落到 handler 之后的正常 continuation；
- 真正的 finally: continue/break 会在正常路径执行一份复制的 finally 控制转移，不具备上述“绕过 handler”的正常边；
- 只有能证明前向正常边跨过 handler 时才分类为裸 except；
- 缺少目标、范围或正常边证据时继续按 finally 处理，保持 fail-closed。

分类正确后，现有 handler 恢复逻辑会根据外层循环的 continue/break target，将 POP_EXCEPT 后的跳转恢复为 ast.Continue 或 ast.Break。

### 14.3 自动化测试

新增 pytest/test_except_continue311.py，包含两组动态语义测试：

1. catch-all except: continue：
   - 正常与非法输入混合；
   - 第一项非法；
   - 全部非法；
   - 自定义 __int__ 抛出 RuntimeError；
   - handler 外回调异常的类型、消息、调用顺序及传播行为。
2. 相邻控制流边界：
   - 嵌套 for 中的 except: continue；
   - except: break；
   - except ValueError: continue；
   - 真正的 finally: continue，包括异常被 continue 覆盖的语义。

测试同时检查反编译 AST、重新编译、函数参数/闭包元数据，以及原函数与恢复函数的返回值精确类型、异常和副作用顺序。

### 14.4 验证结果

- 新增回归测试：2 passed
- 异常、循环及 with/finally 相关测试：189 passed
- 完整测试：1033 passed, 6 skipped
- 真实项目回归：604/604 反编译成功，604/604 语法验证成功，fail-closed 与未包装崩溃均为 0
- ClientEntities.original.fixed.pyc：455 个 code object 全部完成反编译，生成源码可以重新编译
- checkBossShowTimeNotify 已恢复为 catch-all handler，主体为 continue，不再生成错误的 finally

外部 pyc 在验证过程中只读取和反编译，没有执行其中代码。
