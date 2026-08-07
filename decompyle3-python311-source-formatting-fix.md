# decompile3 Python 3.11 源码集合字面量格式化修复说明

## 1. 问题概述

decompile3 当前在恢复 CPython 3.11 源码时，使用 `ast.unparse()` 把标准 AST 转换为 Python 源码。`ast.unparse()` 不提供按行宽自动排版的能力，因此大型 `set`、`dict` 和 `tuple` 字面量经常全部输出在一行。

真实输出示例：

```python
DEFAULT_MAP = {'model\\bujianyue_guajian\\bujianyue_guajian.gim', 'model\\s1_xuzuozhinan_show\\s1_xuzuozhinan_toufa2_show.gimmodel\\s1_xuzuozhinan_show\\s1_xuzuozhinan_toufa1_show.gimmodel\\s1_xuzuozhinan\\s1_xuzuozhinan_toufa2.gim', 'model\\s1_xuzuozhinan\\s1_xuzuozhinan_toufa1.gim'}

bujianyue_special_model = {'bujianyue': ('model/bujianyue/bujianyue', 'bip01_bone276'), 'bujianyue_show': ('model/bujianyue/bujianyue', 'bip01_bone276'), 'j_bujianyue': ('model/j_bujianyue/j_bujianyue_01', 'bip01_bone276')}
```

该结果可以运行，但存在以下问题：

- 单行内容过长；
- 集合元素和字典条目不容易区分；
- 修改时容易漏掉逗号或括号；
- 与 PEP 8 及常用 Python 格式化工具的输出差异较大；
- 大型反编译文件的人工检查成本较高。

该问题属于源码可读性和格式化问题，不是当前已确认的执行语义错误。

建议将问题命名为：

> CPython 3.11 AST 源码生成阶段缺少集合字面量行宽格式化。

## 2. 修复目标

对 CPython 3.11 反编译结果增加统一的 Python 源码格式化阶段，使以下结构能够按照配置的行宽换行：

- `set`；
- `dict`；
- `tuple`；
- `list`；
- 函数调用参数；
- 函数默认参数；
- 其他 Black 支持的标准 Python 结构。

修复后应得到类似结果：

```python
DEFAULT_MAP = {
    "model\\bujianyue_guajian\\bujianyue_guajian.gim",
    "model\\s1_xuzuozhinan_show\\s1_xuzuozhinan_toufa2_show.gimmodel\\s1_xuzuozhinan_show\\s1_xuzuozhinan_toufa1_show.gimmodel\\s1_xuzuozhinan\\s1_xuzuozhinan_toufa2.gim",
    "model\\s1_xuzuozhinan\\s1_xuzuozhinan_toufa1.gim",
}

bujianyue_special_model = {
    "bujianyue": (
        "model/bujianyue/bujianyue",
        "bip01_bone276",
    ),
    "bujianyue_show": (
        "model/bujianyue/bujianyue",
        "bip01_bone276",
    ),
    "j_bujianyue": (
        "model/j_bujianyue/j_bujianyue_01",
        "bip01_bone276",
    ),
}
```

具体换行可能随格式化器版本略有变化，只要满足语义不变、结构清楚和输出稳定即可。

## 3. 当前源码生成流程

当前 CPython 3.11 源码生成入口位于：

```text
decompyle3/parsers/p311/base.py
Python311BaseParser.parse
```

核心逻辑为：

```python
tree = ast.fix_missing_locations(tree)
source = ast.unparse(tree)
```

随后由以下适配器直接写入输出流：

```text
decompyle3/semantics/customize311.py
Python311SourceWalker.gen_source
```

当前适配器执行：

```python
self.text = tree.source
self.println(self.text)
```

因此，最合适的修复位置是：

```text
AST 恢复完成
    -> ast.unparse(tree)
    -> Python 源码格式化
    -> 格式化前后 AST 等价验证
    -> 现有语法编译验证
    -> 写入最终输出
```

