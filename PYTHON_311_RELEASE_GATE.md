# CPython 3.11 发布门禁

> 本文件由 `test/bytecode_3.11/run_release_gate.py` 生成，
> 与阶段 11 发布策略、覆盖矩阵和真实语料归档同步。

## 四层覆盖

- `Opcode inventory: 110/110`
- `Scanner: 110/110`
- `Normalizer: 110/110 (102 pass, 8 internal_consumed)`
- `Parser pass: 102/110`
- `Parser internal_consumed: 8/110`
- `Parser unsupported_fail_closed: 0/110`
- `Parser missing: 0/110`
- `Behavior verified: 110/110`
- `Shape pass: 45`
- `Shape fail-closed: 1`
- `Shape missing: 0`

## 固定环境

- Runtime：CPython 3.11.9
- `attrs`：26.1.0
- `click`：8.4.2
- `packaging`：26.2
- `platformdirs`：4.11.0
- `pluggy`：1.6.0
- `pytest`：9.1.1
- `spark-parser`：1.9.0
- `xdis`：6.3.0

## 真实语料归档

- 输入：605
- 成功反编译：605
- fail-closed：0
- 语法失败：0
- 未包装崩溃：0
- 行为一致：6
- 行为不一致：0

## 已审批的 fail-closed shape

- `irreducible_control_flow`

## 已解释的全量测试 skip

- `pytest/test_code_deparse.py::test_single_mode`：asssume Python 3.7 or 3.8
- `pytest/test_code_deparse.py::test_eval_mode`：asssume Python 3.7 or 3.8
- `pytest/test_code_deparse.py::test_lambda_mode`：asssume Python 3.7 or 3.8
- `pytest/test_deparse_offset.py::test_assign_stmts_with_offset`：Only works for Python 3.7 and 3.8
- `pytest/test_grammar.py::test_grammar`：Only works for Python 3.7 and 3.8
- `pytest/test_grammar.py::test_dup_rule`：Only works for Python 3.7 and 3.8

## CI 命令

```console
python test/bytecode_3.11/run_release_gate.py --check
python test/bytecode_3.11/generate.py --check
python test/bytecode_3.11/run_release_gate.py --pytest
```

任何 `missing`、未经审批的状态变化、行为不一致、报告过期、
新增 skip 或全量测试失败都会使门禁返回非零状态。
