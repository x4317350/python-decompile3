# Python 3.11 / Python 2.7 反编译功能差异修复计划

## 1. 文档目的

本文根据以下功能对比报告，固化当前 CPython 3.11 控制流反编译仍存在的三处
语义错误、根因、安全边界、分阶段修复步骤和验收标准：

```text
/Users/ice/Desktop/Custom/WorkCode_github/py3disassembly/docs/python311-vs-python27-source-functional-comparison.md
```

本轮只分析问题并制定计划，不修改反编译器源码。

## 2. 当前基线

- 仓库：`/Users/ice/Desktop/Custom/WorkCode_github/python-decompile3`
- 分支：`master`
- 当前提交：`77ef48520c05ae472cfd4ad6b939ebd8cbae6f81`
- 提交说明：`修复：恢复 Patch 与 helpers 控制流`
- 当前状态：`master` 相对 `origin/master` ahead 1，工作树在生成本文前为干净状态
- 运行时：CPython 3.11.9
- decompyle3：3.9.4.dev0

真实输入：

```text
/Users/ice/Desktop/Custom/WorkCode_github/py3disassembly/dump/dump_marshal_only_use_decompiled/fixed_pyc/SubPatch.original.fixed.pyc
/Users/ice/Desktop/Custom/WorkCode_github/py3disassembly/dump/dump_marshal_only_use_decompiled/fixed_pyc/com.utils.helpers.original.fixed.pyc
```

Python 2.7 参考源码：

```text
/Users/ice/Desktop/Custom/WorkCode_github/py3disassembly/dump/decopile_with_python2.7/SubPatch.py
/Users/ice/Desktop/Custom/WorkCode_github/py3disassembly/dump/decopile_with_python2.7/com.utils.helpers.py
```

外部 PYC 只允许读取、扫描和反编译，不执行其中代码。

## 3. 报告结论复核

报告覆盖 17 个 Python 3.11 反编译文件，其中 12 个存在同名 Python 2.7
参考文件。当前确认 3 处功能差异：

| 文件和函数 | 当前错误 | 功能影响 |
| --- | --- | --- |
| `SubPatch.check_npk_size` | try 后错误生成 `else: return True; return False` | 正常移动端文件也可能返回 `True`，中止 SubPatch 初始化 |
| `SubPatch.update` | NPK 错误处理块脱离组合条件 | 正常资源可能被清理，并提示重启 |
| `helpers.get_loop_act_time` | 有效三元组返回被无条件 `return None` 截断 | 活动有效期内也永远返回 `None` |

使用当前提交重新反编译两个真实 PYC 后，三处错误仍然存在；不是报告引用旧输出造成
的误判。

真实 code object 基线：

| 文件 | 递归 code object | 唯一限定名 |
| --- | ---: | ---: |
| SubPatch | 36 | 36 |
| helpers | 1140 | 1118 |

## 4. 总体根因

三处错误归属于两个控制流根因，而不是三个独立格式问题。

### 4.1 根因 A：try 尾部多值终止出口没有归属到 protected body

目标函数：`SubPatch.check_npk_size`。

真实字节码的关键结构为：

```text
214  判断 fsize >= 2 GiB
224  false → 628
...  超限处理
624  LOAD_CONST True
626  RETURN_VALUE
628  LOAD_CONST False
630  RETURN_VALUE
632  PUSH_EXC_INFO             # 外层 bare except
...
638  LOAD_CONST False
640  RETURN_VALUE
```

外层 exception table 的最后一个 protected fragment 在 offset 624 之前结束。原因是
`LOAD_CONST True/False` 和 `RETURN_VALUE` 不会抛出业务异常，CPython 3.11 不需要把
这些终止块放进保护区。

当前 `_try_except()` 以最后一个 protected fragment 的 end 作为 `body_end`。当
handler 前没有普通 `JUMP_FORWARD` 时，它把 `[body_end, handler)` 一律作为
`try.orelse` 捕获：

```python
try:
    ...
    if fsize >= limit:
        return True
except:
    return False
else:
    return True
    return False
```

这同时产生两个错误：