不建议在 Opcode、CFG 或 AST 恢复阶段处理空格和换行。这些阶段只应负责恢复执行语义。

## 4. 推荐技术方案

推荐使用 Black 处理最终 Python 源码，而不是自行编写基于字符串替换的格式化器。

选择 Black 的原因：

- 能正确处理嵌套集合、调用、切片和表达式；
- 不依赖原始源码格式；
- 输出规则稳定；
- 可以统一处理默认参数等号空格；
- 不需要修改已恢复的 AST；
- 可以通过重新解析和 AST 对比证明格式化前后语义结构一致。

不建议直接继承或调用 `ast._Unparser`，因为它属于 Python 私有实现，跨 Python 小版本可能变化。

## 5. 格式化模块实现

建议新增：

```text
decompyle3/source_format.py
```

参考实现：

```python
from __future__ import annotations

import ast

from decompyle3.errors import SemanticGenerationError


def _semantic_ast(source: str) -> str:
    tree = ast.parse(source, mode="exec")
    return ast.dump(
        tree,
        annotate_fields=True,
        include_attributes=False,
    )


def format_python311_source(
    source: str,
    *,
    line_length: int = 100,
) -> str:
    """Format recovered Python 3.11 module source without changing its AST."""

    try:
        import black
    except ImportError as error:
        raise SemanticGenerationError(
            "Python 3.11 source formatting requires black",
            version=(3, 11),
            code_name="<module>",
        ) from error

    before = _semantic_ast(source)

    try:
        formatted = black.format_str(
            source,
            mode=black.FileMode(line_length=line_length),
        ).rstrip()
    except Exception as error:
        raise SemanticGenerationError(
            "Python 3.11 source formatting failed",
            version=(3, 11),
            code_name="<module>",
        ) from error

    after = _semantic_ast(formatted)
    if before != after:
        raise SemanticGenerationError(
            "Source formatter changed the recovered AST",
            version=(3, 11),
            code_name="<module>",
        )

    return formatted
```

这里比较的是：

```text
ast.parse(ast.unparse(tree))
```

与：

```text
ast.parse(black.format_str(ast.unparse(tree)))
```

而不是直接比较内部恢复 AST 与重新解析 AST。这样可以避免原始内部 AST 中位置、类型注释或构造细节造成无意义差异。

## 6. 接入 `Python311BaseParser`

修改：

```text
decompyle3/parsers/p311/base.py
```

增加导入：

```python
from decompyle3.source_format import format_python311_source
```

在 `source = unparse(tree)` 后增加：

```python
source = unparse(tree)

if (
    isinstance(tree, ast.Module)
    and self.code_object.co_name == "<module>"
    and self.format_source
):
    source = format_python311_source(
        source,
        line_length=self.line_length,
    )
```

建议第一阶段只格式化根模块：

```python
self.code_object.co_name == "<module>"
```

根模块 AST 已经包含其中的类、函数和嵌套函数，因此一次格式化即可覆盖完整 `.py` 文件。

不要直接格式化以下局部解析模式：

- `eval`；
- `expr`；
- `lambda`；
- 单独函数 body；
- comprehension 内部片段。

这些模式不一定是完整 Python 模块，直接交给文件级格式化器可能产生错误。

## 7. 配置与命令行参数

建议为 decompile3 增加：

```bash
decompyle3 --format-source --line-length 100 example.pyc
```

CLI 参数参考：

```python
@click.option(
    "--format-source/--no-format-source",
    default=True,
    help="Format recovered CPython 3.11 module source.",
)
@click.option(
    "--line-length",
    type=click.IntRange(min=60, max=240),
    default=100,
    show_default=True,
    help="Preferred maximum source line length.",
)
```

参数需要沿以下调用链传递：

