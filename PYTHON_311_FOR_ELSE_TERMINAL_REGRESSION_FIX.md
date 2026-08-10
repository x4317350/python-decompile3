# Python 3.11 `for ... else` 终止边界与重复 `while` 修复报告

## 1. 目标与安全边界

基于外部报告 `decompyle3-python311-for-else-terminal-regression-fix.md`，修复
提交 `b722c378ece5b37ebb3648cdfa1a0d13b79362d2` 上的两类回归：

1. 函数末尾 `for ... else` 的命中路径丢失 `break`，错误进入 exhaustion
   suite；
2. 函数尾部条件 `while` 的 latch 被误判为 guard-continue，同一物理条件被
   恢复成重复嵌套 `while` 并追加合成 `break`。

修复必须位于 CFG/结构恢复层。不允许跳过 opcode、吞掉反编译异常、输出占位
函数或对生成源码做文本修改。无法证明基本块唯一归属时继续 fail closed。

## 2. 修复前基线

- 分支：`master`
- 提交：`b722c378ece5b37ebb3648cdfa1a0d13b79362d2`
- 工作树：干净
- Python：CPython 3.11.9
- 已知项目门禁：1095 passed、6 skipped
- 真实语料：2425/2425 可反编译，但仍有 1 个确定功能错误和 2 个循环结构
  保真回归。

## 3. 根因

### 3.1 terminal `for ... else` 的三类出口被合并

`TrackTransformBase.Update` 的 `FOR_ITER` exhaustion edge 进入真正的 else
suite；循环命中路径则跳到另一块 iterator cleanup：

```text
POP_TOP
LOAD_CONST None
RETURN_VALUE
```

当前 `_for_loop()` 没有接收外层 region 上界，且只识别 exhaustion target
本身为 None 尾声，或 else 前最后一条语义指令直接为 `POP_TOP` 的形态。该样本
两项均不满足，导致 cleanup、exhaustion suite 和普通 follow 被合并。

### 3.2 terminal while 的真实 latch 被当成 guard-continue

普通 while 的尾部条件为 `POP_JUMP_BACKWARD_IF_*`，false fallthrough 后紧跟
一份隐式 None 尾声，入口 false endpoint 还有另一份等价 None 尾声。
`_trailing_loop_back_jump()` 把 latch 后可达的 terminal block 当作“后续循环
payload”，拒绝真实 latch。随后 body 捕获再次消费尾部条件，生成内层 while，
duplicated-None 规则又给外层追加 `break`。

## 4. 实施方案

1. 新增动态回归，先证明基线存在错误；覆盖 terminal `for ... else` 的命中、
   跳过后命中、全部 continue、空输入，以及一个/两个普通 terminal while。
2. 将当前 `_parse_region()` 上界传给 `_for_loop()`。
3. 新增 terminal-for-else CFG 计划，分别证明 body/latch、terminal iterator
   cleanup、exhaustion suite 和 region end；只接受相互不可达、栈清理闭合且
   异常区域兼容的结构。
4. body 跳到 cleanup 的边恢复为 `Break`，直接 fallthrough 只有在 CFG 证明
   后才合成 `Break`；else suite 写入 `ast.For.orelse`。
5. 在 `_while_loop()` 增加窄的 terminal-latch 证明：候选必须是当前条件的
   backward latch，且 latch fallthrough 和入口 false endpoint 都是隐式 None
   尾声。成功后在 latch 条件前结束 body，不再生成 synthetic break。
6. 执行 AST、重新编译、动态副作用、全量 pytest、golden、真实回归、release
   gate 和 2425 文件全量逻辑对比。

## 5. 验收门槛

- terminal for-else 的命中路径不执行 fallback；耗尽路径只执行 fallback；
- 原函数与反编译后函数的返回值、异常、调用次数、事件顺序和容器状态一致；
- `drain_list` 只有一个 `ast.While`，两个顺序循环只有两个 `ast.While`；
- 普通 while 不出现合成 `Break`；
- `TrackTransformBase.Update` 恢复互斥 break/else 边界；
- `DelayRunMgr.updateDelayRun` 和 `GambleYuhunResSet.clear` 不再重复 while；
- 2425/2425 继续全部反编译并能重新编译；
- 既有复杂条件、循环 payload、异常清理和私有方法测试不回归。

