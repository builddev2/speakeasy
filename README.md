# Speakeasy

Local, private Wispr Flow clone for macOS. A menu-bar app: hold
**Right Command (⌘)** anywhere, speak, release — the transcribed text is pasted
at your cursor. Speech-to-text runs entirely on-device (NVIDIA Parakeet on Apple
MLX); nothing ever leaves your Mac.

Speakeasy lives in the menu bar as a small skull icon — it turns into a mic
while recording and a waveform while transcribing. Click its status item to
see the current state, switch profiles, train, record a meeting, or quit.
Speakeasy also has a normal Dock icon; clicking it opens a small window with
the same core actions, as a fallback for when the status item is hidden by
menu-bar overflow (common with many menu-bar apps installed) or just hard to
spot — the status item stays the primary, full-featured interface.

It can also transcribe whole **meetings** (up to a couple of hours): start a
recording from the menu bar and, when you end it, Speakeasy produces a
speaker-labelled transcript on-device — see
[Meeting transcription](#meeting-transcription). Only the transcript is kept;
the audio is deleted as soon as processing finishes.

## Install

Speakeasy ships as a standalone **Speakeasy.app** — once built it needs no
Terminal, no Python, and no network. Building it does need the source checkout
and a one-time dev environment (see [Development](#development-run-from-source)
for the `.venv` setup).

### 1. Build the app

```bash
cd "/Users/jchiu/Documents/00_Personal_Projects/Coding - General/Speakeasy 06JUL26" && scripts/build_app.sh
```

This produces `dist/Speakeasy.app` (~2.5 GB — it embeds the full 2.3 GB speech
model, so the app is fully offline the first time you open it). To build **and**
install it into `/Applications` in one step:

```bash
scripts/build_app.sh --install
```

`--install` updates the bundle **in place** (it rsyncs the new build over the
existing `/Applications/Speakeasy.app`, quitting a running instance first)
rather than deleting and recreating it. That matters for permissions: macOS
keys the Accessibility / Input Monitoring grants on the app's code signature,
and replacing the whole bundle can drop the existing grant and force you to
re-approve. Overwriting the same bundle keeps it — so with a stable signing
identity (below), later `--install` rebuilds don't re-prompt.

Without `--install` the build only writes `dist/`, and you can install it
yourself; prefer overwriting in place over `mv` for the same reason:

```bash
rsync -a --delete dist/Speakeasy.app/ /Applications/Speakeasy.app/
```

> **Signing (recommended, one time).** By default the build ad-hoc signs the
> app, and macOS treats *every rebuild* as a brand-new app — you'd have to
> re-grant all three permissions each time. To keep your grants across
> rebuilds, create a self-signed **code-signing** certificate named
> `Speakeasy Dev` once: open **Keychain Access → Certificate Assistant →
> Create a Certificate…**, name it `Speakeasy Dev`, set *Certificate Type* to
> **Code Signing**, and create it. `build_app.sh` picks it up automatically
> (override with `SIGN_ID=…`).

### 2. Grant macOS permissions (one time)

Launch Speakeasy (from Spotlight or `/Applications`). It appears in the menu
bar and, on first run, guides you through granting three permissions **to
Speakeasy itself** in **System Settings → Privacy & Security**:

- **Microphone** — to record your voice (prompts automatically on first recording)
- **Accessibility** — to paste the transcribed text (simulated ⌘V)
- **Input Monitoring** — to detect the Right Command hotkey

After enabling **Accessibility** and **Input Monitoring**, quit Speakeasy from
its menu-bar item and reopen it for the changes to take effect.

> **Prompted again after a reinstall?** If Speakeasy asks for Accessibility /
> Input Monitoring even though you'd already granted them — and the Right ⌘
> hotkey does nothing — the old grant is stale (it pointed at a bundle that was
> replaced rather than updated in place). Clear the two entries and re-grant:
>
> ```bash
> tccutil reset ListenEvent com.jasonchiu.speakeasy
> tccutil reset Accessibility com.jasonchiu.speakeasy
> ```
>
> Then relaunch Speakeasy and approve both when prompted. Building with
> `scripts/build_app.sh --install` avoids this going forward.

### Uninstall

```bash
scripts/uninstall.sh            # remove the app + log; keep your profiles/settings
scripts/uninstall.sh --purge    # also remove profiles, settings, and the cached model
```

It quits Speakeasy, deletes the `.app` (from `/Applications`, `~/Applications`,
or `dist/`) and `~/Library/Logs/Speakeasy.log`, and — by default — keeps your
data in `~/Library/Application Support/Speakeasy` so a reinstall resumes where
you left off. It can't revoke the macOS permission grants or delete the
`Speakeasy Dev` cert; it prints where to do both.

## Using it

When Speakeasy is running, its menu-bar item shows the current state (`Ready`,
`Recording…`, `Transcribing…`). Hold **Right Command**, speak, and release: a
pop sound and dancing rainbow waveform bars (bottom-center of the screen) mark
recording, a bottle sound marks stop, and the text appears at your cursor within
about a second.

Click the menu-bar item for:

- **Profile** — switch the active profile, pick **Guest (no corrections)**, or
  create a **New Profile…**
- **Train Profile…** — open the training window (see below)
- **Begin Meeting** / **End Meeting** — record and transcribe a meeting (see
  [Meeting transcription](#meeting-transcription))
- **Meetings…** — browse, copy, export, rename, or delete saved transcripts
- **Quit Speakeasy** (⌘Q)

## Profiles: teach it your words

The speech model is fixed, so it can mishear personal vocabulary — names,
jargon, technical terms ("Claude" might come out as "clod"). A **profile**
fixes that with a learned correction layer: you train it once on your words,
and every future dictation rewrites those misrecognitions automatically. The
rewrite is a single pre-compiled text substitution, so it adds no latency —
dictation stays at the usual ~1 second.

Switch or create profiles from the menu-bar **Profile** submenu. **Guest**
dictates without corrections. The active profile is shown with a checkmark.

### Training

Choose **Train Profile…** from the menu bar to open the training window.
Instead of asking you to think up words, Speakeasy suggests short sessions to
read aloud — everyday phrases first, then the words the model most often
mishears (tech jargon, names, numbers, tricky words):

- **Everyday phrases** (suggested first)
- **Tech & jargon**
- **Names & proper nouns**
- **Numbers & units**
- **Tricky words & homophones**
- **Custom** — type your own words for personal names the built-in content
  can't know

For each line, hold Right Command and read it aloud; when a target word is
misheard, the correction is saved to the profile. Finished sessions are marked
done and remembered, so the window always points you at what's left. All
content ships offline; progress is saved as you go.

### Details

- Profiles are plain JSON in `~/Library/Application Support/Speakeasy/profiles/`,
  one file per profile, and hand-editable — add corrections directly as
  `"heard": "intended"` pairs. Corrections match whole words,
  case-insensitively; longer phrases win over shorter ones.
- Training refuses corrections that would rewrite another word the profile
  knows, so real words can't be mapped away by a bad take.
- Everything stays on-device: profiles are local files; nothing is uploaded.

## Meeting transcription

Click **Begin Meeting** in the menu bar to start recording (the icon becomes a
record symbol and the status line reads *Recording meeting — dictation
paused*). Meetings can run up to a couple of hours; the menu shows the elapsed
time. Click **End Meeting** when you're done, and Speakeasy processes the
recording entirely on-device:

1. the audio is transcribed in chunks (progress shows in the menu),
2. voices are separated by a local speaker-diarization model, and
3. the transcript is saved with each line attributed to **Speaker 1**,
   **Speaker 2**, … in speaking order.

A 2-hour meeting takes several minutes to process; **Cancel Processing** in
the menu discards it. When it finishes, the transcript appears under
**Meetings…**, where you can:

- **Copy** it to the clipboard and paste it into any notepad,
- **Export…** it as a `.txt` or `.md` file,
- **Rename…** or **Delete** it.

While a meeting records, hold-to-talk dictation is off (both would fight over
the mic and the model); it re-arms the moment processing starts. The active
profile's corrections are applied to the transcript, and it counts speakers
automatically — nothing to configure.

**Privacy: the audio itself is never kept.** During the meeting it spools to
a temporary file, which is deleted as soon as the transcript is saved — and
also on cancel, on failure, on quit, and (if the app ever crashes mid-meeting)
swept at the next launch. Transcripts are plain JSON in
`~/Library/Application Support/Speakeasy/meetings/`. Everything — recording,
transcription, speaker identification — runs offline; nothing leaves your Mac.

**Limitation — it hears what your mic hears.** On a Zoom/Teams call, Speakeasy
records alongside the call without interfering (macOS shares the mic between
apps), but remote participants are only captured if they play through your
**speakers**. With headphones on, their voices never reach the mic and won't
be in the transcript. A future enhancement could capture system audio directly
via ScreenCaptureKit (macOS 13+, requires the Screen Recording permission and
mixing the mic and system streams).

## Development (run from source)

You don't need the frozen app to develop — run the package directly from a
virtualenv.

### 1. Install dependencies (one time)

```bash
cd "/Users/jchiu/Documents/00_Personal_Projects/Coding - General/Speakeasy 06JUL26" && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
```

The first run downloads the speech model (~2.3 GB) to `~/.cache/huggingface`;
later runs load it instantly from that cache, and `build_app.sh` copies it out
of there into the app bundle. The meetings feature needs two small
speaker-diarization models (~44 MB total) fetched once, checksum-verified,
into `models/diarization/`:

```bash
scripts/fetch_diarization_models.sh
```

These are the only download steps — both happen at dev/build time, never at
runtime.

Dependencies are pinned to exact, tested versions in `requirements.txt` so a
reinstall can't silently pull a broken or tampered release. To reproduce the
complete tested environment — every transitive package too — install from the
full snapshot instead: `.venv/bin/pip install -r requirements.lock.txt`. Build
tooling (pytest, pyinstaller) lives in `requirements-dev.txt`.

### 2. Run it

```bash
.venv/bin/python -m speakeasy          # menu-bar app (same as the .app)
.venv/bin/python -m speakeasy --cli    # terminal front end (profile picker in the terminal)
.venv/bin/python -m speakeasy --train  # guided training in the terminal (implies --cli)
```

`--profile NAME` skips the picker (handy for a launch alias). When run from
Terminal, the three permissions attach to **Terminal** (or iTerm), not to the
`.app` — grant them there too if you develop from source.

### Tests

```bash
.venv/bin/python -m pytest             # pure-logic units: profiles, training alignment, mel shim,
                                        # meeting store/alignment/recorder/engine flow
```

## Waveform indicator

While you hold the hotkey, seven glassy pastel-rainbow bars float at the
bottom-center of your screen and dance with your voice level — no box or
border, just the bars. After you release, they ripple gently while the text
transcribes, then fade out when it pastes. The indicator never steals focus
and is click-through.

Set `OVERLAY_ENABLED = False` in `speakeasy/config.py` to turn it off
entirely (sounds and status still work).

## Configuration

Edit `speakeasy/config.py` to change:

- `HOTKEY` — the push-to-talk key (currently Right Command / `cmd_r`)
- `MODEL_ID` — the speech model
- `SOUNDS_ENABLED`, `SOUND_START`, `SOUND_STOP` — audio feedback
- `SAMPLE_RATE`, `MIN_DURATION_SECONDS`, `PASTE_SETTLE_SECONDS` — timing
- `OVERLAY_*` — waveform indicator on/off, bar count/size, position, translucency
- `MEETING_CHUNK_SECONDS`, `MEETING_OVERLAP_SECONDS`, `MEETING_MAX_SECONDS` —
  chunked transcription and the spool size cap
- `DIARIZATION_THRESHOLD`, `DIARIZATION_MIN_ON`, `DIARIZATION_MIN_OFF` —
  speaker-clustering sensitivity

## How it works

```
hold Right Command ─► record mic (16 kHz mono) + rainbow bars dance with voice
release            ─► transcribe locally (Parakeet on Apple MLX / Metal)
                   ─► paste text at cursor (clipboard + simulated ⌘V)
                   ─► restore your previous clipboard, bars fade out
```

```
Begin Meeting ─► mic spools to a temp WAV (dictation hotkey disabled)
End Meeting   ─► chunked transcription (Parakeet) + speaker diarization
                  (sherpa-onnx) ─► align text to speakers ─► save transcript
                  ─► delete the temp WAV ─► dictation hotkey re-armed
```

- **`hotkey.py`** — global hold-to-talk listener built on a raw Quartz
  `CGEventTap` (no third-party input library). A listen-only tap makes no Text
  Input Source calls, and pausing around the synthesized paste is a
  `CGEventTapEnable` flip rather than a listener teardown
- **`recorder.py`** — microphone capture (sounddevice) + live RMS level for
  the waveform bars. The CoreAudio stream is opened once at startup and kept
  across recordings, so pressing the hotkey starts capture instantly instead
  of paying a ~100 ms stream open that could clip your first syllable (the
  mic-in-use indicator still only shows while you're actually recording)
- **`transcriber.py`** — Parakeet MLX model; loaded and run on one dedicated
  worker thread, because MLX pins its GPU arrays to their creating thread.
  Audio is fed to the model in-memory (log-mel + generate), skipping the
  temp-WAV file and ffmpeg decode of the library's path-based API. Meetings
  use `transcribe_long()`, a from-scratch chunked loop (the same overlap +
  token-merge approach as the library's `transcribe(path, chunk_duration=…)`,
  but without its ffmpeg dependency) with per-chunk cancellation
- **`_mel_shim.py`** — a pure-numpy log-mel filterbank that stands in for
  librosa, so the freeze-fragile numba/llvmlite/scipy tree is dropped from the
  bundle entirely
- **`profiles.py`** — per-user profiles: personal vocabulary plus learned
  heard→intended corrections, stored as JSON and applied to each transcript
  with one pre-compiled regex substitution (microseconds)
- **`training.py`** — the guided training content and alignment: you read a
  line aloud, and the model's actual misrecognitions are saved to the profile
  as corrections
- **`ui/menubar.py`** — the `NSStatusItem`, its menu, and the app delegate that
  wires the engine to the AppKit run loop. The status glyph is a skull at rest
  (custom template image; macOS has no `skull` SF Symbol) that swaps to
  `mic.fill`/`waveform` while active
- **`ui/training_window.py`** — the native glass training window opened from the
  menu bar
- **`meeting_recorder.py`** — long-form mic capture: an int16 stream whose
  audio callback only enqueues bytes, drained to a spool WAV by a dedicated
  writer thread, so RAM stays flat regardless of meeting length. Spools live
  in `settings.spool_dir()`, are deleted on every exit path, and are swept
  clean at the next launch if the app ever crashes mid-meeting
- **`meetings.py`** — the meeting store (JSON per meeting, atomic saves like
  `profiles.py`), `.txt`/`.md` rendering, and the pure `align_speakers()`
  algorithm that maps diarization turns onto transcribed sentences
- **`diarizer.py`** — sherpa-onnx speaker diarization (CPU/onnxruntime, no
  MLX thread-pinning rule); lazily constructed on the first meeting from the
  two bundled ONNX models
- **`ui/meetings_window.py`** — the native glass window listing saved
  meetings, with copy/export/rename/delete
- **`ui/overlay.py`** — the waveform indicator: a borderless, click-through,
  non-activating AppKit panel animated at 30 fps only while visible
- **`ui/permissions.py`** — first-run permissions guidance and the
  Accessibility / Input Monitoring status checks (via `AXIsProcessTrusted`)
- **`injector.py`** — clipboard save → set text → ⌘V → restore, all via
  NSPasteboard in-process (no pbcopy/pbpaste subprocesses), with ⌘V posted as
  raw Quartz keyboard events. The clipboard is saved while transcription runs,
  and restored after. The hotkey's event tap is disabled for the moment of the
  ⌘V and re-enabled right after, so the listener can't duplicate the paste
- **`cli.py`** — the terminal front end (`--cli` / `--train`): the profile
  picker and guided training as a text UI
- **`__main__.py`** — the entry point; the menu-bar app is the default and the
  AppKit run loop owns the main thread. Threading rules: transcription runs on
  its own worker thread, and recorder start/stop runs on a control thread —
  never on the hotkey event-tap thread, where stopping CoreAudio deadlocks
  against the HAL mutex
- **`launcher.py`** — the frozen-app entry point: redirects stdout/stderr to
  `~/Library/Logs/Speakeasy.log` (a windowed app has none) before starting

## Notes

- Text is inserted via clipboard + simulated ⌘V; your previous clipboard **text**
  is restored afterwards (non-text contents like images are not).
- Recordings shorter than 0.3 s are ignored as accidental taps.
- The global hotkey is briefly inactive (a few hundredths of a second) while
  the ⌘V is synthesized, so the text pastes exactly once; a hotkey press in
  that instant — right after you release — would be missed, but it's far too
  short to hit in practice.
- User data (profiles, meeting transcripts, logs) lives under
  `~/Library/Application Support/Speakeasy/` and `~/Library/Logs/Speakeasy.log`.
  Meeting audio itself is never part of that persisted data — only the
  transcript is kept (see [Meeting transcription](#meeting-transcription)).
- Everything runs in user space — no kernel extensions, no injection into other
  apps. If Speakeasy crashes, nothing else is affected.
