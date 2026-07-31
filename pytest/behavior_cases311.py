"""Deterministic probes and inline shapes for CPython 3.11 behavior tests."""

from __future__ import annotations

import textwrap


def _probe(source):
    return textwrap.dedent(source).strip() + "\n"


FIXTURE_PROBES = {
    "test/simple_source/311/00_expressions.py": _probe(
        """
        _record("expressions", lambda: expressions(3, {}))
        _record("comparisons", lambda: comparisons(2, [1, 2, 3]))
        _record(
            "call_examples",
            lambda: call_examples(
                lambda *args, **kwargs: (args, kwargs),
                5,
            ),
        )
        """
    ),
    "test/simple_source/311/01_functions_classes.py": _probe(
        """
        _record(
            "combine",
            lambda: combine(2, 3, 4, 5, scale=2, bonus=6),
        )

        def _accumulator_state():
            instance = Accumulator.from_values([1, 2, 3])
            return instance.__dict__, instance.doubled

        def _counter_values():
            counter = make_counter(5)
            return counter(), counter(3)

        _record("accumulator", _accumulator_state)
        _record("counter", _counter_values)
        _record("lambda", lambda: make_scaler(4)(7))
        """
    ),
    "test/simple_source/311/02_control_flow.py": _probe(
        """
        _record(
            "classify",
            lambda: [classify(value) for value in (-2, 0, 3, 4)],
        )
        _record(
            "nested_conditions",
            lambda: [
                nested_conditions(False, 2, 3),
                nested_conditions(1, 0, 4),
                nested_conditions(1, 2, 4),
            ],
        )
        _record("loops", lambda: loops([1, 2, 0, 4]))
        _record("none_control", lambda: none_control(None, [1, None, 3]))
        _record("nested_loops", lambda: nested_loops([[1, 2], [3, -1]]))
        _record("while_continue", lambda: while_continue(7))
        """
    ),
    "test/simple_source/311/03_comprehensions.py": _probe(
        """
        _record(
            "comprehensions",
            lambda: comprehensions([[], [1, 2, 3], [-1, 4, 5]]),
        )
        _record("nested", lambda: nested_comprehension(5))
        _record("filtered_lambda", lambda: filtered_lambda(range(-2, 6)))
        _record("scope", lambda: comprehension_scope([1, 2, 3]))
        """
    ),
    "test/simple_source/311/04_generators_async.py": _probe(
        """
        def _echo_values():
            generator = echo([1, 2])
            values = [
                next(generator),
                generator.send(10),
                next(generator),
            ]
            try:
                generator.send(None)
            except StopIteration as stopped:
                values.append(stopped.value)
            return values

        class _AsyncValues:
            def __init__(self, values):
                self.values = iter(values)

            def __aiter__(self):
                return self

            async def __anext__(self):
                try:
                    return next(self.values)
                except StopIteration:
                    raise StopAsyncIteration

        async def _ready(value):
            return value

        async def _collect(iterator):
            return [value async for value in iterator]

        async def _async_values():
            return (
                await consume(_AsyncValues([0, 1, 2, 3])),
                await await_value(_ready(42)),
                await async_filtered(_AsyncValues([-1, 1, 2, 3])),
                await _collect(async_numbers(4)),
                await _collect(async_transform([_ready(5), _ready(8)])),
            )

        _record("numbers", lambda: list(numbers(5)))
        _record("delegating", lambda: list(delegating([2, 4, 6])))
        _record("echo", _echo_values)
        _record_async("async", _async_values)
        """
    ),
    "test/simple_source/311/05_exceptions_with.py": _probe(
        """
        class _Resource:
            def __init__(self, value, events, name, suppress=False):
                self.value = value
                self.events = events
                self.name = name
                self.suppress = suppress

            def __enter__(self):
                self.events.append(("enter", self.name))
                return self

            def __exit__(self, kind, value, traceback):
                name = None if kind is None else kind.__name__
                self.events.append(("exit", self.name, name))
                return self.suppress

            def fail(self):
                raise ValueError("context failed")

        class _AsyncResource(_Resource):
            async def __aenter__(self):
                self.events.append(("aenter", self.name))
                return self

            async def __aexit__(self, kind, value, traceback):
                name = None if kind is None else kind.__name__
                self.events.append(("aexit", self.name, name))
                return self.suppress

        class _AsyncValues:
            def __init__(self, values):
                self.values = iter(values)

            def __aiter__(self):
                return self

            async def __anext__(self):
                try:
                    return next(self.values)
                except StopIteration:
                    raise StopAsyncIteration

        def _sync_contexts():
            events = []
            value = use_context(_Resource(7, events, "one"))
            pair = use_two_contexts(
                _Resource(2, events, "left"),
                _Resource(5, events, "right"),
            )
            nested = use_nested_context(
                _Resource(3, events, "outer"),
                _Resource(4, events, "inner"),
            )
            return value, pair, nested, events

        async def _async_contexts():
            events = []
            value = await use_async_context(
                _AsyncResource(11, events, "async")
            )
            consumed = await consume_async(_AsyncValues([1, 2, 3]))
            broken = await consume_until_negative(
                _AsyncValues([1, -1, 3])
            )
            return value, consumed, broken, events

        _record(
            "guarded",
            lambda: (
                guarded_division(8, 2),
                guarded_division(8, 0),
            ),
        )
        _record(
            "nested_exception",
            lambda: [nested_exception(value) for value in ("12", None, "x")],
        )
        _record("cleanup", lambda: cleanup_only([]))
        _record("finally", lambda: finally_return("value"))
        _record("sync_contexts", _sync_contexts)
        _record_async("async_contexts", _async_contexts)
        """
    ),
    "test/simple_source/311/06_match.py": _probe(
        """
        _record(
            "describe",
            lambda: [
                describe(None),
                describe(1),
                describe([1, 2, 3]),
                describe({"kind": "point", "x": 2, "y": 3}),
                describe(3 + 4j),
                describe("other"),
            ],
        )
        _record(
            "nested",
            lambda: [
                nested_describe({"payload": [7, Point(0, 8)]}),
                nested_describe({"kind": "event", "value": 9}),
                nested_describe(42),
            ],
        )
        _record("collect", lambda: collect_description(1))
        """
    ),
    "test/simple_source/311/07_exception_group.py": _probe(
        """
        _record("handle_group", handle_group)
        _record(
            "mark_values",
            lambda: mark_values(ExceptionGroup("value", [ValueError("bad")])),
        )
        _record(
            "split_group",
            lambda: split_group(
                ExceptionGroup(
                    "mixed",
                    [ValueError("bad"), TypeError("type")],
                )
            ),
        )
        """
    ),
    "test/simple_source/311/08_imports_unpacking.py": _probe(
        """
        _record("formatting", lambda: formatting(9))
        _record(
            "unpacking",
            lambda: unpacking((1, 2), (3, 4), label="kept"),
        )
        """
    ),
    "test/simple_source/311/09_straight_line.py": _probe(
        """
        _record(
            "globals",
            lambda: (
                CONSTANT,
                CHAIN_LEFT,
                CHAIN_RIGHT,
                "temporary" in globals(),
            ),
        )
        _record(
            "calculate",
            lambda: calculate(6, 3, 9, 12, scale=2, extra=4),
        )
        _record(
            "classes",
            lambda: (
                Accumulator.from_value(4).doubled,
                Child(3).add(5),
                Child.stage3,
            ),
        )

        def _counter_values():
            counter = make_counter(5)
            return counter(), counter(3)

        _record("closures", lambda: (make_adder(10)(5), make_lambda(4)(6)))
        _record("counter", _counter_values)
        _record("unpack", lambda: unpack([1, 2, 3, 4]))
        _record("raise", lambda: fail("stage 6"))
        """
    ),
    "test/simple_source/311/10_nested_unpacking.py": _probe(
        """
        _record(
            "unpack_sequence",
            lambda: unpack_sequence(
                ("message", ("file.py", 3, 7, "line"))
            ),
        )
        _record(
            "unpack_sequence_error",
            lambda: unpack_sequence(("message", ("file.py", 3))),
        )
        _record(
            "unpack_extended",
            lambda: unpack_extended(
                ("head", (1, 2, 3, 4), "tail")
            ),
        )
        _record(
            "unpack_extended_error",
            lambda: unpack_extended(("head", (1,), "tail")),
        )
        _record(
            "sequence_loop",
            lambda: sequence_loop(
                [((1, 2), 3), ((4, 5), 6)]
            ),
        )
        _record(
            "sequence_loop_error",
            lambda: sequence_loop([((1,), 3)]),
        )
        _record(
            "extended_loop",
            lambda: extended_loop(
                [((1, 2), 3), ((4, 5, 6), 7)]
            ),
        )
        _record(
            "nested_comprehension",
            lambda: collect([((1, 2), 3), ((4, 5), 6)]),
        )
        """
    ),
    "test/simple_source/311/11_import_transactions.py": _probe(
        """
        _record(
            "import_transactions",
            imported_values,
        )
        """
    ),
    "test/simple_source/311/12_exception_cleanup.py": _probe(
        """
        def _translated(value):
            events = []
            try:
                result = translate_error(value, events)
            except BaseException as error:
                cause = error.__cause__
                result = (
                    type(error).__name__,
                    error.args,
                    None
                    if cause is None
                    else (type(cause).__name__, cause.args),
                )
            return result, events

        def _reraised():
            events = []
            try:
                reraised_error(events)
            except BaseException as error:
                return type(error).__name__, error.args, events

        def _nested_finally(fail):
            events = []
            try:
                result = nested_finally(events, fail)
            except BaseException as error:
                result = (type(error).__name__, error.args)
            return result, events

        def _generator(fail):
            events = []
            generator = cleanup_generator(events, fail)
            try:
                result = ("yield", next(generator))
            except BaseException as error:
                result = ("raise", type(error).__name__, error.args)
            else:
                generator.close()
            return result, events

        _record(
            "handler_returns",
            lambda: [
                return_from_handler(None),
                return_from_handler(0),
                return_from_handler("value"),
            ],
        )
        _record(
            "translated",
            lambda: [_translated("7"), _translated("bad"), _translated(None)],
        )
        _record("reraised", _reraised)
        _record(
            "nested",
            lambda: [
                nested_handler_return("value"),
                _nested_finally(False),
                _nested_finally(True),
            ],
        )
        _record(
            "generator",
            lambda: [_generator(False), _generator(True)],
        )
        _record(
            "handler_break",
            lambda: [
                handler_break([1]),
                handler_break([1, 2]),
                handler_break([1, None, 3]),
            ],
        )
        """
    ),
    "test/simple_source/311/13_call_expression_stack.py": _probe(
        """
        def _register(callback):
            return callback()

        class _Receiver:
            def method(self, positional, *, keyword):
                return positional, keyword

        def _ordered():
            events = []

            def mark(name, value):
                events.append(name)
                return value

            result = ordered_call(_Receiver(), mark)
            return result, events

        def _cleanup(fail):
            events = []

            def function():
                if fail:
                    raise ValueError("failed")
                return "value"

            return nested_finally_except(
                function,
                lambda: events.append("cleanup"),
            ), events

        _record(
            "format_and_choice",
            lambda: (
                formatted(1.25),
                nested_choice(1, 1),
                nested_choice(2, 1),
                nested_choice(1, 2),
            ),
        )
        _record(
            "selected_pattern",
            lambda: [
                selected_pattern("cpython", (3, 11, 4), "old", "new"),
                selected_pattern("pypy", (3, 11, 13), "old", "new"),
                selected_pattern("other", (3, 10), "old", "new"),
            ],
        )
        _record("callback", lambda: callback_argument(_register, 7))
        _record("ordered", _ordered)
        _record(
            "chain_loop",
            lambda: chain_loop([1, 2, 0, 4], []),
        )
        _record(
            "cleanup",
            lambda: [_cleanup(False), _cleanup(True)],
        )
        _record(
            "loop_return",
            lambda: loop_return_finally(
                [lambda: None, lambda: "found"],
                lambda: None,
            ),
        )
        """
    ),
    "test/simple_source/311/14_function_object_flow.py": _probe(
        """
        def _function_object_snapshot():
            result = decorated(4, scale=5)
            original = decorated.original.original
            descriptor = DescriptorDemo(6)
            holder, mapping, sequence, assigned = build_lambdas(7)
            return (
                result,
                list(EVENTS),
                original.__defaults__,
                original.__kwdefaults__,
                {
                    key: value.__name__
                    for key, value in original.__annotations__.items()
                },
                descriptor.add(2, 4),
                descriptor.owner_name(),
                descriptor.doubled,
                default_callback(),
                holder.transform(),
                holder.transform(5),
                mapping["scale"](3),
                mapping["identity"]("value"),
                sequence[0](),
                sequence[1][0](2),
                assigned["offset"](4),
            )

        _record("function_object_flow", _function_object_snapshot)
        """
    ),
    "test/simple_source/311/15_comprehension_iterator_protocol.py": _probe(
        """
        def _incremental_snapshot():
            sequence, members, mapping = incremental_literals(
                [1, 2],
                {"seed": 0},
                3,
            )
            return sequence, sorted(members), mapping

        def _comprehension_snapshot():
            records = [
                {"name": "compare", "hash": None, "compare": True},
                {"name": "skip", "hash": None, "compare": False},
                {"name": "hash", "hash": True, "compare": False},
            ]
            conditional, filtered, selected, valid = (
                comprehension_shapes(
                    [-2, -1, 0, 1, 2, 3],
                    records,
                    [b"a", b"bc"],
                )
            )
            return (
                conditional,
                filtered,
                [item["name"] for item in selected],
                valid,
            )

        def _generator_snapshot():
            generated = generator_lambda(7)
            first = next(generated)
            try:
                generated.send("finished")
            except StopIteration as stopped:
                returned = stopped.value
            return (
                list(make_prefixed(1, [2, 3])()),
                first,
                returned,
            )

        _record("incremental_literals", _incremental_snapshot)
        _record(
            "extended_and_terminal_loop",
            lambda: (
                extended_for(["k0", "k69", "missing"]),
                first_or_error([5, 6]),
            ),
        )
        _record("comprehension_shapes", _comprehension_snapshot)
        _record("generator_protocols", _generator_snapshot)
        """
    ),
    "test/simple_source/311/16_with_control_transfer.py": _probe(
        """
        def _return_snapshot():
            events = []
            manager = TraceContext(events, "return", value=3)
            return multi_statement_return(manager, 4), events

        def _loop_snapshot():
            events = []

            def factory(value):
                return TraceContext(events, str(value))

            return loop_transfers(factory, [1, -1, 2, 0, 3]), events

        def _context_snapshot():
            events = []
            multiple = multiple_contexts(
                TraceContext(events, "left", value=2),
                TraceContext(events, "right", value=5),
            )
            nested = nested_contexts(
                TraceContext(events, "outer", value=3),
                TraceContext(events, "inner", value=4),
            )
            suppressed = suppressed_exception(
                TraceContext(events, "suppress", suppress=True)
            )
            return multiple, nested, suppressed, events

        async def _async_snapshot():
            events = []
            returned = await async_return(
                AsyncTraceContext(events, "async", value=6),
                4,
            )

            def factory(value):
                return AsyncTraceContext(events, str(value))

            looped = await async_loop_transfers(
                factory,
                [1, -1, 2, 0, 3],
            )
            return returned, looped, events

        _record("with_return", _return_snapshot)
        _record("with_loop", _loop_snapshot)
        _record("with_contexts", _context_snapshot)
        _record_async("async_with", _async_snapshot)
        """
    ),
    "test/simple_source/311/17_recursive_structure.py": _probe(
        """
        _record(
            "scan",
            lambda: (
                scan_until("abc]tail", 0, "]"),
                scan_until("plain", 1, "]"),
            ),
        )
        _record(
            "prefix",
            lambda: collect_prefix([2, None, 4, "stop", 6], "stop"),
        )
        _record(
            "nested",
            lambda: nested_compound(["ab]", "xyz", "]"], "]"),
        )
        _record(
            "chained",
            lambda: (
                chained_guard([2, 4, 6, 3, 8]),
                chained_guard([2, -2, 4]),
            ),
        )
        """
    ),
    "test/bytecode_3.11/opcode_fixtures/collections/list_to_tuple.py": (
        '_record("starred_tuple", lambda: starred_tuple([1, 2, 3]))\n'
    ),
    "test/bytecode_3.11/opcode_fixtures/collections/set_update.py": (
        '_record("starred_set", lambda: starred_set([1, 2, 3]))\n'
    ),
    "test/bytecode_3.11/opcode_fixtures/expressions/unary_not.py": (
        '_record("logical_not", lambda: [logical_not(0), logical_not(3)])\n'
    ),
    "test/bytecode_3.11/opcode_fixtures/expressions/unary_positive.py": (
        '_record("unary_positive", lambda: unary_positive(-7))\n'
    ),
    "test/bytecode_3.11/opcode_fixtures/imports/import_star.py": _probe(
        """
        _record(
            "import_star",
            lambda: (sqrt(81), pi, "sin" in globals()),
        )
        """
    ),
    "test/bytecode_3.11/opcode_fixtures/internal/print_expr.py": "",
    "test/bytecode_3.11/opcode_fixtures/scope/delete_attr.py": _probe(
        """
        class _Box:
            def __init__(self):
                self.value = "present"

        def _delete_attribute():
            box = _Box()
            delete_attribute(box)
            return hasattr(box, "value"), box.__dict__

        _record("delete_attr", _delete_attribute)
        """
    ),
    "test/bytecode_3.11/opcode_fixtures/scope/delete_deref.py": _probe(
        """
        _deleter = make_deleter()
        _record("delete_deref_once", _deleter)
        _record("delete_deref_again", _deleter)
        """
    ),
    "test/bytecode_3.11/opcode_fixtures/scope/delete_global.py": _probe(
        """
        _record("global_before", lambda: globals().get("value"))
        _record("delete_global", delete_value)
        _record("global_after", lambda: globals().get("value", "missing"))
        """
    ),
    "test/bytecode_3.11/opcode_fixtures/scope/load_classderef.py": (
        '_record("class_deref", '
        'lambda: class_from_closure("captured").captured)\n'
    ),
    "test/bytecode_3.11/opcode_fixtures/scope/setup_annotations.py": _probe(
        """
        _record(
            "annotations",
            lambda: (__annotations__, Annotated.__annotations__),
        )
        """
    ),
    "test/bytecode_3.11/opcode_fixtures/scope/store_global.py": _probe(
        """
        _record("store_global", lambda: store_value("updated"))
        _record("stored_value", lambda: value)
        """
    ),
    "test/bytecode_3.11/opcode_fixtures/statements/load_assertion_error.py": (
        '_record("assert_success", lambda: require_value("present"))\n'
        '_record("assert_failure", lambda: require_value(""))\n'
    ),
}


