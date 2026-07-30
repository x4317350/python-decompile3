# CPython 3.11 指令组合覆盖报告

> 本文件由 `test/bytecode_3.11/generate_opcode_matrix.py` 自动生成，
> 请勿手工修改。

## 汇总

- Shape inventory：31
- pass：30
- internal_consumed：0
- unsupported_fail_closed：1
- not_applicable：0
- missing：0

## Shape 明细

| Shape | 类别 | 状态 | Fixture | 预期错误 | 测试数 |
| --- | --- | --- | --- | --- | ---: |
| `nested_and_or` | expression | pass | `—` | `—` | 2 |
| `mixed_short_circuit_return` | expression | pass | `—` | `—` | 2 |
| `short_circuit_evaluation_order` | expression | pass | `—` | `—` | 2 |
| `conditional_expression` | expression | pass | `test/simple_source/311/02_control_flow.py` | `—` | 2 |
| `chained_comparison` | expression | pass | `—` | `—` | 2 |
| `explicit_if_multiple_return` | control_flow | pass | `—` | `—` | 2 |
| `if_elif_else` | control_flow | pass | `test/simple_source/311/02_control_flow.py` | `—` | 2 |
| `for_break_continue_else` | control_flow | pass | `test/simple_source/311/02_control_flow.py` | `—` | 2 |
| `while_break_continue_else` | control_flow | pass | `test/simple_source/311/02_control_flow.py` | `—` | 2 |
| `irreducible_control_flow` | control_flow | unsupported_fail_closed | `—` | `IrreducibleControlFlowError` | 2 |
| `nested_comprehension_filter` | comprehension | pass | `test/simple_source/311/03_comprehensions.py` | `—` | 2 |
| `generator_and_yield_from` | generator_async | pass | `test/simple_source/311/04_generators_async.py` | `—` | 2 |
| `coroutine_await` | generator_async | pass | `test/simple_source/311/04_generators_async.py` | `—` | 2 |
| `async_for` | generator_async | pass | `test/simple_source/311/04_generators_async.py` | `—` | 2 |
| `try_except_else_finally` | exception | pass | `test/simple_source/311/05_exceptions_with.py` | `—` | 2 |
| `with_statement` | exception | pass | `test/simple_source/311/05_exceptions_with.py` | `—` | 2 |
| `async_with` | exception | pass | `test/simple_source/311/05_exceptions_with.py` | `—` | 2 |
| `except_star_basic` | exception_group | pass | `test/simple_source/311/07_exception_group.py` | `—` | 2 |
| `except_star_with_else` | exception_group | pass | `—` | `—` | 2 |
| `except_star_with_finally` | exception_group | pass | `—` | `—` | 2 |
| `match_patterns_and_guards` | match | pass | `test/simple_source/311/06_match.py` | `—` | 2 |
| `single_mode_print_expr` | compile_mode | pass | `test/bytecode_3.11/opcode_fixtures/internal/print_expr.py` | `—` | 2 |
| `extended_arg` | internal | pass | `—` | `—` | 2 |
| `compound_assert_condition` | statement | pass | `—` | `—` | 2 |
| `closure_class_scope` | scope | pass | `test/bytecode_3.11/opcode_fixtures/scope/load_classderef.py` | `—` | 3 |
| `variable_annotations` | scope | pass | `test/bytecode_3.11/opcode_fixtures/scope/setup_annotations.py` | `—` | 3 |
| `assert_statement` | statement | pass | `test/bytecode_3.11/opcode_fixtures/statements/load_assertion_error.py` | `—` | 3 |
| `import_star` | import | pass | `test/bytecode_3.11/opcode_fixtures/imports/import_star.py` | `—` | 3 |
| `starred_collection_build` | collection | pass | `test/bytecode_3.11/opcode_fixtures/collections/list_to_tuple.py` | `—` | 3 |
| `incremental_mapping_build` | collection | pass | `test/simple_source/311/08_imports_unpacking.py` | `—` | 2 |
| `scope_deletion` | scope | pass | `test/bytecode_3.11/opcode_fixtures/scope/delete_deref.py` | `—` | 3 |

## Missing

```text
```

## Fail-closed

- `irreducible_control_flow`：`IrreducibleControlFlowError`；Artificial irreducible graphs are rejected instead of guessed. Phase 7 retains the explicit IrreducibleControlFlowError boundary after auditing all known unsupported shapes.
