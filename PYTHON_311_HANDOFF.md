# CPython 3.11 维护工作交接

> 本文用于在新的 Codex 对话或新的开发设备上继续 `decompyle3` 的
> CPython 3.11 修复工作。仓库内的代码、测试和机器归档是事实来源；聊天记录只作
> 补充，不能代替 Git 状态和回归门禁。

## 1. 当前交接点

本文建立于 2026-08-19，交接基线为：

| 项目 | 当前值 |
| --- | --- |
| 仓库 | `https://github.com/x4317350/python-decompile3.git` |
| 上游仓库 | `https://github.com/rocky/python-decompile3.git` |
| 分支 | `master` |
| 基线提交 | `17a118cb7922cd7c7014b12d38ed64b781eebeb2` |
| 提交说明 | `修复 Python 3.11 终止循环控制流` |
| 项目版本 | `decompyle3 3.9.4.dev0` |
| 目标字节码 | CPython 3.11 标准磁盘 `.pyc` |
| 开发运行时 | CPython 3.11.9 |
| `xdis` | 6.3.0 |
| 工作区状态 | 基线检查时干净，`master` 与 `origin/master` 同步 |

`17a118cb` 是添加本文之前的代码基线。本文提交后，新设备应检查当前
`origin/master` 是该提交的后代，而不是要求 HEAD 永远等于此值。

当前没有未完成的 3.11 实现阶段或已知待提交修复。阶段 0～8、Opcode/Shape
发布门禁和后续真实项目控制流修复均已完成。新问题应作为维护修复处理，不要从
`PYTHON_311_IMPLEMENTATION_PLAN.md` 的阶段 0 重新实现 3.11 流水线。

## 2. 当前能力和安全边界

当前支持范围以 `PYTHON_311_SUPPORT.md` 为准：

- 支持 released CPython 3.11 生成的标准磁盘 `.pyc`；
- 支持普通表达式、函数、类、闭包、推导式、生成器、协程、普通控制流、
  exception table、`with`、`match/case` 和已经有严格结构证明的 `except*`；
- 追求执行语义等价，不承诺恢复注释、空白、引号或原始源码排版；
- Scanner 保留物理 offset/inline cache，Normalizer、CFG、结构恢复和源码生成保持
  分层；
- 无法证明的结构必须明确 fail closed，不得输出猜测源码或部分函数。

维护时必须继续遵守：

1. 不允许跳过未知 opcode 后继续生成源码。
2. 不允许捕获反编译异常后输出 `pass`、空函数或失败占位函数。
3. 不允许手工修改最终反编译结果来规避结构恢复缺陷。
4. 不允许把所有相同的 `LOAD_CONST None / RETURN_VALUE` 出口无条件合并。
5. 不允许仅凭 `ast.parse()`、`compile()` 或 code object 数量一致宣告语义正确。
6. 条件、调用、异常、循环和上下文管理器修复必须比较返回值及其精确类型、异常、
   调用次数、调用顺序和外部状态。
7. 外部 marshal/PYC 只允许读取、转换和反编译，不得执行其中代码。
8. 动态行为测试只执行仓库内可审查的最小源码 fixture 和 mock，不执行真实项目
   PYC 恢复出的代码。
9. 证据不足时保持原有 fail-closed；不能为了提高成功数放宽结构启发式。

## 3. 当前发布门禁

### 3.1 2026-08-19 本次复核

在 CPython 3.11.9、`xdis 6.3.0`、`pytest 9.1.1` 环境执行：

```console
.venv311/bin/python -m pytest -q pytest/test_for_else_terminal_regression311.py
5 passed

.venv311/bin/python test/bytecode_3.11/generate.py --check
checked 32 CPython 3.11 corpus files

.venv311/bin/python test/bytecode_3.11/run_realworld_regression.py --check
exit 0（归档一致）

.venv311/bin/python test/bytecode_3.11/run_release_gate.py --check
文档与归档时效检查：通过

.venv311/bin/python test/bytecode_3.11/run_release_gate.py --pytest
1100 passed, 6 skipped
```

四层覆盖结果：

- Opcode inventory：110/110；
- Scanner：110/110；
- Normalizer：102 pass、8 internal-consumed；
- Parser：102 pass、8 internal-consumed、0 missing；
- Behavior：110/110；
- Shape：45 pass、1 个已审批 fail-closed、0 missing。

唯一已审批的 shape 是人工构造的 `irreducible_control_flow`。它是安全边界，不能
通过删除检查或猜测结构改成 pass。

### 3.2 仓内固定真实语料