1. offset 624 的 `return True` 已被条件分支使用，又被 orelse 重复捕获；
2. offset 628 的 `return False` 本应是 try body 的另一个正常终止出口，却被错误
   归入源码中不存在的 else。

当前 `_protected_terminal_none_return_end()` 只处理精确的
`LOAD_CONST None / RETURN_VALUE`，不能处理：

- `True` / `False` / 其他常量返回；
- 同一 try body 的多个终止出口；
- 由条件分支分别到达的终止块集合。

因此该问题需要“终止出口前沿”级别的 CFG 所有权分析，不能再增加一个
`return False` 特例。

### 4.2 根因 B：条件决策图扩展不是原子闭包

目标函数：`SubPatch.update` 和 `helpers.get_loop_act_time`。

当前 `_bounded_condition_plan()` 为修复多组 AND/OR 条件，已经从单次扩展改成迭代
扩展。为防止吸收条件 suite 中的嵌套 `if`，当前又增加了两项限制：

1. 待扩展 decision block 至少有两个普通前驱；
2. 待扩展 decision 的源码位置必须落在已有条件源码范围内。

这两个限制能够保护 `unittest.mock._patch.__enter__` 一类：

```python
if spec is not None or spec_set is not None:
    if original is DEFAULT:
        ...
```

但它们会拒绝两种合法条件 continuation。

#### 4.2.1 跨行 OR continuation 被源码位置限制拒绝

`SubPatch.update` 的真实字节码：

```text
3804  exists(res)
3872  false → 3886
3874  res_size > 0
3884  true → 3968                 # 错误处理 body

3886  exists(script)              # 第二组 OR，源码在下一行
3954  false → 4134                # 正常 join
3956  script_size > 0
3966  false → 4134

3968  crash report / clean / message / return
4134  正常 continuation
```

初始条件计划只拥有第一组节点，端点为 3886 和 3968。第二组节点在下一源码行，
其 `co_positions()` 不落在第一组的源码范围内，因此迭代扩展提前停止。

后续结构恢复先把第二组恢复为嵌套 if，又把 offset 3968 的错误处理块作为外层顺序
代码再次捕获，最终产生无条件清理。

#### 4.2.2 chained comparison 后的单前驱 conjunct 被拒绝

`get_loop_act_time` 的关键条件为：

```python
now_begin_time <= now_time < now_end_time and now_end_time <= end_time
```

CPython 3.11 为 chained comparison 生成 `SWAP/COPY/POP_TOP/JUMP_FORWARD` cleanup：

```text
226..250  now_begin_time <= now_time < now_end_time
252       JUMP_FORWARD → 258
254       POP_TOP
256       JUMP_FORWARD → 270
258..268  now_end_time <= end_time
270       return None
274       return tuple
```

`_chained_condition_plan()` 能正确恢复第一段 chained comparison，但 offset 258 的
最后一个 conjunct 只有一个直接普通前驱：offset 252 的透明 `JUMP_FORWARD` bridge。
当前“至少两个前驱”的限制拒绝扩展，条件因此被截断。

结构化结果变成：

```python
if now_begin_time <= now_time < now_end_time:
    if now_end_time > end_time:
        return None
return None
return (now_begin_time, now_end_time, period)
```

### 4.3 最近修复暴露出的回归机制

提交 `77ef4852` 的迭代条件闭包解决了 Patch 中三组汇合 OR 的问题，也防止了
`unittest.mock` 嵌套 suite 被吸收。但当前实现允许候选扩展暂时保留 2 到 3 个端点，
随后再尝试继续扩展；为阻止错误吸收，又依赖粗粒度的源码范围和前驱数量限制。

这形成了两个相反风险：

- 允许临时 3 个端点，可能把 suite 中的多个独立分支吸收到条件；
- 用源码范围和两个前驱阻止吸收，又会拒绝跨行条件和透明 bridge。

可行方向是恢复“每次提交扩展都必须原子地闭合为两个端点”的不变量，再迭代执行
多次，而不是提交一个三端点的半成品条件图。

## 5. 必须保持的安全边界

