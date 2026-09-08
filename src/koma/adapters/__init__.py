"""Adapters — model-adapter registry, specs not code (PLAN §11, Phase 3+).

Planned contents:
- ``config/models/components/*.toml`` — one weights artifact each: file,
  sha256, target dir, provenance (hash-verified at ``koma setup``).
- ``config/models/profiles/*.toml`` — lane definitions: workflow JSON +
  inject map (incl. unet/clip) + frame grid + component refs.
- ``koma models list/install/default/pin/ab`` — the multi-model UX
  (decision #021). Stock = zero-config defaults.
"""