`PYTHON_311_REALWORLD_REGRESSION.md` 和
`test/bytecode_3.11/realworld_regression311.json` 固定了发布语料：

- 604/604 成功反编译；
- 604/604 语法验证成功；
- 0 fail-closed；
- 0 syntax failure；
- 0 unexpected crash；
- 6/6 差分行为探针一致。

这 604 项是可重复的发布门禁，不代表任意第三方 CPython 3.11 PYC 都已经覆盖。

### 3.3 外部 2,425 文件逻辑审计

最新提交 `17a118cb` 内的修复报告记录：

- 自定义 Opcode marshal 转成标准 CPython 3.11 PYC 后，2425/2425 完成反编译；
- 2425/2425 生成源码可重新编译；
- `unsupported_python27=0`；
- 最新 `for ... else` 确定语义错误和两个重复 `while` 结构回归已修复；
- 外部输入只读取、转换和反编译，未执行其中代码。

该批次不属于仓内 604 文件发布归档。换设备后如果没有外部语料和 Python 2.7
参考源码，只能运行仓内门禁，不能声称重新完成了 2425 文件审计。

## 4. 新设备恢复环境

### 4.1 获取代码

当前 3.11 基线已经推送到用户 fork 的 `origin/master`：

```bash
git clone https://github.com/x4317350/python-decompile3.git
cd python-decompile3
git remote add upstream https://github.com/rocky/python-decompile3.git
git switch master
git pull --ff-only origin master
git status --short --branch
git log -5 --oneline
```

开始修改前，确认：

- 工作区没有未知修改；
- `HEAD` 包含 `17a118cb7922cd7c7014b12d38ed64b781eebeb2`；
- 当前分支基于用户 fork，而不是误在 `upstream/master` 上修改；
- 如果 `origin/master` 已有更新，以最新已审核提交为准，并先阅读新增报告。

### 4.2 重建虚拟环境

不要从旧设备复制 `.venv311`。在新设备用 CPython 3.11 重建：

```bash
python3.11 -m venv .venv311
.venv311/bin/python -m pip install -e '.[dev]'
.venv311/bin/python --version
.venv311/bin/python -c "import xdis, pytest; print(xdis.__version__, pytest.__version__)"
```

已发布门禁的固定环境详见 `PYTHON_311_RELEASE_GATE.md`。至少应确认运行时为
CPython 3.11.x，且 `xdis` 能正确读取 CPython 3.11 PYC；重建基线时优先使用本文
记录的 CPython 3.11.9 和 `xdis 6.3.0`。

### 4.3 首次门禁

```bash
.venv311/bin/python test/bytecode_3.11/generate.py --check
.venv311/bin/python test/bytecode_3.11/run_realworld_regression.py --check
.venv311/bin/python test/bytecode_3.11/run_release_gate.py --check
.venv311/bin/python test/bytecode_3.11/run_release_gate.py --pytest
```

首次门禁不通过时先检查 Python/依赖版本、归档时效和工作区状态，不要立即放宽
测试或更新 golden。

## 5. 必须单独迁移的外部资产

以下文件和目录不属于 `python-decompile3` Git 仓库，新设备如需复现真实项目问题，
必须通过受控方式另行同步：

```text
/Users/ice/Desktop/Custom/WorkCode_github/py3disassembly
/Users/ice/Desktop/Custom/WorkCode_github/py3disassembly/dump/dump_marshal
/Users/ice/Desktop/Custom/unpak/0810/originalpy
```

2425 文件逻辑审计当前默认依赖：

- marshal 输入：`py3disassembly/dump/dump_marshal`；
- Python 3.11 反编译输出：默认
  `py3disassembly/dump/dump_marshal_decompiled`；
- Python 2.7 参考源码：`/Users/ice/Desktop/Custom/unpak/0810/originalpy`；
- Python 2.7 解释器：
  `/Library/Frameworks/Python.framework/Versions/2.7/bin/python2.7`。

路径可通过 `LOGIC_CHECK_*` 环境变量覆盖，具体以
`py3disassembly/scripts/run_decompile_logic_check.sh` 为准。外部语料、恢复源码和
对比报告不应提交到 `python-decompile3`。

最重要的单文件回归资产是：

```text
py3disassembly/dump/testcfg/network.rpcentity.ClientEntities.original.marshal
py3disassembly/dump/testcfg/network.rpcentity.ClientEntities.original.fixed.pyc
py3disassembly/dump/testcfg/network.rpcentity.ClientEntities.py
py3disassembly/dump/testcfg/network.rpcentity.ClientEntities.original.decompyle3.py
```