1. 不跳过 `POP_JUMP_*`、`JUMP_FORWARD`、`SWAP`、`COPY` 或 exception protocol。
2. 不捕获最终解析异常后输出 `pass`、空函数或失败占位。
3. 不根据文件名、函数名、源码行号或固定 bytecode offset 特判。
4. 不手工修改反编译结果。
5. 不把所有 handler 前的返回块无条件归入 try body。
6. 不简单删除条件扩展的源码位置和前驱保护条件。
7. 无法证明端点、前驱来源或异常所有权时继续 fail-closed。
8. 动态测试必须比较返回值、返回类型、异常和副作用顺序，不能只检查
   `ast.parse()`。
9. 外部真实 PYC 只反编译，不执行。

## 6. 分阶段修复计划

### 阶段 0：冻结基线和修复前输出

目标：保证所有后续差异都可追踪。

步骤：

1. 检查 `git status` 和 `git rev-parse HEAD`，要求基线为 `77ef4852`；若存在其他
   用户修改，先停止并确认范围。
2. 保存以下修复前输出，不覆盖原文件：
   - `SubPatch.original.decompyle3.before-77ef4852.py`
   - `com.utils.helpers.original.decompyle3.before-77ef4852.py`
   - 当前 17 文件输出目录的 SHA-256 清单。
3. 记录真实 PYC 递归 code object 数、限定名、参数元数据、freevars/cellvars。
4. 保存三个目标函数的 normalized token、basic block、普通边、异常边、exception
   table fragment 和 `co_positions()` 调试快照。
5. 确认当前错误文本和本计划第 3 节一致。

阶段完成标准：基线、快照、哈希和复现命令全部可重复。

### 阶段 1：先增加失败的最小动态回归

建议新增：

```text
pytest/test_source_functional_differences311.py
```

最小样例一：try 尾部两个布尔返回出口。

```python
def terminal_try(size, fail):
    try:
        if fail:
            raise RuntimeError()
        if size >= 10:
            return True
        return False
    except:
        return False
```

断言：

- `size < 10` 返回 `False`，类型为 `bool`；
- `size >= 10` 返回 `True`；
- try 内抛异常返回 `False`；
- 反编译 AST 不包含连续不可达的两个 return；
- 不能重复执行任何 size/action 探针。

最小样例二：try 内跨行两组 OR。

```python
def missing_npk(res_exists, res_size, script_exists, script_size, clean, tail):
    try:
        if res_exists() == False and res_size > 0 or \
                script_exists() == False and script_size > 0:
            clean()
            return
    except Exception:
        pass
    tail()
```

断言：

- res/script 都存在时只调用 `tail()`；
- 只缺 res 且 `res_size > 0` 时只调用一次 `clean()`；
- 只缺 script 且 `script_size > 0` 时只调用一次 `clean()`；
- 缺文件但对应 size 为 0 时仍调用 `tail()`；
- 比较全部布尔/size 组合，并比较 `exists()` 调用顺序和次数；
- `clean()`、异常 handler 和 `tail()` 都不得重复调用。

最小样例三：chained comparison 加最后一个 conjunct。

```python
def active_window(begin, now, cycle_end, total_end):
    if not (begin <= now < cycle_end and cycle_end <= total_end):
        return None
    return (begin, cycle_end)
```

断言：

- 活动前、冻结期、周期结束超过总结束时间均返回 `None`；
- 有效周期返回 tuple；
- 使用带比较副作用的 mock 值确认 chained comparison 的短路顺序一致；
- tuple return 在 AST 中可达。

负向保护样例：

1. `unittest.mock` 形态的“条件后嵌套 if”不能被吸收；
2. Patch 三组 OR 必须全部恢复且保持调用顺序；
3. 真正的 `try/except/else` 必须保留；
4. else 中调用函数并抛异常时，异常不能错误落入 try 的 except；
5. 隐式 `return None` 不得重新打印成大量显式 return。

阶段完成标准：三组新正向测试在修复前稳定失败，已有负向保护测试通过。

### 阶段 2：恢复 try 的终止出口前沿所有权

主要位置：

```text
decompyle3/controlflow/exception_structures.py
```

建议将 `_protected_terminal_none_return_end()` 泛化为类似：

