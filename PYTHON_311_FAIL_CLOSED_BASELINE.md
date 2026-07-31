# CPython 3.11 Fail-closed Shape 阶段 0 基线

> 本文件由 `test/bytecode_3.11/build_fail_closed_baseline.py` 生成。
> 计数来自固定环境中的 604 文件真实语料重放；人工不可约 CFG
> 是独立的安全边界，不包含在 401 个真实语料失败中。

## 汇总

- 输入文件：604
- 成功反编译：203
- fail-closed：401
- 真实语料失败家族：9
- 人工安全边界：1
- 输入摘要：`8b69da10c639757a77c33fe575a95f5c9cd7d4ebd84e3be835e181a024b9ac62`

## 修复顺序

| 阶段 | Shape | 基线失败 | 风险 | 主要 opcode | 处置 |
| ---: | --- | ---: | --- | --- | --- |
| 1 | `realworld_unpack_assignment` | 2 | medium | `UNPACK_SEQUENCE`×2 | `recover_or_split_until_zero` |
| 2 | `realworld_import_protocol` | 6 | medium | `IMPORT_FROM`×6 | `recover_or_split_until_zero` |
| 3 | `realworld_exception_cleanup_control_transfer` | 57 | very_high | `RERAISE`×29、`<none>`×15、`POP_EXCEPT`×8、`PUSH_EXC_INFO`×3 | `recover_or_split_until_zero` |
| 4 | `realworld_call_and_expression_stack` | 217 | very_high | `<none>`×71、`SWAP_STACK`×68、`POP_TOP`×18、`CALL`×17 | `recover_or_split_until_zero` |
| 5 | `realworld_function_object_flow` | 9 | medium | `STORE_ATTR`×5、`CALL`×4 | `recover_or_split_until_zero` |
| 6 | `realworld_comprehension_and_iterator_protocol` | 67 | very_high | `MAP_ADD`×28、`FOR_ITER`×26、`RETURN_GENERATOR`×11、`SET_ADD`×1 | `recover_or_split_until_zero` |
| 7 | `realworld_with_control_transfer` | 29 | high | `<none>`×29 | `recover_or_split_until_zero` |
| 8 | `realworld_recursive_structure` | 13 | high | `<none>`×13 | `recover_or_split_until_zero` |
| 9 | `realworld_match_boundary` | 1 | high | `<none>`×1 | `recover_or_split_until_zero` |
| 10 | `irreducible_control_flow` | 0 | security_boundary | — | `retain_safety_boundary` |

## 分项基线

### 1. `realworld_unpack_assignment`

- 基线失败：2
- 风险：`medium`
- 依赖：无
- 目标：Recover assignment and loop-target routing without allowing parser-only unpack markers to escape.
- 错误类型：`Python311ParseError`×2
- Opcode：`UNPACK_SEQUENCE`×2
- 主要错误签名：

  - `Expected an expression on the operand stack, found _UnpackItem (opcode UNPACK_SEQUENCE)`：1
  - `Unpacking loop target contains a non-store opcode (opcode UNPACK_SEQUENCE)`：1

代表输入：

- `stdlib:code.py`，code=`showsyntaxerror`，opcode=`UNPACK_SEQUENCE`
- `stdlib:multiprocessing/util.py`，code=`_run_after_forkers`，opcode=`UNPACK_SEQUENCE`

### 2. `realworld_import_protocol`

- 基线失败：6
- 风险：`medium`
- 依赖：无
- 目标：Bind IMPORT_FROM to its owning import transaction across intermediate stack operations.
- 错误类型：`Python311ParseError`×6
- Opcode：`IMPORT_FROM`×6
- 主要错误签名：

  - `IMPORT_FROM has no owning IMPORT_NAME (opcode IMPORT_FROM)`：6

代表输入：

- `project:decompyle3/scanners/pypy37.py`，code=`<module>`，opcode=`IMPORT_FROM`
- `project:decompyle3/scanners/pypy38.py`，code=`<module>`，opcode=`IMPORT_FROM`
- `project:decompyle3/semantics/customize311.py`，code=`<module>`，opcode=`IMPORT_FROM`
- `project:decompyle3/semantics/parser_error.py`，code=`<module>`，opcode=`IMPORT_FROM`
- `project:decompyle3/semantics/pysource.py`，code=`<module>`，opcode=`IMPORT_FROM`

### 3. `realworld_exception_cleanup_control_transfer`

