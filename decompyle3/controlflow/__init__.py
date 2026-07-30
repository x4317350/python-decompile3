"""Version-neutral control-flow graph support used by Parser311."""

from decompyle3.controlflow.basicblock import BasicBlock
from decompyle3.controlflow.cfg import (
    ControlFlowGraph,
    Edge,
    build_cfg,
)
from decompyle3.controlflow.dominators import (
    ControlFlowAnalysis,
    IrreducibleControlFlowError,
    NaturalLoop,
    analyze_control_flow,
)
from decompyle3.controlflow.exception_regions import (
    ExceptionRegionMap,
    build_exception_region_map,
)
from decompyle3.controlflow.exceptiontable311 import (
    ExceptionRegion,
    ExceptionTableDecodeError,
    decode_exception_table,
)

__all__ = [
    "BasicBlock",
    "ControlFlowAnalysis",
    "ControlFlowGraph",
    "Edge",
    "ExceptionRegion",
    "ExceptionRegionMap",
    "ExceptionTableDecodeError",
    "IrreducibleControlFlowError",
    "NaturalLoop",
    "analyze_control_flow",
    "build_cfg",
    "build_exception_region_map",
    "decode_exception_table",
]
