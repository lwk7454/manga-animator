"""UI→API workflow converter (ComfyUI) — adapter-spec foundation (decision #014).

Vendor/community workflows ship in UI format (nodes + links + Set/Get virtual
wires). ComfyUI's /prompt endpoint needs API format (flat {id: {class_type,
inputs}}). This module converts:

  1. SetNode/GetNode virtual wires → real links (Set name → source output).
  2. Prunes UI-only nodes (MarkdownNote, group bypassers, show-anything…).
  3. Prunes mode≠0 nodes (bypassed/muted) and everything orphaned after that.
  4. Keeps only nodes reachable from OUTPUT nodes (ComfyUI executes every node
     in an API prompt; orphans would run and waste VRAM/time).
  5. Maps positional widgets_values → named inputs via the live /object_info
     class definitions (fetched once, cached).

Reroute nodes (UI type 'Reroute') are spliced out; NAMED links through
Get/Set resolve to the underlying source node output.
"""

from __future__ import annotations

import json
from typing import Any

# UI node types that never appear in an API prompt
DROP_TYPES = {
    "MarkdownNote",
    "Note",
    "Reroute",
    "GetNode",
    "SetNode",
    "Fast Groups Bypasser (rgthree)",
    "easy showAnything",
    "easy if",
}

# widget-typed input specs (everything else is a connection socket)
_WIDGET_TYPE_NAMES = {"INT", "FLOAT", "STRING", "BOOLEAN", "COMBO"}


class WorkflowConvertError(ValueError):
    """UI workflow cannot be converted to an API prompt."""


def _widget_names(class_def: dict[str, Any]) -> list[str]:
    """Ordered widget-input names for a class from its /object_info entry.

    A v3 spec may declare combined types like "FLOAT,INT" — still a widget.
    A pure socket spec is [TYPE, {...}] where TYPE is ALL-CAPS single word
    like MODEL/VAE/IMAGE/LATENT/NOISE/GUIDER/SAMPLER — never a widget.
    """
    names: list[str] = []
    for section in ("required", "optional"):
        spec = class_def.get(section) or {}
        for iname, ispec in spec.items():
            if not isinstance(ispec, (list, tuple)) or not ispec:
                continue
            t = ispec[0]
            if isinstance(t, list):  # COMBO options list
                names.append(iname)
            elif isinstance(t, str) and t.split(",")[0].strip() in _WIDGET_TYPE_NAMES:
                names.append(iname)
    return names


class UIWorkflow:
    """Parsed UI-format workflow with virtual-wire resolution."""

    def __init__(self, raw: dict[str, Any]):
        self.nodes: dict[int, dict[str, Any]] = {n["id"]: n for n in raw.get("nodes", [])}
        self.links: dict[int, list[Any]] = {
            lk[0]: lk for lk in raw.get("links", []) if isinstance(lk, list) and len(lk) >= 6
        }
        self._resolve_set_get()

    def _resolve_set_get(self) -> None:
        """Map Set names → (source_node_id, source_slot); rewrite Get links.

        SetNode: its first input link defines the name's source. GetNode
        outputs re-point to that source. Chained Get→Set also resolves
        because sets are processed in link order via fixed-point iteration.
        """
        set_sources: dict[str, tuple[int, int]] = {}
        for node in self.nodes.values():
            if node.get("type") != "SetNode" or not node.get("widgets_values"):
                continue
            name = node["widgets_values"][0]
            inp = (node.get("inputs") or [{}])[0]
            lid = inp.get("link")
            if lid is None:
                continue
            link = self.links.get(lid)
            if link:
                set_sources[name] = (link[1], link[2])
        # fixed-point: resolve Set-fed-by-Get chains
        for _ in range(8):
            changed = False
            for name, (src, slot) in list(set_sources.items()):
                src_type = self.nodes.get(src, {}).get("type")
                if src_type == "GetNode" and self.nodes[src].get("widgets_values"):
                    inner = self.nodes[src]["widgets_values"][0]
                    if inner in set_sources and set_sources[inner] != (src, slot):
                        set_sources[name] = set_sources[inner]
                        changed = True
                elif src_type in DROP_TYPES:
                    # e.g. Set fed by Reroute — resolve through it
                    inp = (self.nodes[src].get("inputs") or [{}])[0]
                    lid2 = inp.get("link")
                    link2 = self.links.get(lid2) if lid2 is not None else None
                    if link2:
                        set_sources[name] = (link2[1], link2[2])
                        changed = True
            if not changed:
                break
        self._set_sources = set_sources

    def real_source(self, node_id: int, slot: int) -> tuple[int, int] | None:
        """Resolve (node, slot) through Get/Set/Reroute to a real node output.

        Returns None for dead ends AND bypassed/muted sources (mode≠0) —
        a live node wired through a bypassed one must not drag the bypassed
        node back into the graph (vendor workflows bypass TextGenerateLTX2Prompt
        and friends; koma always drives the static prompt path).
        """
        visited: set[tuple[int, int]] = set()
        while True:
            source = (node_id, slot)
            if source in visited:
                raise WorkflowConvertError(f"cycle resolving workflow source {source}")
            visited.add(source)
            node = self.nodes.get(node_id)
            if node is None:
                return None
            t = node.get("type")
            if node.get("mode", 0) != 0:
                # ComfyUI bypass semantics: a bypassed node passes its
                # matching-type INPUT through to its output. Follow the first
                # same-type input that carries a link (TextGenerateLTX2Prompt
                # bypassed → static prompt string; LTXVChunkFeedForward
                # bypassed → model passes through).
                outs = node.get("outputs") or []
                want = outs[slot].get("type") if slot < len(outs) else None
                for inp in node.get("inputs") or []:
                    if inp.get("type") == want and inp.get("link") is not None:
                        link = self.links.get(inp["link"])
                        if link:
                            node_id, slot = link[1], link[2]
                            break
                else:
                    return None
                continue
            if t == "GetNode" and node.get("widgets_values"):
                name = node["widgets_values"][0]
                if name not in self._set_sources:
                    return None
                node_id, slot = self._set_sources[name]
            elif t in ("Reroute", "SetNode"):
                inp = (node.get("inputs") or [{}])[0]
                lid = inp.get("link")
                link = self.links.get(lid) if lid is not None else None
                if not link:
                    return None
                node_id, slot = link[1], link[2]
            else:
                return (node_id, slot)

    def live_nodes(self) -> set[int]:
        """Real nodes (mode==0, not dropped) reachable from output nodes."""
        # candidate real nodes
        real = {
            nid
            for nid, n in self.nodes.items()
            if n.get("type") not in DROP_TYPES and n.get("mode", 0) == 0
        }
        # output nodes: real nodes with no outgoing live links
        outgoing: dict[int, list[tuple[int, int]]] = {}
        for link in self.links.values():
            src, dst = link[1], link[3]
            if src in real and dst in real:
                outgoing.setdefault(src, []).append((dst, link[2]))
        outputs = [nid for nid in real if not outgoing.get(nid)]
        # walk upstream from outputs
        reachable: set[int] = set()
        stack = list(outputs)
        while stack:
            nid = stack.pop()
            if nid in reachable:
                continue
            reachable.add(nid)
            node = self.nodes[nid]
            for inp in node.get("inputs") or []:
                lid = inp.get("link")
                if lid is None:
                    continue
                link = self.links.get(lid)
                if not link:
                    continue
                rs = self.real_source(link[1], link[2])
                if rs and rs[0] in real:
                    stack.append(rs[0])
        return reachable


