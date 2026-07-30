# CPython 3.11 Opcode 四层覆盖报告

> 本文件由 `test/bytecode_3.11/generate_opcode_matrix.py` 自动生成，
> 请勿手工修改。

## 目标与来源

- Implementation：CPython
- Python：3.11.9
- Cache tag：`cpython-311`
- Magic：`a70d0d0a` / `3495`
- Opcode inventory：110/110
- CPython/xdis 表一致：是

## Corpus 基线

- 源文件：23
- Code object：131
- Raw opcode：110/110
- Normalized original opcode：108/110

## 四层状态汇总

| 层级 | pass | internal | fail-closed | N/A | missing |
| --- | ---: | ---: | ---: | ---: | ---: |
| scanner | 110 | 0 | 0 | 0 | 0 |
| normalizer | 0 | 0 | 0 | 0 | 110 |
| parser | 0 | 0 | 0 | 0 | 110 |
| behavior | 0 | 0 | 0 | 0 | 110 |

截至阶段 3，逐 opcode 四层正式状态仍从 `missing`
开始归因。Corpus 中观察到指令不等同于完成 Scanner、Normalizer、
Parser 和行为验证。

## Opcode 明细

| 编号 | Opcode | 类别 | Raw | Normalized | Scanner | Normalizer | Parser | Behavior | Fixture |
| ---: | --- | --- | :---: | :---: | --- | --- | --- | --- | --- |
| 0 | `CACHE` | internal | 是 | 否 | pass | missing | missing | missing | `test/simple_source/311/00_expressions.py` |
| 1 | `POP_TOP` | statement_protocol | 是 | 是 | pass | missing | missing | missing | `test/simple_source/311/00_expressions.py` |
| 2 | `PUSH_NULL` | internal | 是 | 是 | pass | missing | missing | missing | `test/simple_source/311/00_expressions.py` |
| 9 | `NOP` | statement_protocol | 是 | 是 | pass | missing | missing | missing | `test/simple_source/311/01_functions_classes.py` |
| 10 | `UNARY_POSITIVE` | expression | 是 | 是 | pass | missing | missing | missing | `test/bytecode_3.11/opcode_fixtures/expressions/unary_positive.py` |
| 11 | `UNARY_NEGATIVE` | expression | 是 | 是 | pass | missing | missing | missing | `test/simple_source/311/00_expressions.py` |
| 12 | `UNARY_NOT` | expression | 是 | 是 | pass | missing | missing | missing | `test/bytecode_3.11/opcode_fixtures/expressions/unary_not.py` |
| 15 | `UNARY_INVERT` | expression | 是 | 是 | pass | missing | missing | missing | `test/simple_source/311/00_expressions.py` |
| 25 | `BINARY_SUBSCR` | expression | 是 | 是 | pass | missing | missing | missing | `test/simple_source/311/00_expressions.py` |
| 30 | `GET_LEN` | expression | 是 | 是 | pass | missing | missing | missing | `test/simple_source/311/06_match.py` |
| 31 | `MATCH_MAPPING` | match | 是 | 是 | pass | missing | missing | missing | `test/simple_source/311/06_match.py` |
| 32 | `MATCH_SEQUENCE` | match | 是 | 是 | pass | missing | missing | missing | `test/simple_source/311/06_match.py` |
| 33 | `MATCH_KEYS` | match | 是 | 是 | pass | missing | missing | missing | `test/simple_source/311/06_match.py` |
| 35 | `PUSH_EXC_INFO` | exception | 是 | 是 | pass | missing | missing | missing | `test/simple_source/311/05_exceptions_with.py` |
| 36 | `CHECK_EXC_MATCH` | exception | 是 | 是 | pass | missing | missing | missing | `test/simple_source/311/05_exceptions_with.py` |
| 37 | `CHECK_EG_MATCH` | exception | 是 | 是 | pass | missing | missing | missing | `test/simple_source/311/07_exception_group.py` |
| 49 | `WITH_EXCEPT_START` | exception | 是 | 是 | pass | missing | missing | missing | `test/simple_source/311/05_exceptions_with.py` |
| 50 | `GET_AITER` | generator_async | 是 | 是 | pass | missing | missing | missing | `test/simple_source/311/04_generators_async.py` |
| 51 | `GET_ANEXT` | generator_async | 是 | 是 | pass | missing | missing | missing | `test/simple_source/311/04_generators_async.py` |
| 52 | `BEFORE_ASYNC_WITH` | exception | 是 | 是 | pass | missing | missing | missing | `test/simple_source/311/05_exceptions_with.py` |
| 53 | `BEFORE_WITH` | exception | 是 | 是 | pass | missing | missing | missing | `test/simple_source/311/05_exceptions_with.py` |
| 54 | `END_ASYNC_FOR` | exception | 是 | 是 | pass | missing | missing | missing | `test/simple_source/311/04_generators_async.py` |
| 60 | `STORE_SUBSCR` | store | 是 | 是 | pass | missing | missing | missing | `test/simple_source/311/00_expressions.py` |
| 61 | `DELETE_SUBSCR` | delete | 是 | 是 | pass | missing | missing | missing | `test/simple_source/311/00_expressions.py` |
| 68 | `GET_ITER` | statement_protocol | 是 | 是 | pass | missing | missing | missing | `test/simple_source/311/01_functions_classes.py` |
| 69 | `GET_YIELD_FROM_ITER` | generator_async | 是 | 是 | pass | missing | missing | missing | `test/simple_source/311/04_generators_async.py` |
| 70 | `PRINT_EXPR` | statement_protocol | 是 | 是 | pass | missing | missing | missing | `test/bytecode_3.11/opcode_fixtures/internal/print_expr.py` |
| 71 | `LOAD_BUILD_CLASS` | call_function_class | 是 | 是 | pass | missing | missing | missing | `test/simple_source/311/01_functions_classes.py` |
| 74 | `LOAD_ASSERTION_ERROR` | load | 是 | 是 | pass | missing | missing | missing | `test/bytecode_3.11/opcode_fixtures/statements/load_assertion_error.py` |
| 75 | `RETURN_GENERATOR` | generator_async | 是 | 是 | pass | missing | missing | missing | `test/simple_source/311/03_comprehensions.py` |
| 82 | `LIST_TO_TUPLE` | collection | 是 | 是 | pass | missing | missing | missing | `test/bytecode_3.11/opcode_fixtures/collections/list_to_tuple.py` |
| 83 | `RETURN_VALUE` | statement_protocol | 是 | 是 | pass | missing | missing | missing | `test/simple_source/311/00_expressions.py` |
| 84 | `IMPORT_STAR` | import | 是 | 是 | pass | missing | missing | missing | `test/bytecode_3.11/opcode_fixtures/imports/import_star.py` |
| 85 | `SETUP_ANNOTATIONS` | statement_protocol | 是 | 否 | pass | missing | missing | missing | `test/bytecode_3.11/opcode_fixtures/scope/setup_annotations.py` |
| 86 | `YIELD_VALUE` | generator_async | 是 | 是 | pass | missing | missing | missing | `test/simple_source/311/03_comprehensions.py` |
| 87 | `ASYNC_GEN_WRAP` | generator_async | 是 | 是 | pass | missing | missing | missing | `test/simple_source/311/04_generators_async.py` |
| 88 | `PREP_RERAISE_STAR` | exception | 是 | 是 | pass | missing | missing | missing | `test/simple_source/311/07_exception_group.py` |
| 89 | `POP_EXCEPT` | exception | 是 | 是 | pass | missing | missing | missing | `test/simple_source/311/05_exceptions_with.py` |
| 90 | `STORE_NAME` | store | 是 | 是 | pass | missing | missing | missing | `test/simple_source/311/00_expressions.py` |
| 91 | `DELETE_NAME` | delete | 是 | 是 | pass | missing | missing | missing | `test/simple_source/311/09_straight_line.py` |
| 92 | `UNPACK_SEQUENCE` | collection | 是 | 是 | pass | missing | missing | missing | `test/simple_source/311/02_control_flow.py` |
| 93 | `FOR_ITER` | control_flow | 是 | 是 | pass | missing | missing | missing | `test/simple_source/311/01_functions_classes.py` |
| 94 | `UNPACK_EX` | collection | 是 | 是 | pass | missing | missing | missing | `test/simple_source/311/06_match.py` |
| 95 | `STORE_ATTR` | store | 是 | 是 | pass | missing | missing | missing | `test/simple_source/311/01_functions_classes.py` |
| 96 | `DELETE_ATTR` | delete | 是 | 是 | pass | missing | missing | missing | `test/bytecode_3.11/opcode_fixtures/scope/delete_attr.py` |
| 97 | `STORE_GLOBAL` | store | 是 | 是 | pass | missing | missing | missing | `test/bytecode_3.11/opcode_fixtures/scope/store_global.py` |
| 98 | `DELETE_GLOBAL` | delete | 是 | 是 | pass | missing | missing | missing | `test/bytecode_3.11/opcode_fixtures/scope/delete_global.py` |
| 99 | `SWAP` | statement_protocol | 是 | 是 | pass | missing | missing | missing | `test/simple_source/311/00_expressions.py` |
| 100 | `LOAD_CONST` | load | 是 | 是 | pass | missing | missing | missing | `test/simple_source/311/00_expressions.py` |
| 101 | `LOAD_NAME` | load | 是 | 是 | pass | missing | missing | missing | `test/simple_source/311/01_functions_classes.py` |
| 102 | `BUILD_TUPLE` | collection | 是 | 是 | pass | missing | missing | missing | `test/simple_source/311/00_expressions.py` |
| 103 | `BUILD_LIST` | collection | 是 | 是 | pass | missing | missing | missing | `test/simple_source/311/00_expressions.py` |
| 104 | `BUILD_SET` | collection | 是 | 是 | pass | missing | missing | missing | `test/simple_source/311/00_expressions.py` |
| 105 | `BUILD_MAP` | collection | 是 | 是 | pass | missing | missing | missing | `test/simple_source/311/00_expressions.py` |
| 106 | `LOAD_ATTR` | load | 是 | 是 | pass | missing | missing | missing | `test/simple_source/311/01_functions_classes.py` |
| 107 | `COMPARE_OP` | expression | 是 | 是 | pass | missing | missing | missing | `test/simple_source/311/00_expressions.py` |
| 108 | `IMPORT_NAME` | import | 是 | 是 | pass | missing | missing | missing | `test/simple_source/311/01_functions_classes.py` |
| 109 | `IMPORT_FROM` | import | 是 | 是 | pass | missing | missing | missing | `test/simple_source/311/01_functions_classes.py` |
| 110 | `JUMP_FORWARD` | control_flow | 是 | 是 | pass | missing | missing | missing | `test/simple_source/311/00_expressions.py` |
| 111 | `JUMP_IF_FALSE_OR_POP` | control_flow | 是 | 是 | pass | missing | missing | missing | `test/simple_source/311/00_expressions.py` |
| 112 | `JUMP_IF_TRUE_OR_POP` | control_flow | 是 | 是 | pass | missing | missing | missing | `test/simple_source/311/02_control_flow.py` |
| 114 | `POP_JUMP_FORWARD_IF_FALSE` | control_flow | 是 | 是 | pass | missing | missing | missing | `test/simple_source/311/02_control_flow.py` |
| 115 | `POP_JUMP_FORWARD_IF_TRUE` | control_flow | 是 | 是 | pass | missing | missing | missing | `test/simple_source/311/02_control_flow.py` |
| 116 | `LOAD_GLOBAL` | load | 是 | 是 | pass | missing | missing | missing | `test/simple_source/311/00_expressions.py` |
| 117 | `IS_OP` | expression | 是 | 是 | pass | missing | missing | missing | `test/simple_source/311/00_expressions.py` |
| 118 | `CONTAINS_OP` | expression | 是 | 是 | pass | missing | missing | missing | `test/simple_source/311/00_expressions.py` |
| 119 | `RERAISE` | exception | 是 | 是 | pass | missing | missing | missing | `test/simple_source/311/05_exceptions_with.py` |
| 120 | `COPY` | statement_protocol | 是 | 是 | pass | missing | missing | missing | `test/simple_source/311/00_expressions.py` |
| 122 | `BINARY_OP` | expression | 是 | 是 | pass | missing | missing | missing | `test/simple_source/311/00_expressions.py` |
| 123 | `SEND` | generator_async | 是 | 是 | pass | missing | missing | missing | `test/simple_source/311/04_generators_async.py` |
| 124 | `LOAD_FAST` | load | 是 | 是 | pass | missing | missing | missing | `test/simple_source/311/00_expressions.py` |
| 125 | `STORE_FAST` | store | 是 | 是 | pass | missing | missing | missing | `test/simple_source/311/00_expressions.py` |
| 126 | `DELETE_FAST` | delete | 是 | 是 | pass | missing | missing | missing | `test/simple_source/311/05_exceptions_with.py` |
| 128 | `POP_JUMP_FORWARD_IF_NOT_NONE` | control_flow | 是 | 是 | pass | missing | missing | missing | `test/simple_source/311/02_control_flow.py` |
| 129 | `POP_JUMP_FORWARD_IF_NONE` | control_flow | 是 | 是 | pass | missing | missing | missing | `test/simple_source/311/02_control_flow.py` |
| 130 | `RAISE_VARARGS` | exception | 是 | 是 | pass | missing | missing | missing | `test/simple_source/311/07_exception_group.py` |
| 131 | `GET_AWAITABLE` | generator_async | 是 | 是 | pass | missing | missing | missing | `test/simple_source/311/04_generators_async.py` |
| 132 | `MAKE_FUNCTION` | call_function_class | 是 | 是 | pass | missing | missing | missing | `test/simple_source/311/00_expressions.py` |
| 133 | `BUILD_SLICE` | collection | 是 | 是 | pass | missing | missing | missing | `test/simple_source/311/00_expressions.py` |
| 134 | `JUMP_BACKWARD_NO_INTERRUPT` | control_flow | 是 | 是 | pass | missing | missing | missing | `test/simple_source/311/04_generators_async.py` |
| 135 | `MAKE_CELL` | internal | 是 | 是 | pass | missing | missing | missing | `test/simple_source/311/01_functions_classes.py` |
| 136 | `LOAD_CLOSURE` | load | 是 | 是 | pass | missing | missing | missing | `test/simple_source/311/01_functions_classes.py` |
| 137 | `LOAD_DEREF` | load | 是 | 是 | pass | missing | missing | missing | `test/simple_source/311/01_functions_classes.py` |
| 138 | `STORE_DEREF` | store | 是 | 是 | pass | missing | missing | missing | `test/simple_source/311/01_functions_classes.py` |
| 139 | `DELETE_DEREF` | delete | 是 | 是 | pass | missing | missing | missing | `test/bytecode_3.11/opcode_fixtures/scope/delete_deref.py` |
| 140 | `JUMP_BACKWARD` | control_flow | 是 | 是 | pass | missing | missing | missing | `test/simple_source/311/01_functions_classes.py` |
| 142 | `CALL_FUNCTION_EX` | call_function_class | 是 | 是 | pass | missing | missing | missing | `test/simple_source/311/01_functions_classes.py` |
| 144 | `EXTENDED_ARG` | internal | 是 | 是 | pass | missing | missing | missing | `test/simple_source/311/08_imports_unpacking.py` |
| 145 | `LIST_APPEND` | collection | 是 | 是 | pass | missing | missing | missing | `test/simple_source/311/03_comprehensions.py` |
| 146 | `SET_ADD` | collection | 是 | 是 | pass | missing | missing | missing | `test/simple_source/311/03_comprehensions.py` |
| 147 | `MAP_ADD` | collection | 是 | 是 | pass | missing | missing | missing | `test/simple_source/311/03_comprehensions.py` |
| 148 | `LOAD_CLASSDEREF` | load | 是 | 是 | pass | missing | missing | missing | `test/bytecode_3.11/opcode_fixtures/scope/load_classderef.py` |
| 149 | `COPY_FREE_VARS` | internal | 是 | 是 | pass | missing | missing | missing | `test/simple_source/311/01_functions_classes.py` |
| 151 | `RESUME` | internal | 是 | 是 | pass | missing | missing | missing | `test/simple_source/311/00_expressions.py` |
| 152 | `MATCH_CLASS` | match | 是 | 是 | pass | missing | missing | missing | `test/simple_source/311/06_match.py` |
| 155 | `FORMAT_VALUE` | statement_protocol | 是 | 是 | pass | missing | missing | missing | `test/simple_source/311/00_expressions.py` |
| 156 | `BUILD_CONST_KEY_MAP` | collection | 是 | 是 | pass | missing | missing | missing | `test/simple_source/311/00_expressions.py` |
| 157 | `BUILD_STRING` | collection | 是 | 是 | pass | missing | missing | missing | `test/simple_source/311/00_expressions.py` |
| 160 | `LOAD_METHOD` | call_function_class | 是 | 是 | pass | missing | missing | missing | `test/simple_source/311/01_functions_classes.py` |
| 162 | `LIST_EXTEND` | collection | 是 | 是 | pass | missing | missing | missing | `test/simple_source/311/00_expressions.py` |
| 163 | `SET_UPDATE` | collection | 是 | 是 | pass | missing | missing | missing | `test/bytecode_3.11/opcode_fixtures/collections/set_update.py` |
| 164 | `DICT_MERGE` | collection | 是 | 是 | pass | missing | missing | missing | `test/simple_source/311/01_functions_classes.py` |
| 165 | `DICT_UPDATE` | collection | 是 | 是 | pass | missing | missing | missing | `test/simple_source/311/06_match.py` |
| 166 | `PRECALL` | internal | 是 | 是 | pass | missing | missing | missing | `test/simple_source/311/00_expressions.py` |
| 171 | `CALL` | call_function_class | 是 | 是 | pass | missing | missing | missing | `test/simple_source/311/00_expressions.py` |
| 172 | `KW_NAMES` | internal | 是 | 是 | pass | missing | missing | missing | `test/simple_source/311/00_expressions.py` |
| 173 | `POP_JUMP_BACKWARD_IF_NOT_NONE` | control_flow | 是 | 是 | pass | missing | missing | missing | `test/simple_source/311/02_control_flow.py` |
| 174 | `POP_JUMP_BACKWARD_IF_NONE` | control_flow | 是 | 是 | pass | missing | missing | missing | `test/simple_source/311/02_control_flow.py` |
| 175 | `POP_JUMP_BACKWARD_IF_FALSE` | control_flow | 是 | 是 | pass | missing | missing | missing | `test/simple_source/311/03_comprehensions.py` |
| 176 | `POP_JUMP_BACKWARD_IF_TRUE` | control_flow | 是 | 是 | pass | missing | missing | missing | `test/simple_source/311/02_control_flow.py` |

## 尚未触达

### Raw corpus

```text
```

### Normalized corpus

```text
CACHE
SETUP_ANNOTATIONS
```

## 状态定义

- `pass`：已实现并通过该层验证；
- `internal_consumed`：由内部协议消费，不直接生成 AST；
- `unsupported_fail_closed`：明确不支持并有稳定错误测试；
- `not_applicable`：该层不适用；
- `missing`：尚未完成实现或逐项测试归因。
