# CPython 3.11 Patch / helpers 控制流反编译问题与修复记录

## 1. 文档目的

本文固化以下两个真实文件在 CPython 3.11 控制流反编译中的失败、根因、
安全边界、修复方法和回归结果：

- `Patch.original.fixed.pyc`
- `com.utils.helpers.original.fixed.pyc`

修复基线为：

- 分支：`master`
- 提交：`aafc753373bfdad71a6911b3489819e289042b1e`
- decompyle3：`3.9.4.dev0`
- 运行时：CPython `3.11.9`

本文与修复代码、自动化测试位于同一个 Git 提交。最终提交哈希记录在任务交付
报告中，避免在提交内容中写入无法稳定自引用的哈希。

## 2. 输入和参考文件

真实 PYC：

```text
/Users/ice/Desktop/Custom/WorkCode_github/py3disassembly/dump/dump_marshal_only_use_decompiled/fixed_pyc/Patch.original.fixed.pyc
/Users/ice/Desktop/Custom/WorkCode_github/py3disassembly/dump/dump_marshal_only_use_decompiled/fixed_pyc/com.utils.helpers.original.fixed.pyc
```

Python 2.7 参考源码：

```text
/Users/ice/Desktop/Custom/WorkCode_github/py3disassembly/dump/decopile_with_python2.7/Patch.py
/Users/ice/Desktop/Custom/WorkCode_github/py3disassembly/dump/decopile_with_python2.7/com.utils.helpers.py
```

问题分析来源：

```text
/Users/ice/Desktop/Custom/WorkCode_github/py3disassembly/docs/decompyle3-patch-helpers-failure-fix.md
```

真实 PYC 在整个过程中只被读取、解析和反编译，从未执行其中代码。

## 3. 安全边界

本次修复必须保持以下约束：

1. 不跳过无法识别的 opcode。
2. 不捕获解析错误后生成 `pass`、空函数或占位函数。
3. 不对输出源码做手工修补。
4. 不使用 wrapper、monkeypatch 或仅针对文件名、函数名、offset 的特判。
5. CFG 证据不足时继续 fail-closed。
6. 生成源码必须能够 `ast.parse()` 和 `compile()`。
7. 最小样例必须执行原函数和反编译后函数，比较返回值、异常和副作用顺序。
8. 真实 PYC 必须完整保留递归 code object，不能静默丢函数。

## 4. 问题一：Patch 正常完成出口和复合条件恢复失败

### 4.1 基线症状

基线反编译在 `Patch.init` 失败：

```text
Cannot prove try normal-completion ownership ('init', offset 1946)
```

在临时放宽该失败后，真实输出还暴露出以下语义错误：

- `init` 中 `doPatching` 的 `else` 边界丢失；
- `setImageFullScreen` 的 `test and left or right` 被拆成语句级空分支；
- `tag_xg1` 中参与条件赋值的 `STORE_GLOBAL` 没有恢复 `global` 声明；
- `createForceUpdatePanel2` 的内外失败分支被压平，可能重复通知；
- 三组汇合的 OR 条件只恢复第一层，可能重复检查或重复清理；
- 终止分支的隐式 `return None` 一度被打印成大量额外 `return`。

### 4.2 根因

这是同一个 CFG 所有权问题在不同结构上的表现：

1. CPython 3.11 exception table 只保护可能抛出异常的指令。函数尾部的
   `LOAD_CONST None / RETURN_VALUE` 不会抛异常，因此可能紧跟在最后一个
   protected fragment 后，却不在 exception table 的区间内。
2. 原 `_normal_completion_entry()` 允许路径离开 protected fragment 后重新进入
   另一个 fragment，导致 fragment 之间的非抛异常间隙被误认为 try 的正常完成
   suite。
3. 条件端点扩展只处理一个汇合层，无法恢复三组连续的 AND/OR 决策；但若仅循环
   扩展，又会把条件之后的嵌套 `if` 误吸收到条件表达式。
4. 短路值表达式没有 `JUMP_FORWARD` 形式的直接 merge 时，语句恢复会先消费首个
   决策，丢失表达式整体。
5. 条件赋值恢复会消费多个 store，但作用域记录只看后续直接 dispatch 的 store，
   因此被表达式恢复消费的 `STORE_GLOBAL` / `STORE_DEREF` 会丢失声明。
