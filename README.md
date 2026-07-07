# Speakeasy

Local, private Wispr Flow clone for macOS. Hold **Right Command (⌘)** anywhere,
speak, release — the transcribed text is pasted at your cursor. Speech-to-text
runs entirely on-device (NVIDIA Parakeet on Apple MLX); nothing leaves your Mac.

## Run it (copy & paste)

Open **Terminal** and paste this single command. It works from anywhere — it
changes into the project directory and starts the app:

```bash
cd "/Users/jchiu/Documents/00_Personal_Projects/Coding - General/Speakeasy 06JUL26" && .venv/bin/python -m speakeasy
```

When you see `Ready. Hold [cmd_r] and speak...`, hold **Right Command**, speak,
and release. A pop sound and dancing rainbow waveform bars (bottom-center of
the screen) mark recording, a bottle sound marks stop, and the text appears at
your cursor within about a second.

Press **Ctrl-C** in the Terminal window to quit.

### Optional: a shorter command

To launch with a short word instead of the long path, paste this once to create
a `speakeasy` shortcut, then just type `speakeasy` in any new Terminal:

```bash
echo 'alias speakeasy='\''cd "/Users/jchiu/Documents/00_Personal_Projects/Coding - General/Speakeasy 06JUL26" && .venv/bin/python -m speakeasy'\''' >> ~/.zshrc
```

Open a new Terminal window afterwards for the alias to take effect.

## First-time setup

### 1. Install dependencies (one time)

```bash
cd "/Users/jchiu/Documents/00_Personal_Projects/Coding - General/Speakeasy 06JUL26" && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
```

The first run also downloads the speech model (~600 MB) to
`~/.cache/huggingface`; later runs load it instantly from that cache.

Dependencies are pinned to exact, tested versions in `requirements.txt` so a
reinstall can't silently pull a broken or tampered release. To reproduce the
complete tested environment — every transitive package too — install from the
full snapshot instead: `.venv/bin/pip install -r requirements.lock.txt`.

### 2. Grant macOS permissions (one time)

The **first time you run Speakeasy from Terminal**, macOS will prompt for
permissions. Grant all three to **Terminal** in
**System Settings → Privacy & Security**:

- **Microphone** — to record your voice (prompts automatically on first recording)
- **Accessibility** — to paste the transcribed text (simulated ⌘V)
- **Input Monitoring** — to detect the Right Command hotkey

After enabling **Accessibility** and **Input Monitoring**, fully quit and
reopen Terminal for the change to take effect, then run the command again.

> Tip: permissions are tied to the specific app you launch from. If you switch
> from Terminal to iTerm (or vice versa), grant the three permissions to that
> app too.

## Waveform indicator

While you hold the hotkey, seven glassy pastel-rainbow bars float at the
bottom-center of your screen and dance with your voice level — no box or
border, just the bars. After you release, they ripple gently while the text
transcribes, then fade out when it pastes. The indicator never steals focus
and is click-through.

Set `OVERLAY_ENABLED = False` in `speakeasy/config.py` to turn it off
entirely (sounds and terminal log still work).

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

- **`hotkey.py`** — global hold-to-talk listener (pynput)
- **`recorder.py`** — microphone capture (sounddevice) + live RMS level for
  the waveform bars
- **`transcriber.py`** — Parakeet MLX model; loaded and run on one dedicated
  worker thread, because MLX pins its GPU arrays to their creating thread
- **`overlay.py`** — the waveform indicator: a borderless, click-through,
  non-activating AppKit panel animated at 30 fps only while visible
- **`injector.py`** — clipboard save → set text → ⌘V → restore. The hotkey
  listener's event tap is torn down for the moment of the ⌘V and rebuilt right
  after, so a live pynput listener can't duplicate the synthesized paste
- **`__main__.py`** — wires it together; the AppKit run loop owns the main
  thread. Threading rules: transcription runs on its own worker thread, and
  recorder start/stop runs on a control thread — never on the hotkey
  event-tap thread, where stopping CoreAudio deadlocks against the HAL mutex

## Notes

- Text is inserted via clipboard + simulated ⌘V; your previous clipboard **text**
  is restored afterwards (non-text contents like images are not).
- Recordings shorter than 0.3 s are ignored as accidental taps.
- The global hotkey is briefly inactive (~0.2 s) while the ⌘V is synthesized, so
  the text pastes exactly once; a hotkey press in that window — right after you
  release — would be missed, but it's short enough to be unnoticeable in practice.
- Everything runs in user space — no kernel extensions, no injection into other
  apps. If Speakeasy crashes, nothing else is affected.
