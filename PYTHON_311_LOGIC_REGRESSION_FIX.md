# Python 3.11 逻辑回归修复报告

## 1. 修复目标

基于 `decompyle3-python311-logic-regression-fix-report.md` 固化并修复三类
CPython 3.11 反编译问题：

1. `for`/`while` 中 guard-`continue` 后的可达有效载荷被裁掉；
2. 条件表达式、分支 join 和循环 follow 证明不足，可能产生错误的
   `IfExp`、额外 `while True` 或错误公共出口；
3. 类作用域内双下划线私有方法仍以字节码中的 mangled 名称输出。

修复必须保持 fail-closed：不能跳过 opcode、吞掉解析异常、输出占位函数，
也不能在反编译后对源码做文本替换。

## 2. 修复前基线

- 仓库：`python-decompile3`
- 分支：`master`
- 基线提交：`bec137336de149ad6782140552a1b2f641a819e9`
- 外部逻辑审计：2425 个 Python 3.11 输出，1552 个进入对比，604 个待复核；
  已确认 34 个循环有效载荷错误、7 个复杂控制流错误和 22 个私有方法名错误。

修复前抽样结果：

- `TrackCocosVx.Update` 只剩两个 guard-`continue`，后续调用、赋值和退出丢失；
- `PatchCloud2.init_async` 被恢复出额外的外层 `while True`；
- `JSONRPCError.__get_code` 被输出成 `_JSONRPCError__get_code`；
- 上述反编译源码均可编译，说明仅做语法检查不能发现这些错误。

## 3. 根因

### 3.1 循环区域所有权错误

`_for_loop()` 把所有回到 `FOR_ITER` 的反向跳转都当作循环尾 latch，并取最后
一个候选。对于“若干 guard-continue + 调用/赋值 + break/return”结构，最后
一个回跳实际仍是 `continue`；真正的非 continue 路径位于它之后。错误的
`body_limit` 使 `_capture_region()` 在有效载荷前结束。

`_jump_control()` 生成 `Continue` 并结束当前分支是正确行为，不能通过忽略该
跳转修复。修复点必须是循环体、分支 suite 和 follow 的 CFG 归属。

### 3.2 复杂条件和循环证明不足

当前 `IfExp`、`if/else` 和 `while True` 恢复仍有依赖物理偏移与“最后一个
跳转”的路径。构造结构前没有统一证明分支块闭合、join 栈效果一致、循环头
支配回边以及 follow 位于循环外部，因此复杂短路、终止分支和异常区域可能被
错误合并。

### 3.3 类名上下文丢失

类体反编译器只收到 `is_class_body=True`，没有收到源级类名；函数定义直接用
`STORE_NAME` 的 target。CPython 已在编译期把 `__name` 变成
`_ClassName__name`，因此反编译结果泄漏了 mangled 名称。

## 4. 实施步骤

1. 增加最小回归，覆盖单/多 guard-continue、payload 调用/赋值/return/break、
   while、嵌套条件、生成器以及类私有方法。
2. 用 CFG 普通边确认候选回边是否为物理尾 latch；若回边后仍有同一循环内的
   可达正常块，则把它视为 continue，而不是循环体上界。
3. 为条件表达式和无限循环增加闭合性、支配/后支配、栈效果与控制转移证明；
   证明不足时保持 fail-closed，不猜测源码结构。
4. 将类名传入类体反编译器；仅当候选 `co_name` 按 CPython 规则重新 mangling
   后与实际存储名完全一致时，输出源级私有名。
5. 执行 AST/compile、动态语义、完整 Python 3.11 pytest、真实 fixed.pyc
   反编译和批量逻辑对比。

## 5. 验收门槛

- 原函数与反编译后函数在输入矩阵中的返回值、异常类型、副作用、调用次数和
  顺序一致；
- 私有方法的类字典键保持一致，同时 `__name__`/`__qualname__` 恢复源级语义；
- 生成源码可由 CPython 3.11 重新编译；
- 真实样本不出现占位函数、跳过函数或新增解析异常；
- 全量测试和逻辑审计相较基线不新增高风险差异。

## 6. 执行结果

### 6.1 实现

修改源码：