其中：

- `.marshal` 是自定义 Opcode 输入；
- `.fixed.pyc` 是转换后的标准 CPython 3.11 PYC；
- `ClientEntities.py` 是已知正确的源码对照；
- `.original.decompyle3.py` 是反编译结果，不是正确性基准。

过去还使用过：

```text
py3disassembly/py3Tool/map_opcode/fixed_output_repaired.pyc
py3disassembly/py3Tool/all_opcodes_311.py
py3disassembly/dump/test/Globals.py
py3disassembly/dump/test/Globals.original.marshal
py3disassembly/dump/test/Globals.original.decompyle3.py
```

这些资产在新设备上缺失时，仓内测试仍可运行，但不能完成相应真实样本复核。

## 6. 真实文件验证与修复前后快照

每次修复前必须保留同一基线提交生成的反编译文件，不能只保留修复后的输出。
建议为每个问题创建独立目录，至少保存：

```text
before.decompyle3.py
after.decompyle3.py
before.log
after.log
diff.patch
HEAD.before.txt
HEAD.after.txt
```

单文件反编译命令：

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

覆盖当前输出前先复制为 `before.decompyle3.py`，并记录生成它的 decompile3 HEAD。
修复后生成 `after.decompyle3.py`，用已知正确的 `ClientEntities.py`、反编译前后
diff、AST/compile、code object 清单和定向逻辑检查共同判断。不要仅因文本更接近
Python 2.7 源码就判定正确。

全量逻辑审计示例：

```bash
cd /Users/ice/Desktop/Custom/WorkCode_github/py3disassembly

LOGIC_CHECK_DECOMPILED_ROOT=/tmp/decompile3-311-current \
LOGIC_CHECK_REPORT_ROOT=/tmp/decompile3-311-current-report \
bash scripts/run_decompile_logic_check.sh --redecompile
```

该脚本会转换和反编译真实输入，并运行静态/结构对比。它不能替代仓内最小动态
行为测试，也不能将真实恢复代码作为可信代码执行。

## 7. 新问题的标准处理顺序

1. 检查 `git status --short --branch`、`git rev-parse HEAD` 和最近提交。
2. 保存修复前真实文件输出、日志、HEAD 和摘要。
3. 确认问题是语义错误、源码保真差异还是单纯格式差异。
4. 从真实 code object 提取最小可审查源码，用 CPython 3.11 编译稳定复现。
5. 查看 `dis`、物理 offset、普通 CFG edge、exception table、栈效果和源码位置。
6. 先添加失败的动态语义测试，并加入会拒绝过宽规则的负向 fixture。
7. 在 Scanner/Normalizer/CFG/结构恢复的正确层修复，不在输出文本上打补丁。
8. 对 owner、前驱/后继、正常边、异常边、join、stack depth 和 region 边界给出
   可审计证明；证明不完整则 fail closed。
9. 运行定向测试、相关控制流测试、完整门禁和真实样本前后对比。
10. 同一提交内保存实现、测试、问题/修复报告及需要更新的机器归档。

若问题涉及外部真实文件，问题报告至少应记录：

- 基线提交、Python/decompyle3/xdis 版本；
- 输入路径及 SHA-256；
- code object 名称和失败 offset；
- 最小源码与关键 `dis`/exception-table 形态；
- 正确行为、错误行为和副作用差异；
- fail-closed 证明边界；
- 修复前后测试命令及结果。

## 8. 3.11 控制流维护经验

CPython 3.11 的多次真实回归集中在
`decompyle3/controlflow/structures.py`，但不能因此把所有问题都用一个宽泛 helper
解决。修改前应特别检查以下已知陷阱：

### 8.1 重复的隐式 None 尾声

编译器可能为多个控制流出口复制 `LOAD_CONST None / RETURN_VALUE`。物理指令相同
不代表源码控制边等价：其中可能分别表示条件 false 路径、循环 break/命中路径、
异常 handler 早退或公共函数尾声。删除或合并前必须证明控制所有权。

### 8.2 zero-cost exception table

异常 handler、cleanup、正常 continuation 和 reraising suffix 不能按物理顺序线性
解析。`try` 正常出口、`except: continue/return/break`、`with` cleanup 和
`except*` 协议都依赖 exception table region 与正常 CFG edge 的联合证明。

### 8.3 AND/OR 和条件表达式

短路跳转同时承载求值顺序和栈值所有权。不能因为两个出口终止于相同返回块就丢弃
表达式接收对象，也不能把 `True if value else False` 优化成原始 `value`；后者必须
保留 `bool` 类型语义。

