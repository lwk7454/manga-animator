# Model configuration and qualification

The shipped profiles are experimental configuration, not proof of installed
weights, compatible nodes, memory fit, speed or output quality. Follow
[PLAN.md §§15–19 and §24](../PLAN.md) for the implementation and probe contracts.
All generation must remain local, including text and translation.

| Role | Current configuration/candidate | Remaining evidence |
|---|---|---|
| Text and vision | Local Qwen2.5-VL-7B endpoint in `config.toml` | Actual installed model/API, valid outputs, memory release and latency. |
| OCR | Optional local manga-ocr adapter | Installed dependency and representative Japanese pages. |
| Colorize | `manganinja_sd15` | Approved variant binding, installed node/weight identity and native-aspect output. |
| Outpaint | `qwen_image_edit_2509_outpaint` | Repair full-source preprocessing/effective graph; probe both ratios. |
| Video default | `minimax_h3_fl2va_turbo` | Installed schema/component identity, actual frame grid/fps and decoded output. |
| Video experiment | `minimax_h3_fused_turbo` | Same qualification plus comparative output evidence; not an automatic fallback. |
| TTS | GPT-SoVITS, implementation pending | Actual local API, approved voice assets, waveform/timing and listening checks. |
| Upscale | Real-CUGAN, implementation pending | Actual backend, frame preservation, resolution, memory and speed. |

The active `config/workflows/` files remain because profiles consume them.
Unused ref2va/vendor workflow copies and the scraped research corpus were removed;
experimental features are deferred. Source history is available in the
[pre-cleanup research tree](https://github.com/lwk7454/manga-animator/tree/01666663326be2b0bb2ac1d15fe27021e9e7b111/docs/research).
That archive contains outdated/unverified claims and is not an installation guide.

Component metadata preserves provenance fields, but missing hashes or unverified
files cannot establish qualification. Confirm upstream model terms/provenance at
provisioning time. Never infer installed node signatures from historical copied
source or claim that weight-size arithmetic proves a workflow fits the GPU.

A runtime qualification record must identify model bytes, effective workflow,
provider/node versions, server/hardware identity, parameters and decoded output
metadata. Record actual memory, release and timing observations. Keep those logs,
model files and generated media under the runtime data directory, outside git.
The default offline suite validates configuration behavior only.