```text
decompyle3.bin.decompile.main_bin
    -> decompyle3.main.main
    -> decompile_file / code_deparse
    -> Python311SourceWalker
    -> Python311BaseParser
```

若第一阶段不希望修改完整调用链，也可以先为 CPython 3.11 根模块启用固定配置：

```python
format_source = True
line_length = 100
```

完成验证后再补充 CLI 参数。

## 8. 依赖配置

可以在 `pyproject.toml` 中增加格式化可选依赖：

```toml
[project.optional-dependencies]
dev = [
    "pre-commit",
    "pytest",
]

format = [
    "black",
]
```

安装：

```bash
pip install -e '.[format]'
```

如果 `--format-source` 默认开启，则应把 Black 作为运行时必选依赖，或者在缺少 Black 时给出明确错误。

需要注意：decompile3 当前声明支持多个 Python 运行版本。合入正式依赖前，应选择与项目支持版本范围兼容的 Black 版本，不应直接使用未经验证的版本范围。

## 9. 必须保持的语义约束

### 9.1 不得拆分字符串常量

`DEFAULT_MAP` 的第二个元素当前为一个完整字符串：

```text
model\s1_...toufa2_show.gimmodel\s1_...toufa1_show.gimmodel\s1_...toufa2.gim
```

该字符串看起来可能由多条路径拼接而成，但在字节码中只剩一个常量。反编译器无法证明原源码是否遗漏了逗号。

格式化前后必须满足：

```python
len(DEFAULT_MAP) == 3
```

不能为了缩短行长度，把该字符串拆成多个集合元素。否则会把集合从 3 个元素变成 5 个元素，属于执行语义修改。

Black 通常不会自动拆分单个长字符串，这正是期望行为。即使该行仍然超过配置行宽，也应优先保证常量内容不变。

### 9.2 不得重排字典

Python 3.7 及以上版本的字典保留插入顺序，该顺序可以被外部代码观察。

格式化器可以改变字典条目的换行和缩进，但不能按 key 排序。

### 9.3 不得重写集合表达式执行顺序

只有全部元素都是普通常量时，集合文字顺序通常不影响集合结果。若集合包含函数调用、属性访问或其他表达式，求值顺序可能影响副作用。

因此，不应在通用格式化阶段对集合元素排序。

### 9.4 不得使用 JSON 格式化器

这些对象是 Python 的 `set`、`dict` 和 `tuple`，不是 JSON。

不能使用：

```python
json.dumps(...)
```

JSON 不支持 `set` 和 `tuple`，还可能修改字符串、key 类型和对象结构。

## 10. 格式化失败策略

推荐采用 fail-closed 规则：

- 用户显式指定 `--format-source` 时，格式化失败应返回错误；
- 不应写出只完成了一半格式化的源码；
- 不应捕获所有异常后静默使用可能损坏的格式化结果；
- 格式化完成后仍执行现有语法和编译验证；
- AST 等价检查失败时必须拒绝结果。

如果要保留兼容模式，可以定义：

```text
--no-format-source
```

该模式直接使用 `ast.unparse()` 的原始输出。

## 11. 回归测试

建议新增：

```text
pytest/test_source_formatting311.py
```

### 11.1 最小测试源码

```python
SOURCE = r'''
DEFAULT_MAP = {
    "model\\first.gim",
    "model\\second.gimmodel\\third.gim",
    "model\\fourth.gim",
}

SPECIAL_MODEL = {
    "first": ("model/first", "bone001"),
    "second": ("model/second", "bone002"),
    "third": ("model/third", "bone003"),
}


class Example:
    def __init__(self, entityid=None):
        self.entityid = entityid
'''
```

### 11.2 AST 等价测试

```python
def test_formatter_preserves_ast():
    recovered = decompile_source(SOURCE, format_source=False)
    formatted = format_python311_source(recovered, line_length=88)

    assert ast.dump(
        ast.parse(recovered),
        include_attributes=False,
    ) == ast.dump(
        ast.parse(formatted),
        include_attributes=False,
    )
```