## 6. 执行结果

### 6.1 源码修改

- `decompyle3/controlflow/structures.py`
  - `_for_loop()` 接收真实 region 上界；
  - 新增 terminal-for-else CFG 计划，分离 iterator cleanup、exhaustion
    suite 和物理 region end；
  - 只在 CFG 证明 body 路径进入 cleanup 后恢复 `break`；
  - 新增 terminal-while latch 证明，要求重复条件 AST 相同，且 latch false
    path、入口 false endpoint 都是闭合的隐式 None-return sink。
- `pytest/test_for_else_terminal_regression311.py`
  - 增加 terminal `for ... else` 直线 break；
  - 增加分支 `JUMP_FORWARD`/fallthrough 汇入 iterator cleanup；
  - 增加显式 `return None` 与无 else 的普通 break 反例；
  - 增加单个和两个顺序 terminal while 的动态与 AST 回归。
- `PYTHON_311_REALWORLD_REGRESSION.md`、
  `test/bytecode_3.11/realworld_regression311.json`
  - 按项目生成器更新修改后输入摘要；测试分类和门禁基线未被放宽。

### 6.2 动态语义结果

- 新增回归：`5 passed`；所有用例均将原函数与反编译后重新编译的函数
  对照执行，比较返回值、异常类型、事件顺序、调用次数和容器最终状态；
- `TrackTransformBase.Update`：从新反编译源码提取函数并用 mock 关键帧执行，
  `time=5` 得到 `(5.0, 5.0, 5.0)`，且 hit path 未进入 fallback；
- `DelayRunMgr.updateDelayRun`：恢复为 2 个顺序 `while`（原有索引循环和
  satisfied 队列循环），0 个伪造 `break`；
- `GambleYuhunResSet.clear`：恢复为 2 个顺序 `while`，0 个伪造
  `break`。

### 6.3 测试命令与结果

```text
.venv311/bin/python -m pytest -q pytest/test_for_else_terminal_regression311.py
5 passed

.venv311/bin/python test/bytecode_3.11/generate.py --check
checked 32 CPython 3.11 corpus files

.venv311/bin/python test/bytecode_3.11/run_realworld_regression.py --check
通过

.venv311/bin/python test/bytecode_3.11/run_release_gate.py --check
Opcode/Scanner/Behavior 110/110；Shape pass 45；fail-closed 1

.venv311/bin/python test/bytecode_3.11/run_release_gate.py --pytest
1100 passed, 6 skipped
```

真实语料命令：

```text
LOGIC_CHECK_DECOMPILED_ROOT=/tmp/decompile3-for-else-fixed \
LOGIC_CHECK_REPORT_ROOT=/tmp/decompile3-for-else-fixed-report \
bash scripts/run_decompile_logic_check.sh --redecompile
```

结果：`2425/2425 successful`，`unsupported_python27=0`。相对修复前报告：

- `normalized_match`：153 -> 154；
- `low_risk_difference`：111 -> 112；
- `needs_review`：593 -> 591；
- `code_object_layout_difference`：3 -> 2；
- `DelayRunMgr.py`：`needs_review` -> `low_risk_difference`；
- `GambleYuhunResPool.py`：`needs_review` -> `normalized_match`。

### 6.4 安全边界结论

本修复没有增加 opcode 跳过、异常吞噬、`pass`/空函数占位或输出文本后处理。
显式 `return None` 通过源码位置信号保留；条件表达式必须 AST 等价；cleanup
和 else/return sink 必须满足 CFG predecessor、normal-edge、terminal 和异常入口
约束。证据不足时 helper 返回 `None`，继续走既有 fail-closed 路径。

Git 基线：`b722c378ece5b37ebb3648cdfa1a0d13b79362d2`。本报告与修复由同一
提交固化，最终提交号以 `git log` 和任务交付信息为准。