```text
_protected_terminal_exit_frontier(...)
```

算法要求：

1. 从最后一个 protected fragment 的普通出口出发，收集 handler 之前的 terminal
   frontier basic blocks。
2. 只接受终结于 `RETURN_VALUE` 的闭合块；第一阶段不要顺带支持 raise/break/continue。
3. frontier block 不得有普通后继。
4. 每个 frontier block 的所有普通前驱必须来自：
   - 当前 try 的 protected blocks；或
   - 已证明属于同一 frontier 的透明块。
5. 至少存在一个来自 protected blocks 的真实入口。
6. handler protocol、外部普通入口、循环回边或跨区跳转一律拒绝。
7. frontier 中只允许不会改变异常归属的终止协议，例如常量加载和 return；如果包含
   `CALL`、属性读取等可能抛异常的表达式，则不能把它从真实 else 移入 try body。
8. 支持同一条件的多个终止块，例如 `return True` 和 `return False`，并一次性把整个
   frontier 交给 protected body 捕获。
9. frontier 已归入 body 时必须令 `orelse=[]`，不能再次从同一区间捕获。

特别注意：

```python
try:
    work()
except Exception:
    recover()
else:
    return callback()
```

`callback()` 抛出的异常不应被 except 捕获。由于该调用位于 exception table 保护区
之外，修复不能为了消除 else 而把它移入 try body。负向测试必须验证这一点。

阶段完成标准：`terminal_try` 动态语义通过，真实 `check_npk_size` 不再产生重复
return 或错误 try-else，真正 try-else 的异常边界不变。

### 阶段 3：恢复条件扩展的原子二端点不变量

主要位置：

```text
decompyle3/controlflow/structures.py::_bounded_condition_plan
```

修改方向：

1. 保留迭代闭包和 work limit，以支持三组及更多 AND/OR。
2. 每次 `path_to_shared_endpoint()` 返回后，先对候选子图执行 endpoint coalesce 和
   reduction。
3. 只有候选扩展在本次事务内已经闭合为**恰好两个端点**时才提交；不再把临时
   3 端点图写回 `nodes/endpoints` 后等待下一轮补救。
4. 若一轮扩展不能原子闭合，保持原计划不变并尝试其他方向；都失败则 fail-closed。
5. 新节点的普通入口必须能追溯到已有 condition nodes 或已证明透明的条件 cleanup
   bridge；异常入口仍然禁止。
6. 用候选图的叶端点数量和来源证明 suite 边界，源码位置只作为辅助证据，不作为
   跨行条件的唯一否决条件。

预期效果：

- SubPatch 第二组 OR 可一次扩展成“错误 body / 正常 join”两个端点；
- Patch 三组 OR 可通过多次二端点事务连续扩展；
- `unittest.mock` 的 suite 分支会产生三个或更多未闭合叶端点，因此不会被提交。

阶段完成标准：`missing_npk` 全组合动态测试通过，`unittest.mock` 和 Patch 三组 OR
保护测试同时通过。

### 阶段 4：证明 chained comparison 的透明 bridge 来源

主要位置：

```text
decompyle3/controlflow/structures.py::_chained_condition_plan
decompyle3/controlflow/structures.py::_bounded_condition_plan
```

步骤：

1. 为 condition predecessor provenance 增加只读回溯：允许穿过无副作用、单后继的
   `JUMP_FORWARD` / chained cleanup bridge。
2. bridge 的前驱最终必须唯一回溯到已有 condition node，不能接受任意单前驱
   decision block。
3. 单前驱候选只有在以下条件全部成立时才能扩展：
   - 前驱是已证明的透明 chained-comparison bridge；
   - predicate 的源码位置与当前完整条件相同或被其范围覆盖；
   - 扩展后原子闭合为两个端点；
   - 没有异常入口和外部普通入口。
4. 不直接删除“至少两个前驱”保护；将其改为：多前驱使用普通条件来源证明，单前驱
   必须满足更严格的透明 bridge 证明。

阶段完成标准：`active_window` 返回 tuple 的有效路径恢复，所有比较副作用和短路
顺序与原函数一致，独立的后续 if 不被吸收。

