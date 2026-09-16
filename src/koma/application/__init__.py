"""Application services: context, planning, run (P13 target; staged here).

Currently hosts the run controller import surface so tests and the future
unified run loop share one package boundary (PLAN.md §4).
"""

from .control import RunController, can_run_transition

__all__ = ["RunController", "can_run_transition"]