6. 终止区间分析把离开局部区间的 exception edge 当成普通 continuation，导致
   try 内部本应闭合的 if/else 无法恢复；反向放宽时若不区分显式 return 与隐式
   尾声，又会制造额外 return。

### 4.3 修复

`decompyle3/controlflow/exception_structures.py`：

- `_normal_completion_entry()` 遇到重新进入 protected block 的路径立即停止；
- 新增 `_protected_terminal_none_return_end()`，只在以下证据同时成立时把终止
  `None` 返回重新归属给 try body：
  - 精确匹配 `LOAD_CONST None / RETURN_VALUE`；
  - return block 无普通后继；
  - 所有普通前驱都来自同一组 protected fragments；
  - 不存在外部普通入口。

`decompyle3/controlflow/structures.py`：

- `_terminal_interval_exit_kinds()` 区分普通边和异常边；异常传播到外层 handler
  不再被当作局部正常出口；
- `_bounded_condition_plan()` 对汇合条件做有上限的迭代闭包；每个扩展节点必须：
  - 只由当前条件图拥有的节点进入；
  - 仍能到达已有兄弟端点；
  - 具有多个条件前驱；
  - 源码位置属于原复合条件范围；
- 源码位置约束可区分“多行括号条件的下一组决策”和“条件 suite 中的第一个
  嵌套 if”，避免吸收 `unittest.mock._patch.__enter__` 的后续分支；
- `_try_inline_if_expression()` 在无直接 `JUMP_FORWARD` 时寻找闭合的短路 merge，
  一次恢复完整值表达式；
- `_try_assignment_expression()` 对被消费的 store 同步记录 global/nonlocal；
- 终止 if/else 只在源码位置和 CFG 同时证明是控制关键的显式 return 时保留
  `return None`，编译器复制的隐式尾声不打印。

## 5. 问题二：helpers 模块级性能和嵌套异常结构失败

### 5.1 基线症状

`com.utils.helpers.original.fixed.pyc` 在基线下超过 15 秒仍无输出。

移除性能阻塞后，`_is_below_win10` 的外层 try handler 仍可能泄漏到 phase 3，
表现为裸 `PUSH_EXC_INFO` 或无法恢复以下逻辑结构：

```python
try:
    try:
        import primary
    except ImportError:
        import fallback
    key = open_key()
    try:
        major = query(key)
    finally:
        close(key)
    return major < 10
except Exception:
    try:
        return system_fallback() < 10
    except Exception:
        return False
```

### 5.2 根因

1. `_try_return_expression()` 是函数返回值专用分析，但模块 code object 也进入了
   该路径。helpers 模块巨大，模块中的大量候选位置反复触发完整 post-dominator
   分析，形成无意义的高成本扫描。
2. `_is_below_win10` 的逻辑外层 try 被内层 import try 和 finally 切成多个
   exception-table fragments。内层 try 恢复后，continuation 位于外层 fragment
   内部，而旧逻辑只识别 `region.end == continuation`，因此无法证明外层 handler
   对该 continuation 的所有权。
3. 若只按 handler target 合并 fragments，正在捕获的外层 try 会再次被包装，产生
   双重 handler 或错误嵌套。

### 5.3 修复

`decompyle3/controlflow/structures.py`：

- `_try_return_expression()` 对 `co_name == "<module>"` 做 O(1) 返回。模块不可能有
  源码级 return expression，因此这是结构域约束，不是忽略 opcode。

`decompyle3/controlflow/exception_structures.py`：

- 新增 `_enclosing_fragmented_handler()`，只在以下条件下恢复外层 handler：
  - continuation 位于某个外层 protected fragment 内；
  - depth、lasti 和唯一 handler target 一致；
  - handler 以 `PUSH_EXC_INFO` 开始，并可证明是匹配 handler 或 bare handler；
  - target 尚未被当前 capture 抑制；
  - fragment start 不在正在捕获的 suppressed starts 中；
- 内层 try 完成后，先捕获外层剩余 protected fragments，再解析唯一外层 handlers，
  组合成嵌套 `ast.Try`；
- 所有多候选、目标不唯一或协议证据不完整的情况仍返回 `None`，交由现有
  fail-closed 路径处理。

## 6. 自动化回归测试

新增：

```text
pytest/test_patch_helpers_regression311.py
```

测试覆盖 8 组最小样例：

