"""Durable feature tests; no live model services."""

from __future__ import annotations

import pytest

from koma.adapters.ui2api import UIWorkflow, WorkflowConvertError


@pytest.mark.parametrize("kind,mode", [("Bypass", 4), ("Reroute", 0)])
def test_converter_detects_cycles(kind, mode):
    wf = UIWorkflow({"nodes": [{"id": 1, "type": kind, "mode": mode,
                               "inputs": [{"type": "IMAGE", "link": 1}],
                               "outputs": [{"type": "IMAGE"}]}],
                     "links": [[1, 1, 0, 1, 0, "IMAGE"]]})
    with pytest.raises(WorkflowConvertError, match="cycle"):
        wf.real_source(1, 0)


def test_converter_follows_long_acyclic_bypass_chain():
    nodes = [{"id": n, "type": "Bypass", "mode": 4,
              "inputs": [{"type": "IMAGE", "link": n}],
              "outputs": [{"type": "IMAGE"}]} for n in range(1, 25)]
    nodes.append({"id": 25, "type": "LoadImage"})
    links = [[n, n + 1, 0, n, 0, "IMAGE"] for n in range(1, 25)]
    assert UIWorkflow({"nodes": nodes, "links": links}).real_source(1, 0) == (25, 0)