### 11.3 集合内容测试

```python
def test_set_literal_keeps_exact_members():
    original_ns = {}
    recovered_ns = {}

    exec(SOURCE, original_ns)
    exec(formatted_source, recovered_ns)

    assert recovered_ns["DEFAULT_MAP"] == original_ns["DEFAULT_MAP"]
    assert len(recovered_ns["DEFAULT_MAP"]) == 3
```

### 11.4 字典顺序测试

```python
def test_dict_order_is_preserved():
    assert list(recovered_ns["SPECIAL_MODEL"]) == list(
        original_ns["SPECIAL_MODEL"]
    )
```

### 11.5 格式测试

不要对完整 Black 输出做过度严格的字符串快照，因为格式化器版本可能产生细微布局差异。

建议只断言关键结构：

```python
assert "DEFAULT_MAP = {\n" in formatted_source
assert "SPECIAL_MODEL = {\n" in formatted_source
assert "entityid=None" in formatted_source
```

还应检查：

- 输出可以 `ast.parse()`；
- 输出可以 `compile()`；
- 格式化结果没有丢失结尾语句；
- 字典 key 顺序没有变化；
- 集合成员没有增加或减少；
- 字符串反斜杠内容保持一致；
- `--no-format-source` 可以返回未格式化结果。

## 12. 真实文件验收

修复后重新反编译：

```text
com.utils.helpers.original.marshal
```

检查输出文件中的：

```python
DEFAULT_MAP
bujianyue_special_model
```

验收要求：

1. `DEFAULT_MAP` 使用多行集合布局；
2. `bujianyue_special_model` 使用多行字典布局；
3. `DEFAULT_MAP` 仍然只有 3 个元素；
4. 字典 key 及其顺序不变；
5. 所有字符串常量内容不变；
6. 源码通过 AST 解析；
7. 源码通过 Python 3.11 编译；
8. 格式化前后 AST 完全一致；
9. 原始 code object 与新源码重编译 code object 的函数路径和签名没有新增差异；
10. decompile3 现有 CPython 3.11 测试全部通过。

建议执行：

```bash
PYTHONDONTWRITEBYTECODE=1 \
PYTHONPATH=. \
.venv311/bin/python -m pytest -q -p no:cacheprovider \
  pytest/test_source_formatting311.py \
  pytest/test_source_functional_differences311.py \
  pytest/test_try_loop_terminal_frontier311.py \
  pytest/test_exceptiontable311.py
```

## 13. 不建议采用的实现

不要使用以下方式：

1. 用正则表达式在 `{`、`}`、`,` 后直接插入换行；
2. 只根据字符串长度拆分 Python 字符串；
3. 将 Python 集合转换成 JSON 后重新输出；
4. 对字典或集合内容统一排序；
5. 修改 `ast.Set`、`ast.Dict` 节点内容来达到排版目的；
6. 直接依赖私有的 `ast._Unparser`；
7. 只检查格式化结果能否编译，不比较格式化前后的 AST；
8. 针对 `DEFAULT_MAP`、变量名或具体业务文件编写特判。

这些方式可能改变字符串、元素数量、字典顺序或表达式求值顺序。

## 14. 最终验收标准

修复完成需要同时满足：

- CPython 3.11 完整模块可以选择启用源码格式化；
- 大型集合和字典按照配置行宽换行；
- 默认参数格式符合 `entityid=None` 风格；
- 格式化前后 AST 完全一致；
- 字符串常量内容完全一致；
- 集合成员数量完全一致；
- 字典插入顺序完全一致；
- 格式化结果可以重新编译；
- 缺少格式化依赖或格式化失败时给出明确诊断；
- 可以通过 `--no-format-source` 禁用格式化；
- 真实 `com.utils.helpers` 样本和现有 CPython 3.11 回归测试全部通过。
