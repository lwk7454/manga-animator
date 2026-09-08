"""Scheduler — queue, pause switch, monitor loop (PLAN §9, Phase 3+).

Planned contents:
- ``queue.json`` under ``data/state/`` — one job in flight at a time.
- Pause semantics: ``koma pause`` drains the in-flight job then holds;
  ``--now`` hard-stops; machine-off ≡ paused (probe fails ⇒ hold).
- ``koma monitor [--once]`` — submit/poll/download/advance/assemble tick;
  survives Mac and box reboots; lost-in-crash jobs ⇒ FAILED ⇒ re-queueable.

Phase 0 ships no implementations (pause flag + status surfacing only).
"""
