# decompile3 Python 3.11 冗余 `return None` 修复说明

## 1. 问题概述

在 decompile3 完成 Python 3.11 控制流修复后，真实文件已经可以成功反编译，已知的条件边界和异常分支语义错误也已修复。但部分函数仍会生成冗余的显式 `return None`，甚至在无条件 `return` 后继续生成不可达的 `return None`。

这类问题目前没有改变目标函数的主要运行语义，但会产生死代码、降低源码可读性，并使原始字节码与反编译源码重新编译后的控制流指纹出现不必要的差异。

建议将其归类为：

> Python 3.11 控制流结构化后的公共函数尾声重复物化与不可达代码清理问题。

## 2. 测试环境

- decompile3 项目：`/Users/ice/Desktop/Custom/WorkCode_github/python-decompile3`
- decompile3 版本：`3.9.4.dev0`
- 测试提交：`9324a1b4`
- 标准测试 PYC：`dump/testcfg/network.rpcentity.ClientEntities.original.fixed.pyc`
- 已知原始源码：`dump/testcfg/network.rpcentity.ClientEntities.py`
- 当前反编译结果：`dump/testcfg/network.rpcentity.ClientEntities.original.decompyle3.py`

## 3. 问题一：`onEnterBattleField` 生成不可达的重复返回

### 3.1 当前反编译结果

`ClientAvatar.onEnterBattleField` 中存在以下结构：

```python
elif battleMode == const.BattleMode.MODE_TEAM_FB:
    if Globals.uiMgr.fightPrePanel:
        # ...
        if isinstance(Globals.currGameScene, TeamFbScene):
            Globals.currGameScene.clearCombat()
            return None
    return None
    return None
```

另一个分支也有相同问题：

```python
else:
    if battleMode == const.BattleMode.MODE_RUNE_PLOT_FB:
        if Globals.uiMgr.fightPrePanel:
            # ...
            Globals.currGameScene.beginFight()
            return None
    return None
    return None
```

### 3.2 问题说明

这里存在两类冗余：

1. 同一个语句列表中连续生成两个 `return None`。
2. 第一个无条件 `return None` 之后的第二个 `return None` 永远不可达。
3. 部分分支本身已经位于函数的终止路径，可以直接使用函数的隐式 `None` 尾声。
4. 多个基础块共享的函数尾声被重复输出到了不同的结构化分支中。

### 3.3 期望结果

不要求反编译文本与原始源码逐字一致，但输出中不应存在 `return` 后面的不可达语句。该结构可以规范化为：

```python
elif battleMode == const.BattleMode.MODE_TEAM_FB:
    if Globals.uiMgr.fightPrePanel:
        # ...
        if isinstance(Globals.currGameScene, TeamFbScene):
            Globals.currGameScene.clearCombat()

elif battleMode == const.BattleMode.MODE_RUNE_PLOT_FB:
    if Globals.uiMgr.fightPrePanel:
        # ...
        Globals.currGameScene.beginFight()
```

## 4. 稳定最小复现

以下 Python 3.11 源码可以稳定复现 `onEnterBattleField` 中的重复尾声问题：

```python
def battle_like(
    mode,
    enabled,
    is_team_scene,
    begin_fight,
    clear_combat,
):
    if mode == 1:
        if enabled:
            begin_fight()
    elif mode == 2:
        if enabled:
            begin_fight()
            if is_team_scene():
                clear_combat()
    elif mode == 3:
        if enabled:
            begin_fight()
```

当前 decompile3 错误输出：

```python
def battle_like(mode, enabled, is_team_scene, begin_fight, clear_combat):
    if mode == 1:
        if enabled:
            begin_fight()
    elif mode == 2:
        if enabled:
            begin_fight()
            if is_team_scene():
                clear_combat()
                return None
        return None
        return None
    elif mode == 3:
        if enabled:
            begin_fight()
```

复现命令：

```bash
python3 -m py_compile decompyle3_redundant_return_repro.py

/path/to/decompyle3 \
  __pycache__/decompyle3_redundant_return_repro.cpython-311.pyc
```

## 5. 问题二：`realname_info` 生成冗余的显式函数尾声

### 5.1 原始结构

```python
if getattr(Globals, 'IS_SHOW_TIP_YOUNG', False):
    return

if isYoung():
    Globals.IS_SHOW_TIP_YOUNG = True
    import ui.phonebinding.PanelIdentify as PanelIdentify
    PanelIdentify.openPanelIdentifyOnPaperMan()
```

### 5.2 当前反编译结果

```python
if getattr(Globals, 'IS_SHOW_TIP_YOUNG', False):
    return None

if isYoung():
    Globals.IS_SHOW_TIP_YOUNG = True
    import ui.phonebinding.PanelIdentify as PanelIdentify
    PanelIdentify.openPanelIdentifyOnPaperMan()
    return None

return None
```

### 5.3 优化边界

第一个提前返回必须保留，因为它用于跳过后续的 `isYoung()` 判断和相关副作用：

```python
if stop:
    return
action()
```

不能被错误优化为：

```python
if stop:
    pass
action()
```

但 `isYoung()` 分支末尾和整个函数末尾同时生成的 `return None` 只是显式表现 Python 函数的隐式 `None` 尾声，可以进行规范化。

`realname_info` 的简单两分支版本目前不能稳定复现该现象。它可能与函数前面存在局部函数、多处布尔返回及公共 `None` 尾声合并有关。因此建议把真实的 `realname_info` code object 保存为回归 fixture，不要用不准确的简化用例替代。

## 6. 可能的根因