def convert(
    ui_workflow: dict[str, Any],
    object_info: dict[str, Any],
    *,
    overrides: dict[str, dict[str, Any]] | None = None,
) -> dict[str, dict[str, Any]]:
    """UI workflow dict + live /object_info → API prompt graph.

    `overrides`: {class_type: {input_name: value}} applied to every node of
    that class after mapping (e.g. {"EmptyLTXVLatentVideo": {"length": 9}}).
    Widget values not covered by the class def are dropped silently — the
    object_info mapping is authoritative.
    """
    wf = UIWorkflow(ui_workflow)
    live = wf.live_nodes()
    if not live:
        raise WorkflowConvertError("no live nodes after pruning")

    api: dict[str, dict[str, Any]] = {}
    renumber: dict[int, int] = {}
    for i, nid in enumerate(sorted(live), start=1):
        renumber[nid] = i

    for nid in sorted(live):
        node = wf.nodes[nid]
        ctype = node["type"]
        class_def = (object_info.get(ctype) or {}).get("input") or {}
        if not class_def:
            raise WorkflowConvertError(f"class {ctype!r} not on this ComfyUI instance")
        wnames = _widget_names(class_def)
        widgets = node.get("widgets_values")
        widgets = widgets if isinstance(widgets, list) else []
        inputs: dict[str, Any] = {}
        # positional widget mapping
        for wi, wname in enumerate(wnames):
            if wi < len(widgets) and widgets[wi] is not None:
                inputs[wname] = widgets[wi]
        # vendor UI workflows omit values for widgets that still need a value
        # in API format (UI saves only non-default widgets) — fill from the
        # class spec defaults so validation never sees a missing input.
        for section in ("required", "optional"):
            spec = class_def.get(section) or {}
            for iname, ispec in spec.items():
                if iname in inputs:
                    continue
                if isinstance(ispec, (list, tuple)) and len(ispec) >= 2 and isinstance(
                    ispec[1], dict
                ):
                    default = ispec[1].get("default")
                    if default is not None and not isinstance(
                        ispec[0], list
                    ):  # sockets never get defaults
                        inputs[iname] = default
        # connections override widgets
        for inp in node.get("inputs") or []:
            iname = inp.get("name")
            lid = inp.get("link")
            if iname is None:
                continue
            if lid is None:
                # widget-connection carrying a widget value (serialized links)
                wv = inp.get("widget")
                if wv is not None and isinstance(wv, dict) and "name" in wv:
                    pass
                continue
            link = wf.links.get(lid)
            if not link:
                continue
            rs = wf.real_source(link[1], link[2])
            if rs is None:
                raise WorkflowConvertError(
                    f"{ctype}.{iname}: source {link[1]} resolves to nothing"
                )
            # API format link targets are [str(node_id), slot] — ints KeyError
            # inside ComfyUI's validator (learned on the first smoke submit).
            inputs[iname] = [str(renumber[rs[0]]), rs[1]]
        if overrides and ctype in overrides:
            inputs.update(overrides[ctype])
        api[str(renumber[nid])] = {"class_type": ctype, "inputs": inputs}
    return api


def convert_file(
    ui_path: str, object_info: dict[str, Any], **kw: Any
) -> dict[str, dict[str, Any]]:
    with open(ui_path, encoding="utf-8") as f:
        return convert(json.load(f), object_info, **kw)


__all__ = ["WorkflowConvertError", "convert", "convert_file"]