### 阶段 5：真实 SubPatch/helpers 验证

重新反编译：

```bash
.venv311/bin/decompyle3 --verify syntax \
  --output /private/tmp/SubPatch.after.py \
  /Users/ice/Desktop/Custom/WorkCode_github/py3disassembly/dump/dump_marshal_only_use_decompiled/fixed_pyc/SubPatch.original.fixed.pyc

.venv311/bin/decompyle3 --verify syntax \
  --output /private/tmp/helpers.after.py \
  /Users/ice/Desktop/Custom/WorkCode_github/py3disassembly/dump/dump_marshal_only_use_decompiled/fixed_pyc/com.utils.helpers.original.fixed.pyc
```

静态验收：

1. 两份输出均可 `ast.parse()` 和 `compile()`。
2. SubPatch 保持 36/36 code object、限定名无缺失/新增。
3. helpers 保持 1140/1140 code object、1118 个唯一限定名。
4. 参数数量、posonly/kwonly、freevars/cellvars 与 PYC 一致。
5. 不出现失败占位、跳过函数、`Unsupported phase-3 opcode` 或 stack underflow。
6. `check_npk_size` 正常路径明确返回 `False`，超限路径返回 `True`。
7. `SubPatch.update` 只有“文件缺失且对应 size > 0”才执行错误处理。
8. `get_loop_act_time` 的 tuple return 可达。

真实 PYC 不执行。业务动态语义由等价最小样例和 mock 环境覆盖。

### 阶段 6：17 文件三方差异审计

1. 修复前快照、修复后输出、Python 2.7 参考源码做三方 diff。
2. 对 12 个可直接比较文件重新执行：
   - 定义数量和名称；
   - 函数签名；
   - global/nonlocal；
   - 调用集合；
   - 不可达语句扫描；
   - 复杂条件真值表和副作用顺序。
3. 期望原报告的 3 处确认差异全部清零，不新增确认差异。
4. 5 个无 Python 2.7 同名参考的文件仍只做语法、code object 和结构检查，不作
   无证据的功能等价结论。
5. 继续重新反编译并比较 ClientEntities、Patch 等已修复大型样本，防止条件边界
   或 return 清理回归。

阶段完成标准：17/17 可编译；12 个可比文件不再存在已确认功能差异；所有新增差异
均经过人工控制流审计。

### 阶段 7：完整测试和真实世界回归

至少运行：

```bash
.venv311/bin/pytest -q pytest/test_source_functional_differences311.py

.venv311/bin/pytest -q \
  pytest/test_patch_helpers_regression311.py \
  pytest/test_controlflow311.py \
  pytest/test_controlflow311_short_circuit_values.py \
  pytest/test_exception_boundary_semantics311.py \
  pytest/test_return_none_cleanup311.py \
  pytest/test_terminal_cleanup_regression311.py \
  pytest/test_exceptiontable311.py \
  pytest/test_exception_cleanup311.py

.venv311/bin/python test/bytecode_3.11/run_realworld_regression.py
.venv311/bin/pytest -q
```

最低标准：

- 当前完整测试基线 `1045 passed, 6 skipped` 不得减少；
- 新增测试全部通过；
- 真实世界回归保持 604/604、fail-closed 0、未包装崩溃 0；
- `flake8` 和 `git diff --check` 通过；
- 性能不得出现 helpers 模块级 post-dominator 扫描退化。

### 阶段 8：文档、产物和提交

1. 更新本计划为实施报告，记录最终根因、实现方法、测试结果和残余风险。
2. 更新 Python 3.11 真实世界归档摘要。
3. 更新外部功能对比报告，保留修复前版本或哈希。
4. 只提交反编译器源码、自动化测试和仓库内文档；不提交 `/private/tmp` 产物。
5. 提交前复核 staged diff，确认没有手工修改反编译输出规避问题。
6. 创建独立 Git 提交并报告完整 commit hash；未经要求不 push。

## 7. 预计修改文件

源码：

```text
decompyle3/controlflow/exception_structures.py
decompyle3/controlflow/structures.py
```

测试：

```text
pytest/test_source_functional_differences311.py
```