建议重点检查以下方向：

1. 控制流结构化阶段把 CPython 自动插入的隐式 `LOAD_CONST None / RETURN_VALUE` 尾声当成了源码显式返回。
2. 多个基础块共享同一个 `RETURN_VALUE` 尾声时，每个结构化分支都重复生成了 `return None`。
3. 分支终止块和整个函数的公共终止块同时被输出。
4. 异常分支边界修复后，合成的终止节点没有执行去重。
5. 结构化结束后缺少 AST 级不可达代码清理。

## 7. 建议的修复顺序

### 7.1 清除确定不可达的语句

在同一个语句列表中，以下终止语句之后的普通语句不可达：

- `return`
- `raise`
- `break`
- `continue`

例如：

```python
return None
return None
```

必须删除第二个 `return None`。

### 7.2 合并重复的公共函数尾声

如果所有相关路径最终只返回 `None`，而且分支后没有其他可执行代码，可以使用函数的隐式返回，不要在每个分支中重复生成 `return None`。

### 7.3 保留影响控制流的提前返回

以下返回不能删除：

```python
if stop:
    return
action()
```

因为它用于阻止 `action()` 执行。

### 7.4 不跨越特殊控制流边界合并

清理或合并返回时，不应直接跨越：

- `try/finally`
- `with`
- 循环边界
- 异常处理区域
- generator/coroutine 终止边界

## 8. AST 不可达代码检查

可以在测试中递归检查反编译 AST 的所有语句列表：

```python
import ast


TERMINATORS = (
    ast.Return,
    ast.Raise,
    ast.Break,
    ast.Continue,
)


def check_suite(statements, errors):
    for index, statement in enumerate(statements[:-1]):
        if isinstance(statement, TERMINATORS):
            errors.append(
                (
                    statement.lineno,
                    type(statement).__name__,
                    statements[index + 1].lineno,
                )
            )

    for statement in statements:
        for field in ('body', 'orelse', 'finalbody'):
            child = getattr(statement, field, None)
            if isinstance(child, list):
                check_suite(child, errors)

        for handler in getattr(statement, 'handlers', []):
            check_suite(handler.body, errors)


tree = ast.parse(recovered_source)
errors = []
check_suite(tree.body, errors)

assert not errors, f'unreachable statements: {errors}'
```

这个检查可以发现确定的死代码，但不能单独判断某个独立的 `return None` 是否冗余。公共尾声是否可以合并，仍需结合 CFG 和后续代码判断。

## 9. 动态行为测试

对最小复现至少覆盖以下组合：

| `mode` | `enabled` | `team_scene` | 预期调用顺序 |
|---:|---|---|---|
| 1 | `False` | 任意 | 无 |
| 1 | `True` | 任意 | `begin_fight` |
| 2 | `False` | 任意 | 无 |
| 2 | `True` | `False` | `begin_fight` |
| 2 | `True` | `True` | `begin_fight -> clear_combat` |
| 3 | `True` | 任意 | `begin_fight` |

所有测试组合都应返回 `None`，而且清理前后的调用次数、调用顺序、异常行为和副作用必须一致。

动态测试不能只比较返回值，还应该记录：

- 返回值及其精确类型；
- 抛出的异常类型；
- 外部调用顺序和参数；
- 全局变量、对象属性和容器修改。

## 10. 验收标准

修复后应同时满足：

1. 最小复现不再生成连续的 `return None`。
2. 反编译源码不存在终止语句后面的不可达普通语句。
3. 真实 `onEnterBattleField` 不再出现重复的 `return None`。
4. `realname_info` 保留用于跳过后续代码的必要提前返回。
5. 不再为同一个公共函数尾声重复生成多个显式返回。
6. 原始 code object 与反编译后重新编译的代码动态行为一致。
7. 重新编译后的函数数量、限定名、参数和闭包结构不变。
8. 现有 Python 3.11 控制流及异常处理回归测试全部通过。

## 11. 一句话总结

> Python 3.11 多个基础块共享隐式 `None/RETURN_VALUE` 尾声时，decompile3 将同一函数尾声重复物化到多个结构化分支，进而生成冗余甚至不可达的 `return None`；需要在保留真实提前返回的前提下，对公共函数尾声去重，并增加 AST 死代码清理。

## 12. 实施结果

修复已在提交 `202fac0dc628990588165434bbe670a5d526266f` 中完成。实际根因是：

1. `_return()` 仅依据 `linestart` 判断 `LOAD_CONST None / RETURN_VALUE` 是否来自显式源码返回，但 CPython 3.11 复制的隐式函数尾声也可能携带源码行信息。
2. 结构恢复层保留物理返回后缀时，没有再次区分显式返回与复制的隐式尾声，导致公共尾声在嵌套分支中重复物化。

修复通过 Python 3.11 指令位置信息识别复制的隐式尾声。缺少完整位置信息时保守地保留返回，维持 fail-closed 边界；没有增加 opcode 跳过、异常吞没或输出文本后处理。

新增的动态回归测试覆盖调用顺序、调用次数、异常传播、返回值及其精确类型，并确认必要的提前返回不会被删除。验证结果：

- 新增回归测试：`2 passed`
- 相关控制流测试：`187 passed`
- 完整测试：`1031 passed, 6 skipped`
- 真实项目回归：`604/604` 反编译成功，`604/604` 语法验证成功
- `ClientEntities.original.fixed.pyc`：455 个 code object 全部完成反编译，生成源码可重新编译，不可达 suite 边数量为 0

外部 pyc 在验证过程中只读取和反编译，没有执行其中代码。
