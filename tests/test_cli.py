"""Durable feature tests; no live model services."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from koma.cli import app
from koma.models.domain import (
    ReadingDirection,
)
from koma.storage.series_store import SeriesStore
from tests.helpers import _script_store

runner = CliRunner()


@pytest.fixture()
def store(tmp_path: Path) -> SeriesStore:
    s = SeriesStore(tmp_path / "data")
    s.create_series("demo", title="Demo", direction=ReadingDirection.RTL)
    return s


class TestCLI:
    def test_new_and_status(self, tmp_path: Path) -> None:
        d = str(tmp_path / "d")
        r = runner.invoke(app, ["new", "clidemo", "--direction", "rtl", "--data-dir", d])
        assert r.exit_code == 0, r.output
        r2 = runner.invoke(app, ["status", "clidemo", "--data-dir", d])
        assert r2.exit_code == 0, r2.output
        assert "clidemo" in r2.output
        assert "chapters: none yet" in r2.output

    def test_new_rejects_bad_direction(self, tmp_path: Path) -> None:
        d = str(tmp_path / "d")
        r = runner.invoke(app, ["new", "x", "--direction", "bogus", "--data-dir", d])
        assert r.exit_code == 2

    def test_new_rejects_duplicate(self, tmp_path: Path) -> None:
        runner.invoke(app, ["new", "dup", "--data-dir", str(tmp_path / "d")])
        r = runner.invoke(app, ["new", "dup", "--data-dir", str(tmp_path / "d")])
        assert r.exit_code == 2

    def test_status_missing_series(self, tmp_path: Path) -> None:
        r = runner.invoke(app, ["status", "ghost", "--data-dir", str(tmp_path / "d")])
        assert r.exit_code == 2

    def test_status_shows_chapter_without_removed_scoring(self, tmp_path: Path) -> None:
        d = tmp_path / "d"
        runner.invoke(app, ["new", "s2", "--data-dir", str(d)])
        store = SeriesStore(d)
        store.ensure_chapter("s2", 1)
        store.set_chapter_gate("s2", 1, False, "seeded test page rejected")
        r = runner.invoke(app, ["status", "s2", "--data-dir", str(d)])
        assert r.exit_code == 0
        assert "ch01" in r.output
        assert "REJECTED" not in r.output
        assert "gate=" not in r.output

    def test_pause_resume(self, tmp_path: Path) -> None:
        d = tmp_path / "d"
        r1 = runner.invoke(app, ["pause", "--data-dir", str(d)])
        assert r1.exit_code == 0
        assert SeriesStore(d).is_paused()
        r2 = runner.invoke(app, ["resume", "--data-dir", str(d)])
        assert r2.exit_code == 0
        assert not SeriesStore(d).is_paused()

    def test_health_exits_zero(self, tmp_path: Path) -> None:
        r = runner.invoke(app, ["health", "--data-dir", str(tmp_path / "d")])
        assert r.exit_code == 0, r.output
        assert "ffmpeg" in r.output

    def test_version(self) -> None:
        r = runner.invoke(app, ["version"])
        assert r.exit_code == 0 and "koma" in r.output


class TestRemovedGate:
    def test_compatibility_command_has_no_scoring_flags(self, tmp_path):
        store, slug = _script_store(tmp_path)
        result = runner.invoke(app, ["gate", slug, "--data-dir", str(store.data_dir)])
        assert result.exit_code == 0
        assert "automated scoring removed" in result.output
        for flag in ("--enabled", "--llama-guard"):
            result = runner.invoke(app, ["gate", slug, flag, "--data-dir", str(store.data_dir)])
            assert result.exit_code == 2
