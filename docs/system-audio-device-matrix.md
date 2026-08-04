# System-audio real-device validation

This matrix is intentionally user-operated. Run it only with explicit approval,
consented test speech, and the required macOS Screen & System Audio Recording
grant. Do not use private meetings.

## Preparation

```bash
cd "/Users/jchiu/Documents/00_Personal_Projects/Coding - General/Speakeasy 06JUL26"
scripts/build_system_audio_helper.sh
npm --prefix frontend run build
.venv/bin/python -m speakeasy
```

For every run, play a short known phrase from the remote side, speak a different
known phrase locally, end the meeting, and inspect only the privacy-safe capture
metadata in the newest transcript JSON:

```bash
latest_meeting_path="$(find "/Users/jchiu/Library/Application Support/Speakeasy/meetings" \
  -type f -name '*.json' -print0 | xargs -0 ls -t | head -1)"
jq '{capture_mode,capture_scope,system_audio_status,capture_health,track_offsets_seconds}' \
  "$latest_meeting_path"
```

Confirm the spool directory is empty after success, cancel, and failure:

```bash
find "/Users/jchiu/Library/Application Support/Speakeasy/spool" -type f
```

## Matrix

Run each row once with **All system audio** and once with the named application
selected. Google Meet's selected case means the browser process, not an
individual tab.

| Application | Output route | Global | Selected | Expected |
|---|---|---:|---:|---|
| Zoom desktop | Wired headphones | ☐ | ☐ | Local=`You`; remote captured; no silent-system warning |
| Zoom desktop | AirPods/Bluetooth | ☐ | ☐ | Same, with stable offsets after route is connected |
| Zoom desktop | Speakers | ☐ | ☐ | Remote captured; possible documented mic bleed, no data loss |
| Teams desktop | Wired headphones | ☐ | ☐ | Local=`You`; remote captured; no silent-system warning |
| Teams desktop | AirPods/Bluetooth | ☐ | ☐ | Same, with stable offsets after route is connected |
| Teams desktop | Speakers | ☐ | ☐ | Remote captured; possible documented mic bleed, no data loss |
| Google Meet in one browser | Wired headphones | ☐ | ☐ | Browser-process audio captured; no tab-level claim |
| Google Meet in one browser | AirPods/Bluetooth | ☐ | ☐ | Same, with stable offsets after route is connected |
| Google Meet in one browser | Speakers | ☐ | ☐ | Remote captured; possible documented mic bleed, no data loss |

For each cell, verify:

- the live status progresses from waiting for data to a nonzero system signal;
- first-buffer offsets preserve the known local/remote phrase order;
- capture health reports truthful dropped-frame, lag, writer, and helper state;
- selected scope captures no unrelated notification/music audio;
- audio spools are deleted after success, cancellation, and processing failure;
- only transcript text and privacy-safe capture metadata remain.

## Selected-application failure checks

These checks must never switch to global capture:

1. Select Zoom, close it before **Begin Meeting**, refresh the selector, and
   confirm Begin is disabled until another scope is selected.
2. Start selected Zoom capture, then quit Zoom. Confirm the live status reports
   the selected application unavailable and the final scope is
   `selected_app_unavailable` or `mic_only`, never `global`.
3. Start selected Teams capture, quit and relaunch Teams. Confirm the old PID is
   rejected and a fresh selection is required.
4. Repeat with the selected browser process. Closing/reopening the browser must
   require reselection; opening a new tab alone must not be described as
   tab-level capture.
5. With explicit permission-change approval, deny/revoke Screen & System Audio
   Recording and confirm both global and selected starts visibly use mic-only
   fallback without persisting temporary system audio.

Record the app version, macOS version, audio route, scope, outcome, and any
unexpected health state for each failed cell. Do not record participant names,
meeting names, device names, window titles, transcript content, or audio.