- 基线失败：57
- 风险：`very_high`
- 依赖：无
- 目标：Structure exception-table cleanup and RERAISE/POP_EXCEPT transfers before expression fallback.
- 错误类型：`Python311ParseError`×57
- Opcode：`RERAISE`×29、`<none>`×15、`POP_EXCEPT`×8、`PUSH_EXC_INFO`×3、`SWAP_STACK`×2
- 主要错误签名：

  - `Unsupported phase-# opcode RERAISE (opcode RERAISE)`：29
  - `Finally suite has no normal-path jump ('<value>', offset #)`：15
  - `Unsupported phase-# opcode POP_EXCEPT (opcode POP_EXCEPT)`：8
  - `Unsupported phase-# opcode PUSH_EXC_INFO (opcode PUSH_EXC_INFO)`：3
  - `SWAP_STACK depth # is invalid (opcode SWAP_STACK)`：2

代表输入：

- `project:decompyle3/bin/decompile.py`，code=`main_bin`，opcode=`RERAISE`
- `project:decompyle3/controlflow/exceptiontable311.py`，code=`_parse_varint`，opcode=`RERAISE`
- `project:decompyle3/main.py`，code=`verify_source`，opcode=`RERAISE`
- `project:decompyle3/parsers/main.py`，code=`get_python_parser`，opcode=`RERAISE`
- `project:decompyle3/parsers/reduce_check/tryexcept.py`，code=`tryexcept`，opcode=`SWAP_STACK`

### 4. `realworld_call_and_expression_stack`

- 基线失败：217
- 风险：`very_high`
- 依赖：无
- 目标：Split the umbrella into precise call, stack, jump-expression, and generated-source shapes, then eliminate each residual.
- 错误类型：`Python311ParseError`×214、`SemanticGenerationError`×3
- Opcode：`<none>`×71、`SWAP_STACK`×68、`POP_TOP`×18、`CALL`×17、`LOAD_ATTR`×11、`STORE_FAST`×5、`POP_JUMP_FORWARD_IF_FALSE`×4、`POP_JUMP_FORWARD_IF_TRUE`×4
- 主要错误签名：

  - `SWAP_STACK depth # is invalid (opcode SWAP_STACK)`：68
  - `Expression produced # final stack values`：45
  - `Invalid expression instruction range #:#`：20
  - `Operand stack underflow (opcode POP_TOP)`：17
  - `Operand stack underflow (opcode CALL)`：10
  - `Operand stack underflow (opcode LOAD_ATTR)`：10
  - `Structured statement region left stack values (opcode CALL)`：6
  - `Operand stack underflow (opcode STORE_FAST)`：5

代表输入：

- `project:decompyle3/controlflow/dominators.py`，code=`<dictcomp>`，opcode=`POP_JUMP_FORWARD_IF_FALSE`
- `project:decompyle3/controlflow/exception_structures.py`，code=`_handler_has_match`，opcode=`POP_TOP`
- `project:decompyle3/controlflow/match_structures.py`，code=`<module>`，opcode=`—`
- `project:decompyle3/controlflow/structures.py`，code=`_combine_decision`，opcode=`LOAD_GLOBAL`
- `project:decompyle3/errors.py`，code=`add_error_context`，opcode=`—`

### 5. `realworld_function_object_flow`

- 基线失败：9
- 风险：`medium`
- 依赖：`realworld_call_and_expression_stack`
- 目标：Represent function values as expressions until their final name, attribute, subscript, or call consumer is known.
- 错误类型：`Python311ParseError`×9
- Opcode：`STORE_ATTR`×5、`CALL`×4
- 主要错误签名：

  - `A function definition is stored to a non-name target (opcode STORE_ATTR)`：5
  - `Expected an expression, found parser-only value _FunctionValue (opcode CALL)`：4

代表输入：

- `stdlib:_pyio.py`，code=`<module>`，opcode=`CALL`
- `stdlib:functools.py`，code=`lru_cache`，opcode=`STORE_ATTR`
- `stdlib:importlib/metadata/_collections.py`，code=`freeze`，opcode=`STORE_ATTR`
- `stdlib:importlib/metadata/_functools.py`，code=`method_cache`，opcode=`STORE_ATTR`
- `stdlib:importlib/resources/_legacy.py`，code=`<module>`，opcode=`CALL`

### 6. `realworld_comprehension_and_iterator_protocol`

