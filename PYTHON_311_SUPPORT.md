# CPython 3.11 support

## Scope

`decompyle3` can decompile standard, on-disk `.pyc` files produced by
released CPython 3.11 versions. The first release target is semantic
equivalence: comments, redundant parentheses, literal spelling, and original
formatting cannot be reconstructed from bytecode.

The Python interpreter running `decompyle3` and the interpreter that produced
the target bytecode are separate versions:

| Target bytecode | Runtime used to run `decompyle3` |
|---|---|
| Python 3.7 or 3.8 | Python 3.7 or newer |
| CPython 3.11 | Python 3.11 or newer |

CPython 3.11 support includes:

- Modules, functions, lambdas, classes, decorators, annotations, closures,
  imports, calls, unpacking, f-strings, and expressions.
- `if`/`elif`/`else`, Boolean short-circuiting, `for`, `while`,
  `break`/`continue`, and loop `else`.
- List, set, and dict comprehensions; generator expressions; generators;
  `yield from`; coroutines; `await`; async comprehensions; and `async for`.
- `try`/`except`/`else`/`finally`, `with`, `async with`, and zero-cost
  exception-table control flow.
- `match`/`case` patterns and guards.
- The covered CPython 3.11 `except*` and `ExceptionGroup` protocol shapes.

The scanner preserves physical offsets and inline caches, then exposes a
separate normalized instruction stream. Unsupported input is rejected instead
of intentionally emitting guessed source.

## Coverage and release gate

The opcode matrix gives every one of CPython 3.11's 110 base opcodes an
explicit Scanner, Normalizer, Parser, and behavior status. This is an opcode
contract, not a claim that every possible multi-opcode control-flow shape is
recoverable.

<!-- BEGIN PYTHON311 RELEASE STATUS -->

当前发布门禁基线：

- `Opcode inventory: 110/110`
- `Scanner: 110/110`
- `Normalizer: 110/110 (102 pass, 8 internal_consumed)`
- `Parser pass: 102/110`
- `Parser internal_consumed: 8/110`
- `Parser unsupported_fail_closed: 0/110`
- `Parser missing: 0/110`
- `Behavior verified: 110/110`
- `Shape pass: 35`
- `Shape fail-closed: 5`
- `Shape missing: 0`
- 真实语料：335/604 成功反编译，269 项明确 fail-closed；
- 差分行为探针：6 项一致，0 项不一致；
- 全量测试允许的已解释 legacy skip：6 项。

<!-- END PYTHON311 RELEASE STATUS -->

The generated
`PYTHON_311_OPCODE_COVERAGE.md`, `PYTHON_311_SHAPE_COVERAGE.md`,
`PYTHON_311_REALWORLD_REGRESSION.md`, and
`PYTHON_311_RELEASE_GATE.md` reports contain the auditable details. CI rejects
missing entries, unapproved status changes, stale reports, behavior
mismatches, unexpected skips, and test failures.

## Command-line use

Decompile one file to standard output:

```console
python3.11 -m decompyle3.bin.decompile module.pyc
```

Decompile several files into a directory and syntax-check the generated
source:

```console
decompyle3 --output recovered --verify syntax first.pyc second.pyc
```

For recursive directory processing:

```console
decompyle3 --recurse --output recovered bytecode-directory
```

Batch processing continues after an individual input failure. Any failure
causes a non-zero command status. A partially written output is renamed with
the `_failed` suffix; a successful sibling input is still processed.

## Errors

Public failures derive from `decompyle3.errors.Decompyle3Error`. The release
error taxonomy includes:

- `UnsupportedVersionError`
- `UnsupportedFeatureError`
- `UnsupportedOpcodeError`
- `MalformedBytecodeError`
- `ControlFlowError`
- `ExceptionTableError`
- `ParserError`
- `SemanticGenerationError`
- `VerificationError`

CPython 3.11 pipeline errors carry the target version, code object name, and
physical bytecode offset when an offset is available.

## Known limitations

- PyPy 3.11, Cython, MicroPython, obfuscated, encrypted, packed, and manually
  edited bytecode are not supported.
- Live adaptive/specialized code objects are not accepted as ordinary disk
  `.pyc` input. The normalization layer can inspect covered live CPython 3.11
  specialization forms for tests, but this is not the disk-file contract.
- Python 3.11 standard-library coverage is a tested subset, not a claim that
  every standard-library module decompiles.
- `except*` combined with `else` and/or an enclosing `finally`, compound
  assertion conditions, import-star namespace behavior, uncommon stack
  rotations, and incrementally built mappings are covered by the current
  behavior corpus. This does not imply support for unrelated combinations of
  those instructions.
- The real-world audit retains explicit fail-closed boundaries for four broad
  families: comprehension/iterator protocols, advanced exception-cleanup
  transfers, recursive structures, and `with` control transfers.
- Artificial irreducible control-flow graphs are also rejected explicitly.
  Together with the four real-world families, this accounts for the five
  fail-closed entries in the current shape matrix.
- The `match` recovery path targets canonical CPython 3.11 compiler output.
  Artificial or ambiguous case/body boundaries are rejected.
- Source is rendered with `ast.unparse()`. Original whitespace, quote style,
  comments, and other non-semantic formatting are not preserved.
- Partial decompilation with `start_offset` or `stop_offset` is rejected for
  CPython 3.11. Slicing normalized instructions before CFG recovery can split
  an operand-producing expression, jump, or exception-table region, so the
  3.11 path fails closed until statement-span slicing is implemented.
- Syntax and recompilation checks are necessary but do not prove semantic
  equivalence. Behavior tests remain important for security-sensitive uses.

## Reproducing and reporting a problem

Reduce the failure to a small `minimal.py`, then create a deterministic
CPython 3.11 bytecode file:

```console
python3.11 -c "import py_compile; py_compile.compile('minimal.py', cfile='minimal.pyc', doraise=True)"
decompyle3 --output recovered.py --verify syntax minimal.pyc
python3.11 -m dis minimal.py
```

Include `minimal.py`, `minimal.pyc`, the full command output, both Python
versions (runtime and target), the `decompyle3` version, and the operating
system in the report. See `HOW-TO-REPORT-A-BUG.md` for the project policy.

## Distribution and GPL check

The project is distributed under GPL-3.0-or-later, as declared in
`pyproject.toml` and provided in `COPYING`. Source distributions include the
license and the corresponding Python sources.

When distributing a modified version, keep the copyright, license, and
warranty notices; clearly identify that the work was modified and its
modification date; license the covered work under GPL-3.0-or-later; and
provide the corresponding source through a method permitted by the license.
`COPYING` is authoritative.