1. try 内复合条件提前 return 与正常 continuation；
2. import fallback + finally + 外层 handler + handler 内 handler；
3. 以 try/except 结束的终止 if/else，不得丢 else；
4. `test and left or right` 的值和调用次数；
5. 被条件表达式消费的 `STORE_GLOBAL` 仍恢复 global 语义；
6. 内外失败分支不会重复通知；
7. 三组汇合 AND/OR 的短路顺序和调用次数；
8. 汇合条件后的嵌套 if 不被吸收到条件中；模块 return probe 保持 O(1)。

动态测试不只比较文本。每个相关样例都会执行原始编译函数和反编译后重新编译的
函数，并比较：

- 返回值及其类型；
- 抛出的异常类型和消息；
- 条件函数调用次数；
- 短路求值顺序；
- cleanup / notification 调用次数；
- finally 的 close 次数；
- global 变量最终值和重编译后的 `STORE_GLOBAL`。

## 7. 真实文件验证

最终命令：

```bash
.venv311/bin/decompyle3 --verify syntax \
  --output /private/tmp/Patch.patch-helpers-final.py \
  /Users/ice/Desktop/Custom/WorkCode_github/py3disassembly/dump/dump_marshal_only_use_decompiled/fixed_pyc/Patch.original.fixed.pyc

.venv311/bin/decompyle3 --verify syntax \
  --output /private/tmp/com.utils.helpers.patch-helpers-final.py \
  /Users/ice/Desktop/Custom/WorkCode_github/py3disassembly/dump/dump_marshal_only_use_decompiled/fixed_pyc/com.utils.helpers.original.fixed.pyc
```

结果：

| 文件 | 耗时 | 原 code object | 重建 code object | 限定名缺失 | 限定名新增 |
| --- | ---: | ---: | ---: | ---: | ---: |
| Patch | 1.15 s | 133 | 133 | 0 | 0 |
| helpers | 4.01 s | 1140 | 1140 | 0 | 0 |

两份输出都通过 `--verify syntax`、`ast.parse()` 和 `compile()`，且没有失败占位函数、
`Unsupported phase-3 opcode` 或 stack underflow。

## 8. ClientEntities 防回归对比

修复开始前保存了：

```text
/Users/ice/Desktop/Custom/WorkCode_github/py3disassembly/dump/testcfg/network.rpcentity.ClientEntities.original.decompyle3.before-aafc7533.py
```

其 SHA-256 为：

```text
edf2b78e46ebd5d98196b0b888f26dec54f21f894082760cc2af0ccf0b4085eb
```

最终重新反编译完整 ClientEntities PYC 成功。与修复前快照的 diff 为 39 行，只包含：

- 提前 return 的语义等价 `if` → `elif` 结构化；
- 删除无意义的 `try/else: return None`；
- `return None` → `return`；
- 集合字面量元素顺序变化。

没有新增调用、丢失 else、重复副作用或额外 return。正确的 Python 2.7 参考源码也
用于核对相关分支。

开发中曾出现 435 行 diff 和大量额外 return。该版本没有被保留：原因是终止分支
错误地把所有局部 `None` sink 当成显式 return。最终实现增加了源码位置与 CFG 的
双重证明，并新增“隐式 return 不得打印”的测试。

开发中还曾使真实世界回归出现 602/604。该版本同样没有进入最终结果：

- 复杂列表推导式使项目源码自反编译失败；
- 仅按多前驱扩展条件，使 `unittest.mock.__enter__` 的嵌套 if 被吸收。

最终均已修正，真实世界归档恢复为：

```text
输入 604，反编译成功 604，语法成功 604，fail-closed 0，未包装崩溃 0
```

## 9. 最终测试结果

```text
pytest/test_patch_helpers_regression311.py: 8 passed
相关 CPython 3.11 控制流回归: 210 passed
完整 pytest: 1045 passed, 6 skipped
flake8（3 个修改/新增 Python 文件）: passed
git diff --check: passed
```

真实世界归档同步更新：

```text
PYTHON_311_REALWORLD_REGRESSION.md
test/bytecode_3.11/realworld_regression311.json
```

## 10. 修改文件

源码：

```text
decompyle3/controlflow/structures.py
decompyle3/controlflow/exception_structures.py
```

测试和报告：

```text
pytest/test_patch_helpers_regression311.py
PYTHON_311_PATCH_HELPERS_FAILURE_FIX.md
PYTHON_311_REALWORLD_REGRESSION.md
test/bytecode_3.11/realworld_regression311.json
```

本次没有修改任何反编译输出文件来规避问题。