- `decompyle3/controlflow/structures.py`
  - 用普通 CFG 边计算循环区域内可达基本块，不再把 guard-`continue`
    的回跳当作物理尾 latch；
  - `for`、条件 `while` 和 `while True` 统一使用经过尾部可达性证明的
    latch，并恢复由重复 `return None` 尾声编码的 `break`；
  - `while True` 的 tail-break `NOP` 必须由循环头沿普通边可达且不能有
    异常入口，避免把 finally/handler 协议块误判成 `break`；
  - 对“非 continue 路径只会 return/raise”的闭合无限循环，把完整异常区域
    保留在循环体内，使异常结构器消费 `RERAISE`，不让协议 opcode 泄漏到
    普通语句解析；
  - 扩展链式比较和同一 PEP 657 源位置的短路续接证明；
  - 对共享 `or` 后缀做保持求值顺序的条件 DAG 因式分解，避免重复条件和
    分支体。
- `decompyle3/parsers/p311/base.py`
  - 将当前类名传入嵌套类体反编译器；
  - 只在 `co_name` 重新按 CPython 规则 mangling 后与 `STORE_NAME` 完全一致
    时，恢复源级 `__private` 名称；普通 `_Class__manual` 名称保持原样。

实现没有新增 opcode 跳过，没有把异常转换为 `pass`，也没有对生成源码做
文本后处理。结构证明失败时仍沿现有 fail-closed 路径报错。

### 6.2 新增回归

新增 `pytest/test_logic_regression_fix311.py`，共 7 个动态测试，覆盖：

- 双 guard-`continue` 后的调用和 `break`；
- 条件 `while`、`while True`、生成器中的 payload 与事件顺序；
- `while True + try/finally + continue/return`，防止 handler `NOP` 被误认成
  tail-break；
- 两组链式比较、跨行短路条件、四组 `and/or` 条件；
- 自定义真值对象的 `__bool__`/`__eq__` 调用次数和顺序；
- 普通类、前导下划线类、嵌套类的私有方法，以及看似 mangled 但不应反解
  的普通方法。

每个行为用例均编译原源码、反编译 code object、对输出执行 `ast.parse()` 和
`compile()`，再分别执行原函数与恢复函数，比较返回值及类型、异常、调用次数和
副作用事件序列。

### 6.3 验证结果

仓内验证：

- `python -m flake8 ...`：通过；
- `python -m compileall -q ...`：通过；
- 新增动态回归：`7 passed`；
- 全量 pytest：`1095 passed, 6 skipped`；
- 32 个 CPython 3.11 golden：通过；
- 仓内真实回归：604/604 反编译成功、604/604 重新编译成功、0
  fail-closed、0 crash，6/6 动态行为一致；
- release gate：110/110 opcode 行为验证通过，shape 为 45 pass、1 个既有
  fail-closed，归档时效和 pytest skip 白名单通过。

外部 2,425 文件验证只读取 marshal/fixed.pyc 并反编译，不执行其代码：

- 2,425/2,425 生成可重新编译源码，0 个反编译失败；
- 对照 Python 2.7 的 1,552 个有效参考，`needs_review` 从 604 降到 593；
- `definition_logic_match` 从 274 增至 281，`normalized_match` 从 148
  增至 153；
- `bytecode_exact_match=237`、`function_bytecode_match=177` 均未下降；
- 已确认的循环样本恢复了 guard 后 payload；7 个复杂控制流样本不再出现
  报告中的额外无限循环、提前 return、恒真返回、丢失 else/成功分支等形态；
- 2,425 个输出中，`def _Class__private` 形式的遗留定义数为 0；报告列出的
  10 个文件、22 个私有方法均恢复成源级双下划线定义。

验证中曾发现 `SubResPatcher._fetch_http_npk` 因 finally handler 的 `NOP` 被
误认成 tail-break 而出现裸 `RERAISE`。通过基线/当前 A/B 确认为本轮回归后，
补充普通可达性和异常入口证明，并加入动态回归；最终该文件和全量批次均通过。

外部生成物和对比报告保存在 `/tmp`，未修改外部语料仓库。代码、测试、
本报告及更新后的仓内真实回归归档由同一个 Git 提交固化。