- 基线失败：67
- 风险：`very_high`
- 依赖：`realworld_call_and_expression_stack`
- 目标：Recover nested iterator, filter, append/add, generator, and suspension protocols in arbitrary enclosing code objects.
- 错误类型：`Python311ParseError`×38、`UnsupportedPython311ControlFlow`×29
- Opcode：`MAP_ADD`×28、`FOR_ITER`×26、`RETURN_GENERATOR`×11、`SET_ADD`×1、`YIELD_VALUE`×1
- 主要错误签名：

  - `This opcode is not supported by the CPython #.# structure decompiler (opcode MAP_ADD)`：28
  - `Unsupported phase-# opcode FOR_ITER (opcode FOR_ITER)`：26
  - `Unsupported phase-# opcode RETURN_GENERATOR (opcode RETURN_GENERATOR)`：11
  - `This opcode is not supported by the CPython #.# structure decompiler (opcode SET_ADD)`：1
  - `Unsupported phase-# opcode YIELD_VALUE (opcode YIELD_VALUE)`：1

代表输入：

- `project:decompyle3/bin/decompile_code_type.py`，code=`main`，opcode=`FOR_ITER`
- `project:decompyle3/controlflow/cfg.py`，code=`format`，opcode=`FOR_ITER`
- `project:decompyle3/parsers/p37/base.py`，code=`customize_grammar_rules37`，opcode=`MAP_ADD`
- `project:decompyle3/parsers/p37/lambda_custom.py`，code=`customize_grammar_rules_lambda37`，opcode=`FOR_ITER`
- `project:decompyle3/parsers/p38/full_custom.py`，code=`customize_grammar_rules_full38`，opcode=`FOR_ITER`

### 7. `realworld_with_control_transfer`

- 基线失败：29
- 风险：`high`
- 依赖：`realworld_call_and_expression_stack`、`realworld_exception_cleanup_control_transfer`
- 目标：Recover return, yield, break, continue, and cleanup transfers inside with/async-with bodies.
- 错误类型：`Python311ParseError`×29
- Opcode：`<none>`×29
- 主要错误签名：

  - `Returning with-body is not one expression ('<value>', offset #)`：29

代表输入：

- `stdlib:_threading_local.py`，code=`<module>`，opcode=`—`
- `stdlib:_weakrefset.py`，code=`<module>`，opcode=`—`
- `stdlib:cProfile.py`，code=`<module>`，opcode=`—`
- `stdlib:codeop.py`，code=`<module>`，opcode=`—`
- `stdlib:concurrent/futures/_base.py`，code=`<module>`，opcode=`—`

### 8. `realworld_recursive_structure`

- 基线失败：13
- 风险：`high`
- 依赖：`realworld_call_and_expression_stack`、`realworld_comprehension_and_iterator_protocol`
- 目标：Replace unbounded recursive structure probing with bounded or iterative traversal while preserving cycle detection.
- 错误类型：`Python311ParseError`×13
- Opcode：`<none>`×13
- 主要错误签名：

  - `Parser311 recursion limit reached while structuring control flow`：13

代表输入：

- `project:decompyle3/parsers/reduce_check/ifelsestmt.py`，code=`<module>`，opcode=`—`
- `project:decompyle3/parsers/reduce_check/ifstmts_jump.py`，code=`<module>`，opcode=`—`
- `project:decompyle3/semantics/customize3.py`，code=`<module>`，opcode=`—`
- `project:decompyle3/semantics/gencomp.py`，code=`<module>`，opcode=`—`
- `stdlib:_markupbase.py`，code=`<module>`，opcode=`—`

### 9. `realworld_match_boundary`

- 基线失败：1
- 风险：`high`
- 依赖：`realworld_call_and_expression_stack`、`realworld_exception_cleanup_control_transfer`
- 目标：Recover canonical case fallthrough and body terminators using CFG dominance rather than source-order guessing.
- 错误类型：`Python311ParseError`×1
- Opcode：`<none>`×1
- 主要错误签名：

  - `Match case body has no structural terminator ('<value>', offset #)`：1

代表输入：

- `stdlib:tarfile.py`，code=`<module>`，opcode=`—`

### 10. `irreducible_control_flow`

- 基线失败：0
- 风险：`security_boundary`
- 依赖：无
- 目标：Keep artificial irreducible graphs fail-closed unless a semantics-preserving structured representation is proven.
- 错误类型：—
- Opcode：—
- 主要错误签名：

  - 无真实语料；由人工 CFG 契约覆盖。

代表输入：

- 无；参见人工不可约 CFG 单元测试。

## 阶段 0 约束

- 任一粗粒度家族只有在归档计数降为 0 后才可直接转为 `pass`；
- 部分修复必须把剩余失败拆成更精确、可复现的 shape；
- 每个新增子 shape 必须有最小 fixture、AST/语法验证和差分行为测试；
- 不得用放宽异常捕获、删除语料或修改分类顺序制造计数下降；
- 人工不可约 CFG 不计入 604 文件成功率，继续作为安全边界审查；
- 每阶段必须更新真实语料归档、shape 矩阵和发布策略。
