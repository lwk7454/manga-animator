"""P03 — revisioned order review contract (§12) on the interim store.

The UI submits the FULL desired permutation with expected revision and an
idempotent operation id. Stale writes conflict; repeats replay the original
result; identities survive arbitrary swaps (D03).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from koma.cli import app
from koma.models.domain import PanelState
from koma.review.sheets import _certify_by_ids, generate_review_page
from koma.storage.errors import RevisionConflict
from koma.storage.series_store import SeriesStore

sys.path.insert(0, str(__file__.rsplit("/", 1)[0]))
from helpers import PANELS_GRID, draw_page

runner = CliRunner()


@pytest.fixture()
def ingested(tmp_path: Path) -> tuple[SeriesStore, str]:
    src = tmp_path / "src"
    src.mkdir()
    draw_page(src / "001.jpg", PANELS_GRID)
    d = tmp_path / "data"
    runner.invoke(app, ["new", "rv", "--direction", "rtl", "--data-dir", str(d)])
    runner.invoke(app, ["ingest", "rv", str(src), "--data-dir", str(d)])
    return SeriesStore(d), "rv"


def test_full_permutation_preserves_identity(ingested):
    store, slug = ingested
    page = store.load_pages(slug)[0]
    ids = [pn.panel_id for pn in page.panels]
    desired = list(reversed(ids))
    r = store.set_page_order(
        slug, page.source_path, 0, desired, "op-a"
    )
    assert r["order_review_revision"] == 1
    page2 = store.load_pages(slug)[0]
    assert sorted(pn.panel_id for pn in page2.panels) == sorted(ids)
    pos = {pn.panel_id: pn.order for pn in page2.panels}
    assert [pid for pid, _ in sorted(pos.items(), key=lambda kv: kv[1])] == desired


def test_stale_expected_revision_conflicts(ingested):
    store, slug = ingested
    page = store.load_pages(slug)[0]
    ids = [pn.panel_id for pn in page.panels]
    store.set_page_order(slug, page.source_path, 0, ids, "op-1")
    # a second tab still at revision 0 with a DIFFERENT order: rejected
    with pytest.raises(RevisionConflict):
        store.set_page_order(
            slug, page.source_path, 0, list(reversed(ids)), "op-2"
        )
    # but a stale write repeating the SAME order idempotently replays
    r = store.set_page_order(slug, page.source_path, 0, ids, "op-1")
    assert r["idempotent"] is True


def test_review_ui_conflict_and_idempotence(ingested):
    store, slug = ingested
    page = store.load_pages(slug)[0]
    ids = [pn.panel_id for pn in page.panels]
    _certify_by_ids(store, slug, 1, ids)
    # UI double-submit (same op id): idempotent, no error
    _certify_by_ids(store, slug, 1, ids)
    # page no longer awaits review
    pages = [p for p in store.load_pages(slug)
             if any(pn.state is PanelState.AWAITING_ORDER_REVIEW for pn in p.panels)]
    assert pages == []


def test_review_sheet_renders_stable_ids(ingested, tmp_path):
    store, slug = ingested
    out = tmp_path / "sheet.html"
    n = generate_review_page(store, slug, out, token="tok")
    assert n == 1
    html = out.read_text(encoding="utf-8")
    page = store.load_pages(slug)[0]
    for pn in page.panels:
        assert pn.panel_id in html  # stable ids in DOM payloads (§12)
    assert "data-pid=" in html


def test_multiple_swaps_survive_save_reload(ingested):
    store, slug = ingested
    page = store.load_pages(slug)[0]
    n = len(page.panels)
    ids = [pn.panel_id for pn in sorted(page.panels, key=lambda p: p.order or 0)]
    rotated = ids[1:] + ids[:1]
    store.set_page_order(slug, page.source_path, 0, rotated, "op-rot")
    store2 = SeriesStore(store.data_dir)
    page2 = next(p for p in store2.load_pages(slug) if p.page_number == 1)
    by_pos = {pn.order: pn.panel_id for pn in page2.panels}
    assert [by_pos[i] for i in range(1, n + 1)] == rotated
