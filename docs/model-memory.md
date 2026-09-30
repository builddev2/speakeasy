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

The cap changed no transcript (all 25 identical to uncapped bf16). bf16 vs fp32 differed
on one 0 dB clip only. Timings are single runs and their differences are within
run-to-run noise, so no speed change is claimed.

## Committed benchmark, run 29 September 2026

`scripts/measure_model_memory.py` (bundled model, synthetic `say` audio). These are
single runs, not repeated. The script forces the weights to load (`mx.eval`) before the
first reading, so "after load" includes them. `peak` is MLX's high-water mark since
process start (it includes the load), not reset between stages.

Workload: 4 `say` sentences transcribed one by one, then one long clip made of those 4
clips repeated 8 times with 1 s of silence after each, transcribed in 120 s chunks with
15 s overlap. The long clip is 197.2 s (3.3 minutes, printed by the script). This is a
different workload from the Background table (24 clips, a 2.8-minute clip), so the
numbers are not like-for-like with it.

Uncapped (`--no-cap`):

```
cache cap: off
long clip: 197.2 s
after load         weights+live=1275 MB cache=630 MB peak=1679 MB footprint=2083 MB
after 4 clips      weights+live=1218 MB cache=1181 MB peak=2008 MB footprint=2586 MB
after long (4.5s)  weights+live=1218 MB cache=5276 MB peak=3066 MB footprint=6678 MB
```

Capped (256 MiB, `config.MLX_CACHE_LIMIT_BYTES`):

```
cache cap: 256 MiB
long clip: 197.2 s
after load         weights+live=1271 MB cache=265 MB peak=1679 MB footprint=1718 MB
after 4 clips      weights+live=1218 MB cache=249 MB peak=2005 MB footprint=1713 MB
after long (4.0s)  weights+live=1218 MB cache=257 MB peak=3066 MB footprint=1659 MB
```

## Caveat

The cap limits the idle buffer cache, not the transient working set. During a 120 s
meeting chunk the active peak is about 3.1 GB (3,082 MB measured earlier); that is
expected and not capped. Recheck it with the `peak=` column of the script output
(3,066 MB in the run above, both modes).

## How to recheck

```
.venv/bin/python -B scripts/measure_model_memory.py --no-cap
.venv/bin/python -B scripts/measure_model_memory.py
```

## Installed app (29–30 September 2026, build 7d72ebf)

Measured with `footprint -p <pid>` on the installed `/Applications/Speakeasy.app`.
Single measurements, not repeated.

| When | Total footprint | IOAccelerator (graphics) |
|---|---|---|
| Old build, 26 min after launch (before install) | 2,859 MB | 2,391 MB |
| New build, 1 min after launch, idle | **1,601 MB** | 1,214 MB |
| New build, 54 min after launch: 20 dictations (5 each in TextEdit, Codex, Teams, Claude) + one mic-only meeting (diarization 17.8 s) | **2,314 MB** | 1,470 MB |

- Dictation: all 20 logged takes `target_status=accepted`; 19 `success`, 1
  `empty_transcription` (Claude). The user confirmed text inserted in TextEdit, Codex
  and Teams.
- GPU memory after the meeting is 1,470 MB = 1,214 MB of weights + ~256 MB cache, so
  the MLX cap holds.
- The plan's targets were ≤ 1,800 MB idle (met) and ≤ 2,000 MB after a meeting
  (**missed, 2,314 MB**). The extra is CPU heap, not MLX: after the meeting,
  "Malloc Large" was 301 MB (8.6 MB before) and "Malloc Small" 384 MB (260 MB before).
  The likely source is the sherpa-onnx diarizer, which `engine.py` keeps loaded after
  the first meeting. This is an inference, not measured per component; it is a
  separate follow-up.
