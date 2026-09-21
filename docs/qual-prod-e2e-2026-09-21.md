# Production E2E — wired transport, 21 September 2026

Upstream `8595b77` wired the local ComfyUI transport into the resumable
worker (`KOMA_COMFY_BASE`). Validated live on the 3090:

## Chain

1. **Stores seeded** (series/chapter/shot plan/beat + panel artifact with
   real sha256 under the data root).
2. **Ratio spec frozen** (`budget:2`, manifest with the panel slot).
3. **`plan_work`** created compiled motion work (fingerprint = canonical
   hash of graph; upload plan carries artifact/hash/`role`).
4. **`R2VWorker.execute` through `ComfyR2VTransport`**:
   - resolved frozen reference artifacts from the store (never caller paths)
   - validated schema/sockets/canvas/upload identity against `/object_info`
   - uploaded → submitted → polled → downloaded → ffprobe-validated →
     **promoted** → **candidate registered in the same transaction**
   - outcome `succeeded`, artifact sha256 durable.
5. **Review**: candidate accepted by the reviewer gate.
6. **Assembly** (accepted candidate → real TTS → mix → mux):
   dialogue measured 1.14 s, mux padded to 5.167 s video authority,
   **aac track present**, mid-frame pixel check 139.2, jp+en SRT written.
   Deliverable: `data/demo/prod_e2e_final.mp4`.

## Transport bug found + fixed (committed `0b99669`)

ComfyUI coerces integer widget values to floats when persisting the
executed graph (`CreateVideo fps: 24` → `24.0`). The frozen-work hash
comparison then rejected a semantically identical remote graph
("remote graph differs from frozen work"). Fix: `graph_hash`
canonicalizes integral floats before hashing. 437 tests stay green.

## Honest notes

- The 6 s final run was ComfyUI cache-dedupe returning the identical
  output for the same graph+seed — the pipeline behavior (validate →
  promote → register) is the point, and the first uncached submission
  ran 99 s end-to-end before failing only at the hash comparison.
- Driver scripts live in `/tmp/koma-qual/` (prod_e2e.py, prod_assembly.py,
  diff_remote_graph.py, hash_check.py).
- Still open per PLAN review checkpoint: colored composition route (§E),
  chapter assembly, review matrix (§G 4–6), production voice bindings.