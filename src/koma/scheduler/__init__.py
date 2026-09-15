"""Experimental JSON queue, pause flag and polling monitor.

Pause currently requests drain/hold; immediate cancellation, command ownership
and resource-releasing resume remain pending. PLAN.md §§5-9 defines the
transactional work/attempt replacement and recovery contracts."""