可能更新：

```text
PYTHON_311_SOURCE_FUNCTIONAL_DIFFERENCE_FIX_PLAN.md
PYTHON_311_REALWORLD_REGRESSION.md
test/bytecode_3.11/realworld_regression311.json
```

若实现过程中需要修改其他解析器文件，必须先在实施报告中说明原因，不能为了绕过
CFG 所有权问题而把 opcode 在 phase 3 静默忽略。

## 8. 最终验收矩阵

| 场景 | 预期 |
| --- | --- |
| Windows `check_npk_size` | `False` |
| 移动端文件不存在、0 或小于 2 GiB | `False` |
| 移动端文件大于等于 2 GiB | `True`，提示/清理调用一次 |
| `check_npk_size` 内部异常 | `False` |
| res/script 均存在 | 不清理，继续 update |
| 仅缺 res 且 `res_size > 0` | 清理和重启提示各一次 |
| 仅缺 script 且 `script_size > 0` | 清理和重启提示各一次 |
| 缺文件但对应 size 为 0 | 不清理 |
| 活动有效窗口 | 返回 `(now_begin_time, now_end_time, period)` |
| 活动前/冻结期/超过总结束时间 | `None` |
| Patch 三组 OR | 真值及短路调用顺序不变 |
| 条件 suite 中嵌套 if | 不被吸收到条件表达式 |
| 真正 try-else 的 else 调用抛异常 | 不被 try 的 except 捕获 |
| 隐式 None 尾声 | 不打印多余 return |

## 9. 风险评估

### 高风险

- 扩大 terminal frontier 所有权可能错误改变 try/else 的异常捕获范围。
- 放宽条件 continuation 可能再次吸收独立嵌套 if。

控制措施：只允许不可抛异常的终止协议；每次条件扩展必须原子闭合为两个端点；
保留真实 try-else 和 `unittest.mock` 负向测试。

### 中风险

- chained comparison 的透明 cleanup block 可能与普通无条件跳转混淆。
- co_positions 在部分外部 code object 中可能缺失或不完整。

控制措施：源码位置只作辅助；核心证明依赖 CFG 前驱来源、单后继透明 bridge、端点
闭包和异常边。位置缺失且 CFG 证据不足时 fail-closed。

### 低风险

- 输出可能由嵌套 if 改写为等价布尔表达式，或由 `return None` 改为 `return`。

这类差异只有在动态返回类型、副作用顺序和异常边界一致时才接受。

## 10. 完成定义

只有同时满足以下条件才可宣布修复完成：

1. 三个最小样例动态语义全部一致。
2. 三个真实函数不再存在报告确认的功能差异。
3. 没有通过跳过 opcode、捕获错误、占位函数或手工输出修改规避。
4. SubPatch/helpers code object 和限定名完整。
5. 17 文件重新编译通过，12 个可比文件不新增功能差异。
6. Patch、ClientEntities、`unittest.mock` 和真实世界语料无回归。
7. 完整 pytest、相关测试、真实世界回归和风格检查全部通过。
8. 修复说明、测试结果和 Git commit 完整记录。

## 11. 实施结果（2026-08-06）

### 11.1 最终根因

本轮确认三处功能差异由两类 CFG 归属错误引起：

1. `check_npk_size` 的 protected fragments 在最后两个
   `LOAD_CONST` / `RETURN_VALUE` 终止块之前结束。旧实现只认单个
   `return None`，于是把 `return True`、`return False` 错当成 try 的正常完成
   suite，生成了不存在的 `try/else`。
2. `update` 的多组 OR 条件需要跨 source line 和透明
   `JUMP_FORWARD` 继续闭包；`get_loop_act_time` 则先被链式比较专用路径截获，
   后续 `and` 比较未加入同一决策图。两者最终都把条件 continuation 当成
   suite 或公共尾声。

### 11.2 实现

- `exception_structures.py` 将单一 None-return 特判收紧并推广为“常量返回终止
  前沿”证明。只有物理间隙完全由一组 `LOAD_CONST` / `RETURN_VALUE` 组成、
  每个块无正常后继、无异常前驱，且所有正常前驱来自 protected fragments 或
  已证明的前沿时，才把它们归入 try body。含调用或其他可能抛异常指令的真正
  try-else 不会被吸收。