### 8.4 循环 terminal frontier

guard-`continue`、真实 latch、`break` cleanup、`for ... else` exhaustion suite、
循环 follow 和函数尾声必须分开。最近一次 `17a118cb` 修复的正是 terminal
`for ... else` 和重复 `while` 所有权问题。

### 8.5 源码位置信息只能作为证据之一

PEP 657 位置可帮助区分显式/隐式结构，但不能单独覆盖 CFG、栈和 exception table
证据。位置缺失或冲突时应采取保守路径。

### 8.6 格式不是当前维护目标

提交 `ea36a05b` 的 Python 3.11 格式化修改已由 `cc3355bc` 回撤。不要重新引入
函数默认参数空格等纯格式修改。当前源码由 `ast.unparse()` 渲染，原始格式无法从
bytecode 可靠恢复。

## 9. 文档阅读顺序

新对话或新设备接手时按以下顺序阅读：

1. `PYTHON_311_HANDOFF.md`（本文）；
2. `PYTHON_311_SUPPORT.md`；
3. `PYTHON_311_RELEASE_GATE.md`；
4. `PYTHON_311_REALWORLD_REGRESSION.md`；
5. `PYTHON_311_SHAPE_COVERAGE.md`；
6. `PYTHON_311_IMPLEMENTATION_PLAN.md`（了解分层和历史，不重做已完成阶段）；
7. 与当前问题最接近的专项报告。

最近且最重要的专项报告：

| 报告 | 对应问题 |
| --- | --- |
| `PYTHON_311_FOR_ELSE_TERMINAL_REGRESSION_FIX.md` | terminal `for ... else`、重复 terminal `while` |
| `PYTHON_311_LOGIC_REGRESSION_FIX.md` | guard-continue payload、条件/循环证明、私有名反解 |
| `DECOMPILE3_DUMP_MARSHAL_51_FAILURES_FIX_REPORT.md` | 51 个 marshal 失败家族 |
| `PYTHON_311_TRY_LOOP_TERMINAL_FRONTIER_FIX_PLAN.md` | `try`/循环终止前沿 |
| `PYTHON_311_SOURCE_FUNCTIONAL_DIFFERENCE_FIX_PLAN.md` | Python 2.7/3.11 功能差异 |
| `PYTHON_311_PATCH_HELPERS_FAILURE_FIX.md` | Patch/helpers 真实样本 |
| `PYTHON_311_TERMINAL_CLEANUP_REGRESSION_FIX.md` | 尾声过度清理回归 |
| `PYTHON_311_EXCEPT_CONTINUE_FIX.md` | `except: continue` |
| `PYTHON_311_RETURN_NONE_CLEANUP.md` | 重复 None 尾声 |
| `PYTHON_311_RETURN_EXPRESSION_FIX.md` | return/条件表达式语义 |
| `PYTHON_311_EXCEPT_STAR_EMPTY_BODY_FIX_PLAN.md` | canonical empty `except*` |

历史报告描述的是当时提交和当时基线，不能覆盖本文和当前 Git HEAD 的状态。

## 10. 新对话启动提示词

```text
继续维护 python-decompile3 的 CPython 3.11 反编译支持。

仓库路径：<新设备上的 python-decompile3 路径>
目标分支：master
已知代码基线：17a118cb7922cd7c7014b12d38ed64b781eebeb2

开始前先执行：
git status --short --branch
git rev-parse HEAD
git log -5 --oneline

然后完整阅读：
PYTHON_311_HANDOFF.md
PYTHON_311_SUPPORT.md
PYTHON_311_RELEASE_GATE.md
PYTHON_311_REALWORLD_REGRESSION.md
以及与本次问题对应的专项修复报告。

阶段 0～8 和当前发布门禁已经完成，不要重新实现整套 3.11 支持。
先复现本次具体问题并保存修复前输出，再增加最小动态语义测试。

必须保持 fail-closed：不得跳过 opcode、吞掉异常、输出 pass/空函数/部分函数，
不得手工修改反编译结果。外部 marshal/PYC 只能读取、转换和反编译，不能执行。
动态测试只执行可审查的最小 fixture。修复必须在正确的 CFG/结构恢复层完成，
并运行定向测试、完整 3.11 门禁和真实样本前后对比。
```

开始具体修复时，应在这段提示后追加样本路径、正确源码、当前错误输出、函数名、
失败 offset 和验收行为。不要复制整段历史聊天来代替这些可验证信息。