INLINE_SHAPE_SOURCES = {
    "nested_and_or": _probe(
        """
        def apply(left, right, fallback):
            return left and (right or fallback)
        """
    ),
    "mixed_short_circuit_return": _probe(
        """
        def apply(value, base):
            return (value and value + base) or base
        """
    ),
    "short_circuit_evaluation_order": _probe(
        """
        def apply(events):
            def mark(name, value):
                events.append(name)
                return value
            return mark("left", 0) and mark("right", 1) or mark("tail", 2)
        """
    ),
    "chained_comparison": _probe(
        """
        def apply(left, middle, right):
            return left < middle <= right
        """
    ),
    "explicit_if_multiple_return": _probe(
        """
        def apply(value, base):
            if not value:
                return base
            result = value + base
            if not result:
                return base
            return result
        """
    ),
    "extended_arg": (
        "def apply(value):\n"
        + "".join(
            f"    item_{index} = value + {index}\n"
            for index in range(300)
        )
        + "    return item_0 + item_299\n"
    ),
    "except_star_with_else": _probe(
        """
        def apply(group, events):
            try:
                if group is not None:
                    raise group
            except* ValueError as errors:
                events.append(("handled", len(errors.exceptions)))
            else:
                events.append(("else",))
            return events
        """
    ),
    "except_star_with_finally": _probe(
        """
        def apply(group, events):
            try:
                raise group
            except* ValueError as errors:
                events.append(("handled", len(errors.exceptions)))
            finally:
                events.append(("finally",))
            return events
        """
    ),
    "compound_assert_condition": _probe(
        """
        def require_and(left, right, events):
            def mark(name, value):
                events.append(name)
                return value
            assert mark("left", left) and mark("right", right), "and failed"
            return events

        def require_or(left, right, events):
            def mark(name, value):
                events.append(name)
                return value
            assert mark("left", left) or mark("right", right), "or failed"
            return events

        def require_nested(left, middle, right, events):
            def mark(name, value):
                events.append(name)
                return value
            assert (
                mark("left", left)
                and (
                    mark("middle", middle)
                    or mark("right", right)
                )
            ), "nested failed"
            return events
        """
    ),
}


