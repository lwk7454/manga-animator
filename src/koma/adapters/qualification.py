"""Profile pins, qualification records and honest capability checks (§15).

One profile authority (§15.1): the series DB row is THE pin; global config
supplies the initial default only when creating an unpinned series. No
competing writable source.

Verification ≠ qualification (§15.3): ``verify`` checks files/hashes/
workflow structure against the configured service; ``qualification`` records
a bounded real probe's identity + evidence. A profile cannot become
qualified merely because files exist or a mocked response succeeds —
qualification requires a probe result and is invalidated when any identity
component (model bytes, workflow hash, custom nodes, hardware/runtime)
changes.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..adapters.registry import AdapterError, load_profile
from ..log import get_logger
from ..storage.domain_store import KomaStore
from ..storage.errors import BlockedPrerequisite
from ..util import canonical_hash, utcnow_iso

log = get_logger("adapters.qualification")

# Identity tuple parts that invalidate qualification when any changes.
QUAL_IDENTITY_KEYS = (
    "profile_id", "workflow_hash", "component_identity", "server_fingerprint",
    "captured_schema_hash",
)


def effective_profile(config_dir: Path, series: dict, default_profile: str) -> str:
    """Series DB row is the authority; default only for unpinned series."""
    pin = series.get("video_profile_id")
    return pin or default_profile


def set_pin(store: KomaStore, slug: str, profile_id: str | None) -> dict:
    """Set the series pin with affected-stage reporting (§15.1).

    Already accepted artifacts remain historical; only dependent desired
    outputs become missing/stale (planners consume fingerprints — this call
    bumps the series revision so new fingerprints differ).
    """
    series = store.require_series(slug)
    old_pin = series["video_profile_id"]
    new_rev = store.set_series_profile_pin(slug, profile_id, series["revision"])
    note = (
        "accepted artifacts stay historical; dependent renders replan "
        "because the series revision (and thus work fingerprints) changed"
    )
    return {
        "slug": slug,
        "previous_pin": old_pin,
        "new_pin": profile_id,
        "series_revision": new_rev,
        "note": note,
    }


# ── verification (static, no GPU) ────────────────────────────────────────────


def verify_profile(config_dir: Path, profile_id: str) -> dict:
    """Static checks: profile loads, workflow exists, inject slots resolve.

    This is NOT qualification — it proves nothing about installed weights or
    runtime behavior (§15.3).
    """
    result: dict[str, Any] = {"profile_id": profile_id, "verified": False,
                              "checks": [], "errors": []}

    def check(name: str, ok: bool, detail: str = "") -> None:
        result["checks"].append({"name": name, "ok": ok, "detail": detail})
        if not ok:
            result["errors"].append(f"{name}: {detail}")

    try:
        spec = load_profile(config_dir, profile_id)
    except AdapterError as exc:
        check("profile_load", False, str(exc))
        return result
    result["kind"] = spec.get("kind")
    check("profile_load", True)

    wf_path = config_dir / spec.get("workflow", "")
    check("workflow_exists", wf_path.is_file(), str(wf_path))
    if wf_path.is_file():
        workflow = json.loads(wf_path.read_text(encoding="utf-8"))
        result["workflow_hash"] = canonical_hash(workflow)
        check("workflow_parses", True)
        inject = spec.get("inject", {})
        missing_nodes = []
        for slot, path in inject.items():
            node = str(path).split(".")[0]
            if node not in workflow:
                missing_nodes.append(f"{slot}→{node}")
        check("inject_nodes_exist", not missing_nodes, ", ".join(missing_nodes))
        if spec.get("kind") == "video_i2v":
            required = {"keyframe", "motion", "length", "fps", "seed", "width",
                        "height", "steps", "output"}
            missing = required - inject.keys()
            check("video_slots_complete", not missing, str(sorted(missing)))
    result["verified"] = not result["errors"]
    return result


# ── qualification records (DB; hardware probe fills evidence) ───────────────


def record_qualification(
    store: KomaStore,
    *,
    profile_id: str,
    workflow_id: str,
    component_identity: dict,
    server_fingerprint: str,
    captured_schema_hash: str,
    status: str,
    measured_metadata: dict | None = None,
    reason: str = "",
    probe_artifacts: list[str] | None = None,
) -> dict:
    """Persist a probe outcome (§15.3). ``status`` is qualified|failed|unqualified."""
    now = utcnow_iso()
    identity_hash = canonical_hash({
        "profile_id": profile_id,
        "workflow_id": workflow_id,
        "component_identity": component_identity,
        "server_fingerprint": server_fingerprint,
        "captured_schema_hash": captured_schema_hash,
    })
    with store.db.transaction(immediate=True) as conn:
        conn.execute(
            "INSERT INTO profile_qualifications (profile_id, workflow_id,"
            " component_identity, server_fingerprint, captured_schema_hash,"
            " probe_artifacts, measured_metadata, status, reason, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
            " ON CONFLICT(profile_id, workflow_id) DO UPDATE SET"
            " component_identity = excluded.component_identity,"
            " server_fingerprint = excluded.server_fingerprint,"
            " captured_schema_hash = excluded.captured_schema_hash,"
            " probe_artifacts = excluded.probe_artifacts,"
            " measured_metadata = excluded.measured_metadata,"
            " status = excluded.status, reason = excluded.reason,"
            " updated_at = excluded.updated_at",
            (
                profile_id, workflow_id, json.dumps(component_identity, sort_keys=True),
                server_fingerprint, captured_schema_hash, json.dumps(probe_artifacts or []),
                json.dumps(measured_metadata or {}), status, reason, now, now,
            ),
        )
    return {"profile_id": profile_id, "workflow_id": workflow_id, "status": status,
            "identity_hash": identity_hash}


def qualification_status(store: KomaStore, profile_id: str) -> dict:
    with store.db.transaction() as conn:
        rows = conn.execute(
            "SELECT * FROM profile_qualifications WHERE profile_id = ?",
            (profile_id,),
        ).fetchall()
    if not rows:
        return {"profile_id": profile_id, "status": "unqualified", "reason":
                "never probed (verification alone is not qualification)"}
    r = dict(rows[0])
    return {"profile_id": profile_id, "workflow_id": r["workflow_id"],
            "status": r["status"], "reason": r["reason"],
            "component_identity": json.loads(r["component_identity"]),
            "server_fingerprint": r["server_fingerprint"],
            "measured_metadata": json.loads(r["measured_metadata"] or "{}")}


def require_qualified(store: KomaStore, profile_id: str, workflow_id: str) -> dict:
    """Production gate: exact profile+workflow identity must be qualified.

    Any identity change (workflow bytes, components, server) invalidates —
    the caller must re-probe. This is the P09 hardware gate's offline half.
    """
    row = None
    with store.db.transaction() as conn:
        r = conn.execute(
            "SELECT * FROM profile_qualifications WHERE profile_id = ? AND workflow_id = ?",
            (profile_id, workflow_id),
        ).fetchone()
        row = dict(r) if r else None
    if row is None:
        raise BlockedPrerequisite(
            f"profile {profile_id}/{workflow_id} is not qualified — run models probe "
            "(verification is not qualification, §15.3)"
        )
    if row["status"] != "qualified":
        raise BlockedPrerequisite(
            f"profile {profile_id}/{workflow_id} qualification = {row['status']}: {row['reason']}"
        )
    row["measured_metadata"] = json.loads(row["measured_metadata"] or "{}")
    row["component_identity"] = json.loads(row["component_identity"] or "{}")
    return row


__all__ = [
    "QUAL_IDENTITY_KEYS",
    "effective_profile",
    "qualification_status",
    "record_qualification",
    "require_qualified",
    "set_pin",
    "verify_profile",
]
