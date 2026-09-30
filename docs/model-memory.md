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

## Post-meeting CPU heap and the diarization child (30 September 2026)

The installed-app check above missed the "≤ 2,000 MB after a meeting" target
(2,314 MB total). The extra was CPU heap, not MLX: "Malloc Large" went from
8.6 MB to 301 MB and "Malloc Small" from 260 MB to 384 MB. Diarization
(sherpa-onnx segmentation plus speaker embedding) now runs in a spawned child
process per meeting (`speakeasy/diarization_process.py`), so those allocations
live in a process that exits after the meeting.

### What was measured (synthetic audio, temp HOME, `footprint -p`)

Synthetic meetings made with macOS `say` (four voices, 16 kHz), 3.2 and 29.3
minutes; the synthetic 3.2-minute meeting diarizes in 19.0–20.4 s against 17.8 s
for the real one. Five meetings in one process (3.2, 3.2, 29.3, 3.2, 3.2 min),
diarize plus speaker merge. Parent-process footprint in MB:

| After meeting | Keep models (today) | Release each time | Release + `MallocLargeCache=0` | Child process (parent footprint) |
|---|---|---|---|---|
| baseline | 53 MB | 52 | 44 | 26 |
| 1 (3.2 min) | 302 | 302 | 54 | 26 |
| 2 (3.2 min) | 312 | 309 | 54 | 26 |
| 3 (29.3 min) | 804 | 356 | 59 | 25 |
| 4 (3.2 min) | 807 | 432 | 59 | 25 |
| 5 (3.2 min) | 723 | 438 | 59 | 25 |

Keeping the models is a plateau at the high-water mark, not a leak: the diarizer
alone is +89 MB to construct and +145 MB after its first 3.2-minute run, then
flat at 310 MB; the speaker-embedding extractor adds about 255 MB.

### macOS keeps freed large blocks in the footprint

On this OS (Darwin 27), libmalloc's large-allocation cache keeps freed blocks
dirty and they still count in `footprint`. In plain numpy, allocating then
freeing a 400 MB array left the footprint 519 MB above baseline; with
`MallocLargeCache=0` it returned to 17 MB. Freeing 12 and 107 MB arrays behaved
the same way (+12 and +119 MB kept by default). `malloc_zone_pressure_relief`
returned 0 MB every time, so deleting the Diarizer returned nothing (310 to
309 MB). `MallocLargeCache` is not in `man malloc`: it is an undocumented
switch, which is why it was rejected as the fix.

### No arena setting

sherpa-onnx 1.13.4 calls `EnableCpuMemArena` internally and its Python configs
expose only `model`, `num_threads`, `provider` and `debug`. Changing the arena
needs a rebuilt sherpa-onnx, which is ruled out because the dependency is pinned.

### Cost of the child

Spawn, import of sherpa-onnx and pipe overhead measured 0.67–0.69 s per meeting
during the design experiments (3.2 min: 20.1–20.9 s wall; 29.3 min: 182.0 s wall).
In the committed benchmark below the medians were equal to within noise (19.1 s
for both), so the overhead is under the 1.5 s limit; the negative figure is
run-to-run noise, not a speed-up.

### Benchmark output (`scripts/measure_diarization_memory.py`, 30 September 2026)

Actual output, one run, real bundled dev models, real child runner:

```
parent baseline: 31 MB
meeting 1 (meeting_3.2min): 19.2 s wall, 3 speakers, parent 31 MB (+0 MB)
meeting 2 (meeting_3.2min): 19.0 s wall, 3 speakers, parent 26 MB (-5 MB)
meeting 3 (meeting_29.3min): 180.2 s wall, 4 speakers, parent 26 MB (-5 MB)
meeting 4 (meeting_3.2min): 19.1 s wall, 3 speakers, parent 26 MB (-5 MB)
meeting 5 (meeting_3.2min): 19.0 s wall, 3 speakers, parent 26 MB (-5 MB)
3.2 min: child median 19.1 s, in-process median 19.1 s, overhead -0.04 s; 29.3 min in-process 179.9 s
PASS  parent growth <= 5 MB after every meeting
PASS  child overhead <= 1.5 s (3.2 min, medians)
PASS  child turns == in-process turns (3.2 min)
PASS  child turns == in-process turns (29.3 min)
```

The parent never grew after any meeting (baseline 31 MB, then 26 MB), and the
child's speaker turns were identical to the in-process turns for both meeting
lengths. Recheck with:

```
.venv/bin/python -B scripts/measure_diarization_memory.py
```

It takes about 8 minutes, uses synthetic audio only, and redirects HOME to a temp
directory before importing speakeasy.

Not measured: the unexplained part of the installed app's "Malloc Small" rise
(124 MB; the synthetic runs account for about 45 MB) may come from the 20
dictations.

Installed-app check after this change: pending (step 4 of the plan).
