# CPython 3.11 指令组合覆盖报告

> 本文件由 `test/bytecode_3.11/generate_opcode_matrix.py` 自动生成，
> 请勿手工修改。

## 汇总

- Shape inventory：30
- pass：20
- internal_consumed：0
- unsupported_fail_closed：3
- not_applicable：0
- missing：7

## Shape 明细

| Shape | 类别 | 状态 | Fixture | 预期错误 | 测试数 |
| --- | --- | --- | --- | --- | ---: |
| `nested_and_or` | expression | pass | `—` | `—` | 1 |
| `mixed_short_circuit_return` | expression | pass | `—` | `—` | 1 |
| `short_circuit_evaluation_order` | expression | pass | `—` | `—` | 1 |
| `conditional_expression` | expression | pass | `test/simple_source/311/02_control_flow.py` | `—` | 1 |
| `chained_comparison` | expression | pass | `—` | `—` | 1 |
| `explicit_if_multiple_return` | control_flow | pass | `—` | `—` | 1 |
| `if_elif_else` | control_flow | pass | `test/simple_source/311/02_control_flow.py` | `—` | 1 |
| `for_break_continue_else` | control_flow | pass | `test/simple_source/311/02_control_flow.py` | `—` | 1 |
| `while_break_continue_else` | control_flow | pass | `test/simple_source/311/02_control_flow.py` | `—` | 1 |
| `irreducible_control_flow` | control_flow | unsupported_fail_closed | `—` | `IrreducibleControlFlowError` | 1 |
| `nested_comprehension_filter` | comprehension | pass | `test/simple_source/311/03_comprehensions.py` | `—` | 1 |
| `generator_and_yield_from` | generator_async | pass | `test/simple_source/311/04_generators_async.py` | `—` | 1 |
| `coroutine_await` | generator_async | pass | `test/simple_source/311/04_generators_async.py` | `—` | 1 |
| `async_for` | generator_async | pass | `test/simple_source/311/04_generators_async.py` | `—` | 1 |
| `try_except_else_finally` | exception | pass | `test/simple_source/311/05_exceptions_with.py` | `—` | 1 |
| `with_statement` | exception | pass | `test/simple_source/311/05_exceptions_with.py` | `—` | 1 |
| `async_with` | exception | pass | `test/simple_source/311/05_exceptions_with.py` | `—` | 1 |
| `except_star_basic` | exception_group | pass | `test/simple_source/311/07_exception_group.py` | `—` | 1 |
| `except_star_with_else` | exception_group | unsupported_fail_closed | `—` | `UnsupportedPython311ControlFlow` | 1 |
| `except_star_with_finally` | exception_group | unsupported_fail_closed | `—` | `UnsupportedPython311ControlFlow` | 1 |
| `match_patterns_and_guards` | match | pass | `test/simple_source/311/06_match.py` | `—` | 1 |
| `single_mode_print_expr` | compile_mode | pass | `—` | `—` | 1 |
| `extended_arg` | internal | pass | `—` | `—` | 1 |
| `closure_class_scope` | scope | missing | `test/simple_source/311/01_functions_classes.py` | `—` | 0 |
| `variable_annotations` | scope | missing | `—` | `—` | 0 |
| `assert_statement` | statement | missing | `—` | `—` | 0 |
| `import_star` | import | missing | `—` | `—` | 0 |
| `starred_collection_build` | collection | missing | `—` | `—` | 0 |
| `incremental_mapping_build` | collection | missing | `—` | `—` | 0 |
| `scope_deletion` | scope | missing | `—` | `—` | 0 |

## Missing

```text
closure_class_scope
variable_annotations
assert_statement
import_star
starred_collection_build
incremental_mapping_build
scope_deletion
```

## Fail-closed

- `irreducible_control_flow`：`IrreducibleControlFlowError`；Artificial irreducible graphs are rejected instead of guessed.
- `except_star_with_else`：`UnsupportedPython311ControlFlow`；The current parser rejects except* combined with an else suite.
- `except_star_with_finally`：`UnsupportedPython311ControlFlow`；The current parser rejects except* combined with an enclosing finally.
