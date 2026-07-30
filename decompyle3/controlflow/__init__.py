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

__all__ = [
    "BasicBlock",
    "ControlFlowAnalysis",
    "ControlFlowGraph",
    "Edge",
    "IrreducibleControlFlowError",
    "NaturalLoop",
    "analyze_control_flow",
    "build_cfg",
]
