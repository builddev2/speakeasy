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
and release. A pop sound marks recording start, a bottle sound marks stop, and
the text appears at your cursor within about a second.

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

## Configuration

Edit `speakeasy/config.py` to change:

- `HOTKEY` — the push-to-talk key (currently Right Command / `cmd_r`)
- `MODEL_ID` — the speech model
- `SOUNDS_ENABLED`, `SOUND_START`, `SOUND_STOP` — audio feedback
- `SAMPLE_RATE`, `MIN_DURATION_SECONDS`, `PASTE_SETTLE_SECONDS` — timing

## How it works

```
hold Right Command ─► record mic (16 kHz mono)
release            ─► transcribe locally (Parakeet on Apple MLX / Metal)
                   ─► paste text at cursor (clipboard + simulated ⌘V)
                   ─► restore your previous clipboard
```

- **`hotkey.py`** — global hold-to-talk listener (pynput)
- **`recorder.py`** — microphone capture (sounddevice)
- **`transcriber.py`** — Parakeet MLX model; loaded and run on one dedicated
  worker thread, because MLX pins its GPU arrays to their creating thread
- **`injector.py`** — clipboard save → set text → ⌘V → restore
- **`__main__.py`** — wires it together; transcription runs off the hotkey
  thread so the listener never blocks

## Notes

- Text is inserted via clipboard + simulated ⌘V; your previous clipboard **text**
  is restored afterwards (non-text contents like images are not).
- Recordings shorter than 0.3 s are ignored as accidental taps.
- Everything runs in user space — no kernel extensions, no injection into other
  apps. If Speakeasy crashes, nothing else is affected.
