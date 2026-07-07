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

## Profiles: teach it your words

The speech model is fixed, so it can mishear personal vocabulary — names,
jargon, technical terms ("Claude" might come out as "clod"). A **profile**
fixes that with a learned correction layer: you train it once on your words,
and every future dictation rewrites those misrecognitions automatically. The
rewrite is a single pre-compiled text substitution, so it adds no latency —
dictation stays at the usual ~1 second.

Every launch starts with a menu in the terminal (it appears instantly,
before the model loads):

```
Speakeasy — choose a profile:

  1. jason     (12 words, 8 corrections)
  2. work      (3 words)

  n. New profile
  t. Train a profile
  g. Guest (no corrections)

Choose [1]:
```

Press **Enter** to use the first profile and start dictating (the ready line
confirms it: `Ready [profile: jason]. ...`), or type its number. **n** creates
a new profile — you'll be offered a training session right away. **t** trains
an existing profile, then continues into dictation with it active. **g**
dictates without corrections, exactly as before. On first run, with no
profiles yet, Enter creates your first one.

### Training

Run training any time with:

```bash
.venv/bin/python -m speakeasy --train
```

Instead of asking you to think up words, Speakeasy suggests short sessions to
read aloud — everyday phrases first, then the words the model most often
mishears (tech jargon, names, numbers, tricky words). Each session is only a
handful of lines, and there are several to work through over a few sittings:

```
Training sessions:

  1. Everyday phrases            (suggested)
  2. Tech & jargon
  3. Names & proper nouns
  4. Numbers & units
  5. Tricky words & homophones

  c. Custom — type your own words
  q. Finish training
Choose [1]:
```

Press **Enter** to run the suggested (next unfinished) session. For each
line, hold Right Command and read it aloud; when a target word is misheard,
the correction is saved:

```
Tech & jargon — read each line aloud.
  Read aloud — hold [cmd_r] and say: "Deploy the service to Kubernetes."
    ✗ heard "communities" — saved correction → "Kubernetes"
```

Finished sessions are marked `✓ done` and the profile remembers them, so the
menu always points you at what's left. **c** keeps the classic type-your-own
words mode for personal names the built-in content can't know. **q** finishes
and continues into normal dictation. All content ships offline; progress is
saved as you go, so Ctrl-C never loses work.

### Details

- Profiles are plain JSON in the `profiles/` folder, one file per profile,
  and hand-editable — add corrections directly as `"heard": "intended"`
  pairs. Corrections match whole words, case-insensitively; longer phrases
  win over shorter ones.
- `--profile NAME` skips the picker (handy for a launch alias).
- Training refuses corrections that would rewrite another word the profile
  knows, so real words can't be mapped away by a bad take.
- Everything stays on-device: profiles are local files; nothing is uploaded.

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
- `PROFILES_DIR` — where dictation profiles are stored
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
  the waveform bars. The CoreAudio stream is opened once at startup and kept
  across recordings, so pressing the hotkey starts capture instantly instead
  of paying a ~100 ms stream open that could clip your first syllable (the
  mic-in-use indicator still only shows while you're actually recording)
- **`transcriber.py`** — Parakeet MLX model; loaded and run on one dedicated
  worker thread, because MLX pins its GPU arrays to their creating thread.
  Audio is fed to the model in-memory (log-mel + generate), skipping the
  temp-WAV file and ffmpeg decode of the library's path-based API
- **`profiles.py`** — per-user profiles: personal vocabulary plus learned
  heard→intended corrections, stored as JSON in `profiles/` and applied to
  each transcript with one pre-compiled regex substitution (microseconds)
- **`training.py`** — the guided training session: you type a word, speak it
  with the hotkey, and the model's actual misrecognitions are saved to the
  profile as corrections
- **`overlay.py`** — the waveform indicator: a borderless, click-through,
  non-activating AppKit panel animated at 30 fps only while visible
- **`injector.py`** — clipboard save → set text → ⌘V → restore, all via
  NSPasteboard in-process (no pbcopy/pbpaste subprocesses). The clipboard is
  saved while transcription runs, and restored after the hotkey listener is
  back up. The listener's event tap is torn down for the moment of the ⌘V and
  rebuilt right after, so a live pynput listener can't duplicate the
  synthesized paste
- **`__main__.py`** — wires it together; the AppKit run loop owns the main
  thread. Threading rules: transcription runs on its own worker thread, and
  recorder start/stop runs on a control thread — never on the hotkey
  event-tap thread, where stopping CoreAudio deadlocks against the HAL mutex

## Notes

- Text is inserted via clipboard + simulated ⌘V; your previous clipboard **text**
  is restored afterwards (non-text contents like images are not).
- Recordings shorter than 0.3 s are ignored as accidental taps.
- The global hotkey is briefly inactive (a few hundredths of a second) while
  the ⌘V is synthesized, so the text pastes exactly once; a hotkey press in
  that instant — right after you release — would be missed, but it's far too
  short to hit in practice.
- Everything runs in user space — no kernel extensions, no injection into other
  apps. If Speakeasy crashes, nothing else is affected.
