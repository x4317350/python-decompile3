# CPython 3.11 标准库与真实项目回归报告

> 本文件由 `test/bytecode_3.11/run_realworld_regression.py` 自动生成，
> 记录阶段 8 的固定环境宽度审计，不等同于全量支持声明。

## 环境

- Runtime：3.11.9
- Platform：darwin
- 输入摘要：`ab4cac873938ff835d81f1be7598b859dd7ea033c6e30d1ebeb1e44a4fa06312`

第三方版本：

- `attrs`：26.1.0
- `click`：8.4.2
- `packaging`：26.2
- `platformdirs`：4.11.0
- `pluggy`：1.6.0
- `pytest`：9.1.1

## 汇总

- 输入文件数：604
- 成功反编译数：331
- 语法验证成功数：331
- 语法失败数：0
- fail-closed 数：273
- malformed/unsupported input 数：0
- 未包装崩溃数：0
- 行为一致数：6
- 行为不一致数：0
- 行为输入缺失数：0
- 首次失败 opcode：`FOR_ITER`
- 首次失败 shape：`realworld_comprehension_and_iterator_protocol`

## 分组

| 分组 | 输入 | 反编译成功 | 语法成功 | 语法失败 | fail-closed | 输入不支持 | 未包装崩溃 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| stdlib | 338 | 159 | 159 | 0 | 179 | 0 | 0 |
| project | 115 | 82 | 82 | 0 | 33 | 0 | 0 |
| third_party | 151 | 90 | 90 | 0 | 61 | 0 | 0 |

## Fail-closed 分类

| Shape | 数量 |
| --- | ---: |
| `realworld_comprehension_and_iterator_protocol` | 124 |
| `realworld_exception_cleanup_control_transfer` | 25 |
| `realworld_function_object_flow` | 15 |
| `realworld_recursive_structure` | 33 |
| `realworld_with_control_transfer` | 76 |

## 行为探针

| 探针 | 分组 | 状态 |
| --- | --- | --- |
| `stdlib_keyword` | stdlib | consistent |
| `stdlib_colorsys` | stdlib | consistent |
| `stdlib_hmac` | stdlib | consistent |
| `project_util` | project | consistent |
| `packaging_structures` | third_party | consistent |
| `click_utils` | third_party | consistent |

## 结论

- 本轮没有把失败 traceback 当作成功；所有反编译失败均需映射到 shape 矩阵或输入分类。
- `unexpected_crash` 必须为 0；递归耗尽等内部异常必须转换为带版本和 code object 上下文的 fail-closed 错误。
- 本报告反映固定运行时和固定第三方版本的结果；通过率不能解释为对整个标准库或任意第三方包的完整支持。