- `structures.py` 的条件闭包现在把已拥有 decision 的起始块、跳转块以及
  单后继透明跳转桥纳入前驱所有权证明。跨行 continuation 还要求同一异常表
  目标和完整源码位置；普通嵌套 if 的单前驱分支仍拒绝扩展。三组 OR 可暂时
  保留第三叶节点，但必须在有界迭代中闭合回两个最终端点。
- 链式比较路径增加独立的透明桥证明：后续谓词只能从链式比较的一个出口进入，
  且另一出口必须回到已知相反端点，才合成为 `chain and tail` 或等价表达式。
- 没有跳过 opcode、捕获解析异常后输出占位函数，也没有修改任何反编译结果来
  规避解析器问题。

### 11.3 新增自动化测试

新增 `pytest/test_source_functional_differences311.py`，覆盖：

- try 尾部 `True` / `False` 两个常量返回出口；
- try 内两组缺失 NPK 的 AND/OR 短路条件；
- 链式比较后接最终 conjunct，并到达 tuple 返回；
- 真正 try-else 中 callback 抛异常的负向边界；
- 条件 suite 中普通嵌套 if 不得被吸收的负向边界。

测试会编译最小源码、反编译、再次 `ast.parse()`/`compile()`，然后执行原函数和
重建函数，逐项比较返回值及类型、异常类型、调用次数和调用顺序。

### 11.4 真实样本和语料结果

- `SubPatch.original.fixed.pyc`：36/36 个 code object，语法验证通过。
- `com.utils.helpers.original.fixed.pyc`：1140/1140 个 code object，语法验证
  通过。
- `ClientEntities.original.fixed.pyc`：455/455 个 code object，语法验证通过。
- 17 个 fixed pyc 全部反编译并重新编译成功；原始与重建 code object 总数均为
  2465，逐文件无数量差异。
- `check_npk_size` 不再生成错误 try-else；`update` 的清理块重新受组合条件控制；
  `get_loop_act_time` 的有效 tuple 返回可达。
- 17 文件修复前后共有 6 个输出发生变化。逐项对照 Python 2.7 参考源码后，
  除目标三处外，其余控制流变化恢复了原先漏掉的 `else`/`elif`，或属于
  `return` 与 `return None`、集合显示顺序等语义等价差异。
- 外部 pyc 全程只读取、扫描和反编译，从未执行其中代码。

修复前快照已保留为：

```text
SubPatch.original.decompyle3.before-77ef4852.py
com.utils.helpers.original.decompyle3.before-77ef4852.py
network.rpcentity.ClientEntities.original.decompyle3.before-77ef4852-functional-fix.py
```

三份最终输出已写回原有输出位置并再次编译验证，SHA-256 分别为：

```text
SubPatch.original.py                                      6a94a5aa10197800268a5fbe28fb8c8fa19d5d3b3d6dbb58a173dfe687dafaf3
com.utils.helpers.original.py                             9c346eee4f033f83db7688e197975427e3d2a8c4d779569bed8b73723dcdd5c5
network.rpcentity.ClientEntities.original.decompyle3.py   3bacd16ea187af149a9e29d907f06074e355fa6987a2a80e491ef92399e044be
```

### 11.5 测试结果

```text
pytest/test_source_functional_differences311.py                  5 passed
相关控制流、异常表、terminal cleanup 回归                       211 passed
17 文件真实 fixed pyc 门禁                                      17/17 passed
真实样本 code object 数量                                       2465/2465
Python 3.11 完整 pytest（归档更新后最终复跑）                    1050 passed, 6 skipped
真实世界归档                                                    604/604, fail-closed 0
flake8（本轮修改文件）                                          passed
git diff --check                                                passed
```

新增测试使完整测试总数比计划基线增加 5；真实世界归档的输入摘要由项目生成器
重新计算，汇总指标、行为探针和 fail-closed 计数没有退化。归档更新后的完整
pytest 最终复跑为 1050 passed、6 skipped。
