# Python 3.11 `.py → .pyc → .py` 端到端验证计划

## 1. 目标

验证本工程能否完整执行下面的 Python 3.11 字节码反编译链路：

```text
Python 3.11 源文件
        ↓ py_compile
Python 3.11 .pyc
        ↓ decompyle3
恢复后的 Python 源文件
        ↓ Python 3.11 执行
与原文件进行行为对比
```

本次验证分为 **8 个步骤**。成功标准不仅是生成恢复文件，还要求：

- 输入文件确实由 CPython 3.11 编译；
- `.pyc` 能被 CPython 3.11 正常执行；
- `decompyle3` 反编译命令返回成功；
- 恢复文件能通过 Python 3.11 语法检查；
- 原始源码、`.pyc` 和恢复源码的运行结果完全一致；
- 整个验证过程不修改仓库内的源码和测试文件。

## 2. 执行约定

- 在仓库根目录执行本文命令。
- 使用同一个 Bash 或 Zsh 终端会话依次执行各步骤，保证环境变量持续有效。
- 使用仓库现有的 `.venv311` 环境。
- 所有临时产物放在系统临时目录，不写入 Git 工作区。
- 任一步骤失败后立即停止，保留临时目录用于诊断。

## 步骤 1：检查环境并建立隔离目录

先进入仓库根目录，然后执行：

```bash
set -euo pipefail

REPO_ROOT="$PWD"
PYTHON311="$REPO_ROOT/.venv311/bin/python"
DECOMPYLE3="$REPO_ROOT/.venv311/bin/decompyle3"
E2E_DIR="$(mktemp -d /tmp/python-decompile3-e2e311.XXXXXX)"

SOURCE_FILE="$E2E_DIR/sample311.py"
PYC_FILE="$E2E_DIR/sample311.pyc"
RECOVERED_FILE="$E2E_DIR/sample311_recovered.py"

ORIGINAL_OUTPUT="$E2E_DIR/original.out"
PYC_OUTPUT="$E2E_DIR/pyc.out"
RECOVERED_OUTPUT="$E2E_DIR/recovered.out"

test -x "$PYTHON311"
test -x "$DECOMPYLE3"

"$PYTHON311" -c 'import sys; assert sys.version_info[:2] == (3, 11), sys.version; print(sys.version)'
"$DECOMPYLE3" --version

git status --short > "$E2E_DIR/git-before.txt"
printf '临时目录：%s\n' "$E2E_DIR"
```

预期结果：

- Python 版本为 `3.11.x`；
- `decompyle3 --version` 正常输出版本；
- 终端打印一个以 `/tmp/python-decompile3-e2e311.` 开头的临时目录。

如果这里失败，不应继续后续步骤。需要先确认 `.venv311` 已创建并完成本工程安装。

## 步骤 2：生成 Python 3.11 样例源码

样例覆盖类、闭包、短路表达式、列表推导式过滤、条件表达式、异常处理、`finally` 和 f-string：

```bash
cat > "$SOURCE_FILE" <<'PY'
import json


FACTOR = 3


class Accumulator:
    def __init__(self, initial):
        self.value = initial

    def add(self, amount):
        self.value += amount
        return self.value


def transform(values):
    selected = [value * FACTOR for value in values if 0 < value < 6]
    total = sum(selected)
    label = "large" if total > 20 else "small"
    return selected, total, label


def make_offset(base):
    def apply(value):
        return (value and value + base) or base

    return apply


def safe_divide(left, right):
    try:
        result = left // right
    except ZeroDivisionError:
        result = None
    finally:
        marker = "done"
    return result, marker


def main():
    accumulator = Accumulator(5)
    selected, total, label = transform([-2, 0, 1, 3, 5, 7])
    offset = make_offset(10)
    result = {
        "accumulator": accumulator.add(4),
        "division": [safe_divide(9, 2), safe_divide(9, 0)],
        "message": f"{label}:{total}",
        "offset": [offset(0), offset(5)],
        "selected": selected,
    }
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
PY

test -s "$SOURCE_FILE"
"$PYTHON311" -m py_compile "$SOURCE_FILE"
```

预期结果：命令返回码为 `0`，且 `$SOURCE_FILE` 非空。

说明：最后一条命令只用于提前检查源文件语法。正式测试使用的 `.pyc` 将在下一步写到指定路径。

## 步骤 3：运行原始源码并记录基准结果

```bash
"$PYTHON311" "$SOURCE_FILE" > "$ORIGINAL_OUTPUT"
cat "$ORIGINAL_OUTPUT"
```

预期输出：

```json
{"accumulator": 9, "division": [[4, "done"], [null, "done"]], "message": "large:27", "offset": [10, 15], "selected": [3, 9, 15]}
```

这一输出是后续行为对比的基准。

## 步骤 4：将源码编译为指定的 Python 3.11 `.pyc`

```bash
"$PYTHON311" -c \
  'import py_compile, sys; py_compile.compile(sys.argv[1], cfile=sys.argv[2], doraise=True, optimize=0)' \
  "$SOURCE_FILE" "$PYC_FILE"

test -s "$PYC_FILE"
ls -l "$PYC_FILE"
```

然后确认 `.pyc` 的 magic number 与当前 CPython 3.11 解释器一致：

```bash
"$PYTHON311" -c \
  'import importlib.util, pathlib, sys; data = pathlib.Path(sys.argv[1]).read_bytes(); assert data[:4] == importlib.util.MAGIC_NUMBER, (data[:4], importlib.util.MAGIC_NUMBER); print("magic number:", data[:4].hex())' \
  "$PYC_FILE"
```

