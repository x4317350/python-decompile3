# CPython 3.11 开发基线

记录日期：2026-07-30（Asia/Shanghai）

## 仓库与环境

- Git 提交：`78b1d89e402ff9a94e309be73213ccec0c7aee53`
- 分支：`master`
- 初始工作区：干净
- 系统：macOS Darwin 25.5.0，arm64
- Python：CPython 3.11.9
- 虚拟环境：`.venv311`
- decompyle3：3.9.4.dev0，editable install
- xdis：6.3.0
- spark-parser：1.9.0
- pytest：9.1.1
- hypothesis：6.24.2
- flake8：7.3.0

完整快照见 `test/bytecode_3.11/DEPENDENCIES.txt`。

## xdis 3.11 装载探针

使用 CPython 3.11.9 编译包含嵌套函数的临时 `.pyc`，然后调用
`xdis.load_module()`。

结果：

- 识别版本：`(3, 11)`
- magic：`3495`
- implementation：`CPython`
- 成功读取模块 code object
- 成功读取嵌套函数 code object

结论：`xdis 6.3.0` 可以作为 3.11 `.pyc` 装载基础。

## 修改前测试结果

### 仓库根目录直接执行 pytest

命令：

```bash
.venv311/bin/python -m pytest -q
```

结果：测试收集阶段出现 32 个错误。

原因：pytest 把 `test/decompyle/` 下用于旧版本反编译的 Python 2
源码样本误认为 pytest 测试，其中包含 Python 2 `print`、tuple
parameter 等 CPython 3.11 不可解析语法。

阶段 0 已在 `pyproject.toml` 中将 `testpaths` 限制为 `pytest/`，
避免后续再次误收集这些源代码语料。

配置修正后的根目录 pytest 结果：

```text
1 failed, 7 passed, 20 skipped
```

唯一失败仍是下述 `xdis` 旧 Scanner import 兼容问题；阶段 0 新增的
3.11 corpus 测试为 `3 passed`。

### 项目 pytest 测试

命令：

```bash
.venv311/bin/python -m pytest pytest -q
```

结果：

```text
1 failed, 4 passed, 16 skipped
```

失败：

```text
pytest/test_basic.py::test_get_scanner
ModuleNotFoundError: No module named 'xdis.opcodes.opcode_37'
```

### make check

命令：

```bash
make check \
  PYTHON=/absolute/path/to/.venv311/bin/python \
  PYTHON3=/absolute/path/to/.venv311/bin/python
```

结果：在相同的 `test_get_scanner` 失败处停止。

### Python 3.7 bytecode 回归入口

命令：

```bash
make -C test check-bytecode-3.7 \
  PYTHON=/absolute/path/to/.venv311/bin/python
```

结果：在加载 `Scanner37` 时出现相同的 `xdis` import 失败。

## 已知基线问题

`pyproject.toml` 声明 `xdis > 6.2`，当前可安装版本解析为
`xdis 6.3.0`。该版本把 3.x opcode 模块放到
`xdis.opcodes.opcode_3x`，而当前代码仍从旧位置
`xdis.opcodes.opcode_37`、`xdis.opcodes.opcode_38` 导入。

这是阶段 0 开始前已经存在的依赖 API 兼容问题，不是 3.11
实现引入的回归。阶段 1 在接入 `Scanner311` 时必须先建立兼容
导入方式，并恢复现有 3.7/3.8 Scanner 测试。

## 后续回归基准

阶段 1 起至少运行：

```bash
.venv311/bin/python -m pytest -q
make check PYTHON=/absolute/path/to/.venv311/bin/python
```

不得在上述已知失败之外引入新的测试失败。
