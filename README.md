# Speakeasy

Local, private Wispr Flow clone for macOS. A menu-bar app: hold
**Right Command (⌘)** anywhere, speak, release — the transcribed text is pasted
at your cursor. Speech-to-text runs entirely on-device (NVIDIA Parakeet on Apple
MLX); nothing ever leaves your Mac.

Speakeasy lives in the menu bar (no Dock icon). Click its status item to see the
current state, switch profiles, train, or quit.

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
model, so the app is fully offline the first time you open it). Move it into
Applications:

```bash
mv dist/Speakeasy.app /Applications/
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

## Development (run from source)

You don't need the frozen app to develop — run the package directly from a
virtualenv.

### 1. Install dependencies (one time)

```bash
cd "/Users/jchiu/Documents/00_Personal_Projects/Coding - General/Speakeasy 06JUL26" && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
```

The first run downloads the speech model (~2.3 GB) to `~/.cache/huggingface`;
later runs load it instantly from that cache, and `build_app.sh` copies it out
of there into the app bundle.

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
.venv/bin/python -m pytest             # pure-logic units: profiles, training alignment, mel shim
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

## How it works

```
hold Right Command ─► record mic (16 kHz mono) + rainbow bars dance with voice
release            ─► transcribe locally (Parakeet on Apple MLX / Metal)
                   ─► paste text at cursor (clipboard + simulated ⌘V)
                   ─► restore your previous clipboard, bars fade out
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
  temp-WAV file and ffmpeg decode of the library's path-based API
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
  wires the engine to the AppKit run loop
- **`ui/training_window.py`** — the native glass training window opened from the
  menu bar
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
- User data (profiles, logs) lives under
  `~/Library/Application Support/Speakeasy/` and `~/Library/Logs/Speakeasy.log`.
- Everything runs in user space — no kernel extensions, no injection into other
  apps. If Speakeasy crashes, nothing else is affected.