预期结果：

- `$PYC_FILE` 存在且非空；
- magic number 检查通过；
- 没有 `PyCompileError` 或 `AssertionError`。

## 步骤 5：直接执行 `.pyc` 并与源码结果比较

```bash
"$PYTHON311" "$PYC_FILE" > "$PYC_OUTPUT"
diff -u "$ORIGINAL_OUTPUT" "$PYC_OUTPUT"
```

预期结果：`diff` 不输出任何差异，返回码为 `0`。

这一检查把“源码编译问题”和“反编译问题”分开：如果本步骤失败，问题发生在反编译之前。

## 步骤 6：使用本工程将 `.pyc` 反编译为 `.py`

```bash
"$DECOMPYLE3" \
  --verify syntax \
  --output "$RECOVERED_FILE" \
  "$PYC_FILE"

test -s "$RECOVERED_FILE"
test ! -e "${RECOVERED_FILE}_failed"
```

查看恢复结果：

```bash
sed -n '1,240p' "$RECOVERED_FILE"
```

预期结果：

- `decompyle3` 返回码为 `0`；
- `$RECOVERED_FILE` 存在且非空；
- 不存在 `${RECOVERED_FILE}_failed`；
- 输出中没有 `Parse error`、`Unsupported Python version` 或 traceback。

注意：Python 3.11 当前对非默认 offset 采取 fail-closed 行为。本流程不得加入 `--start-offset` 或 `--stop-offset`。

## 步骤 7：验证恢复源码的语法和运行行为

先进行独立语法检查：

```bash
"$PYTHON311" -c \
  'import ast, pathlib, sys; ast.parse(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8")); print("AST parse: OK")' \
  "$RECOVERED_FILE"

"$PYTHON311" -m py_compile "$RECOVERED_FILE"
```

再执行恢复后的源码：

```bash
"$PYTHON311" "$RECOVERED_FILE" > "$RECOVERED_OUTPUT"
cat "$RECOVERED_OUTPUT"
```

比较三份程序的行为：

```bash
diff -u "$ORIGINAL_OUTPUT" "$PYC_OUTPUT"
diff -u "$ORIGINAL_OUTPUT" "$RECOVERED_OUTPUT"
```

预期结果：两个 `diff` 均无输出并返回 `0`。

可以额外查看源码文本差异，但文本完全一致不是验收要求：

```bash
diff -u "$SOURCE_FILE" "$RECOVERED_FILE" || true
```

反编译器可能改变空行、括号、局部格式或生成文件头；只要语法有效且运行行为一致，就不应仅因文本差异判定失败。

## 步骤 8：汇总结果并安全清理

确认执行过程没有意外修改 Git 工作区：

```bash
git status --short > "$E2E_DIR/git-after.txt"
diff -u "$E2E_DIR/git-before.txt" "$E2E_DIR/git-after.txt"
```

全部通过后，填写验收记录：

- [ ] `.venv311` 使用 CPython 3.11；
- [ ] 样例源码能运行；
- [ ] `.pyc` 已生成且 magic number 正确；
- [ ] `.pyc` 与原始源码输出一致；
- [ ] `decompyle3` 成功生成恢复源码；
- [ ] 恢复源码通过 AST 和 `py_compile` 检查；
- [ ] 恢复源码与原始源码输出一致；
- [ ] Git 工作区状态没有因验证流程发生变化。

全部勾选后，可认为本样例的 Python 3.11 `.py → .pyc → .py` 端到端流程通过。

确认不再需要诊断文件后，使用带路径保护的命令清理临时目录：

```bash
"$PYTHON311" -c \
  'import pathlib, shutil, sys; path = pathlib.Path(sys.argv[1]).resolve(); allowed = pathlib.Path("/tmp").resolve(); assert path.parent == allowed and path.name.startswith("python-decompile3-e2e311."), path; shutil.rmtree(path); print("removed:", path)' \
  "$E2E_DIR"
```

## 3. 失败处理

发生失败时不要先清理 `$E2E_DIR`。按失败位置分类：

| 失败步骤 | 优先检查 |
| --- | --- |
| 步骤 1 | `.venv311` 是否存在、Python 是否为 3.11、本工程是否安装 |
| 步骤 2～4 | 样例源码语法、`py_compile` 异常、`.pyc` magic number |
| 步骤 5 | `.pyc` 是否由当前 `.venv311` 生成 |
| 步骤 6 | 反编译终端输出、`${RECOVERED_FILE}_failed`、恢复文件中的 parse error |
| 步骤 7 语法失败 | 恢复文件出错行附近的语法结构和对应字节码 |
| 步骤 7 行为失败 | 三份 `.out` 文件及原始/恢复源码的差异 |
| 步骤 8 | `git-before.txt` 与 `git-after.txt` 的差异 |

建议保留下列文件作为问题报告附件：

```text
sample311.py
sample311.pyc
sample311_recovered.py
original.out
pyc.out
recovered.out
```

如果恢复文件生成失败，还应同时保存完整的 `decompyle3` 标准输出、标准错误和 traceback。

## 4. 最终判定

- **通过**：步骤 1～8 的强制检查全部成功。
- **部分通过**：能够生成恢复源码，但语法检查或运行行为对比失败。
- **不通过**：无法读取 Python 3.11 `.pyc`，或反编译命令未生成有效源码。

“生成了一个 `.py` 文件”本身不足以证明 Python 3.11 反编译链路可用；正式结论必须以语法验证和行为一致性验证为准。