INLINE_SHAPE_PROBES = {
    "nested_and_or": (
        '_record("shape", lambda: ['
        "apply(0, 2, 3), apply(1, 0, 3), apply(1, 2, 3)])\n"
    ),
    "mixed_short_circuit_return": (
        '_record("shape", lambda: ['
        "apply(0, 5), apply(2, -2), apply(2, 5)])\n"
    ),
    "short_circuit_evaluation_order": _probe(
        """
        def _shape():
            events = []
            result = apply(events)
            return result, events
        _record("shape", _shape)
        """
    ),
    "chained_comparison": (
        '_record("shape", lambda: [apply(1, 2, 3), apply(3, 2, 1)])\n'
    ),
    "explicit_if_multiple_return": (
        '_record("shape", lambda: ['
        "apply(0, 5), apply(2, -2), apply(2, 5)])\n"
    ),
    "extended_arg": '_record("shape", lambda: apply(7))\n',
    "except_star_with_else": _probe(
        """
        def exercise(group):
            events = []
            try:
                result = apply(group, events)
            except BaseExceptionGroup as error:
                return (
                    "raise",
                    error.args[0],
                    tuple(type(item).__name__ for item in error.exceptions),
                    events,
                )
            return ("return", result, events)

        _record("else_none", lambda: exercise(None))
        _record(
            "else_handled",
            lambda: exercise(
                ExceptionGroup(
                    "values",
                    [ValueError("one"), ValueError("two")],
                )
            ),
        )
        _record(
            "else_split",
            lambda: exercise(
                ExceptionGroup(
                    "mixed",
                    [ValueError("value"), TypeError("type")],
                )
            ),
        )
        """
    ),
    "except_star_with_finally": _probe(
        """
        def exercise(group):
            events = []
            try:
                result = apply(group, events)
            except BaseException as error:
                nested = getattr(error, "exceptions", ())
                return (
                    "raise",
                    type(error).__name__,
                    error.args[0],
                    tuple(type(item).__name__ for item in nested),
                    events,
                )
            return ("return", result, events)

        _record(
            "finally_handled",
            lambda: exercise(
                ExceptionGroup(
                    "values",
                    [ValueError("one"), ValueError("two")],
                )
            ),
        )
        _record(
            "finally_split",
            lambda: exercise(
                ExceptionGroup(
                    "mixed",
                    [ValueError("value"), TypeError("type")],
                )
            ),
        )
        _record(
            "finally_plain",
            lambda: exercise(RuntimeError("plain")),
        )
        """
    ),
    "compound_assert_condition": _probe(
        """
        def exercise(operation, left, right):
            events = []
            function = require_and if operation == "and" else require_or
            try:
                result = function(left, right, events)
            except AssertionError as error:
                return ("raise", error.args, events)
            return ("return", result, events)

        def exercise_nested(left, middle, right):
            events = []
            try:
                result = require_nested(left, middle, right, events)
            except AssertionError as error:
                return ("raise", error.args, events)
            return ("return", result, events)

        _record(
            "assertions",
            lambda: [
                exercise("and", 1, 2),
                exercise("and", 0, 2),
                exercise("and", 1, 0),
                exercise("or", 1, 0),
                exercise("or", 0, 2),
                exercise("or", 0, 0),
                exercise_nested(1, 2, 0),
                exercise_nested(1, 0, 3),
                exercise_nested(1, 0, 0),
                exercise_nested(0, 2, 3),
            ],
        )
        """
    ),
}
