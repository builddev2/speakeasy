# Speech model memory

The model is already bfloat16 in memory; `from_pretrained` casts the 32-bit file at load.

## Background (measured 29 Sep 2026, bundled model, mlx 0.31.2)

The user asked for the model to move to 16-bit. It is already 16-bit:
`parakeet_mlx.from_pretrained` defaults to `dtype=mx.bfloat16` and converts the 32-bit
file (`Contents/Resources/model/model.safetensors`, 2.3 GB) while it loads.
The live app measured 2,615 MB footprint: 2,189 MB "IOAccelerator (graphics)" = 1,218 MB
bf16 weights + MLX's **buffer cache** (freed Metal buffers MLX keeps for reuse; no
limit by default).

Standalone benchmark: 8 macOS `say` sentences, each clean / 10 dB SNR / 0 dB SNR white
noise, plus one 2.8-minute clip in 120 s chunks with 15 s overlap:

| Variant | Weights | Footprint after 24 clips | After 2.8 min audio | Short-clip time (8 clips) | Long clip |
|---|---|---|---|---|---|
| fp32 (no cast) | 2,396 MB | 3,452 MB | 7,757 MB | 1.16 s | 4.32 s |
| bf16 (today's app) | 1,218 MB | 2,792 MB | **7,085 MB** | 1.36 s | 5.11 s |
| fp16 | 1,218 MB | 2,791 MB | 7,085 MB | 1.38 s | 4.68 s |
| **bf16 + 256 MB cache cap** | 1,218 MB | **1,629 MB** | **1,598 MB** | 1.38 s | 3.89 s |

Accuracy (word error rate vs the script; clean errors are only formatting such as
"3:30", "300,000", "kilometers", so true clean WER ≈ 0):

| Set | fp32 | bf16 | bf16 + cap | fp16 |
|---|---|---|---|---|
| clean | 5.7% | 5.7% | 5.7% | 5.7% |
| 10 dB noise | 5.7% | 5.7% | 5.7% | 5.7% |
| 0 dB noise | 17.1% | 16.4% | 16.4% | 17.1% |
| long 2.8 min | 5.7% | 5.7% | 5.7% | 5.7% |

## Committed benchmark, run 29 Sep 2026

`scripts/measure_model_memory.py` (bundled model, synthetic `say` audio). These are
single runs, not repeated.

Uncapped (`--no-cap`):

```
cache cap: off
after load         weights+live=39 MB cache=39 MB footprint=240 MB
after 4 clips      weights+live=1218 MB cache=2806 MB footprint=4238 MB
after long (5.5s)  weights+live=1218 MB cache=6901 MB footprint=8264 MB
```

Capped (256 MiB, `config.MLX_CACHE_LIMIT_BYTES`):

```
cache cap: 256 MiB
after load         weights+live=39 MB cache=39 MB footprint=241 MB
after 4 clips      weights+live=1218 MB cache=256 MB footprint=1708 MB
after long (4.0s)  weights+live=1218 MB cache=208 MB footprint=1612 MB
```

("after load" is small because MLX loads weights lazily; they materialise on first use.)

## Caveat

The cap limits the idle buffer cache, not the transient working set. During a 120 s
meeting chunk the active peak is about 3.1 GB (3,082 MB measured); that is expected
and not capped.

## How to recheck

```
.venv/bin/python -B scripts/measure_model_memory.py --no-cap
.venv/bin/python -B scripts/measure_model_memory.py
```
