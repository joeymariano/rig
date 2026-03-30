# Quick Start

## Prerequisites

- Zoom L6 connected via USB and set to **Multi Track** mode (Menu → USB → Mode → Multi Track)
- VirMIDI patched (run once: `sudo bash ~/rig/patch_midi.sh`)
- Python venv set up (run once: `bash ~/rig/install_multichannel.sh`)
- I2C enabled in raspi-config (for OLED)

## Step 1: Add your tracks

Each song needs three files in a `set-XX/song-XX/` folder:
```
~/rig/set-01/song-01/title.wav
~/rig/set-01/song-01/metronome.wav
~/rig/set-01/song-01/midi-for-processing.midi
```

Optionally add `info.txt` for display metadata:
```
title: My Song
bpm: 120
platform: Ableton
```

## Step 2: Check audio device index

```bash
python3 -c "import sounddevice as sd; print(sd.query_devices())"
```

Find the Zoom L6 entry (e.g. `[2] L6: USB Audio`). Set in `controller.py`:
```python
AUDIO_DEVICE = 2   # or None to always auto-detect
```

## Step 3: Run

The controller normally launches automatically via `~/.config/labwc/autostart` on boot. To run manually:

```bash
sudo python3 ~/rig/controller.py
```

Expected startup output:
```
Starting Performance Rig...
Stopped argononed           ← (or argone-oled / argonone-led)
Taskbar hidden
Found 4 tracks
  1. Set 1 - Song 1
  2. Set 1 - Song 2
  ...
Virtual MIDI port: RigMIDI
Processing launched (PID 12345)
MIDI bridged 128:0 → 14:0
Auto-detected Zoom L6: L6: USB Audio (device 2)
Ready!  ← prev  → next  ↓ play  ↑ pause  ESC/↑←→ quit
```

## Controls

| Key | Action |
|-----|--------|
| `←` | Previous track |
| `→` | Next track |
| `↓` | Play |
| `↑` | Pause / Resume |
| `ESC` | Exit |
| `↑` + `←` + `→` | Exit combo |

## OLED

- **Top ticker**: track number + title, BPM, platform (scrolls if too wide)
- **Middle**: remaining time (`3:42 left`) or `PAUSED` while playing
- **Bottom**: elapsed time since the set started (large clock)

## Autostart

The rig is launched from `~/.config/labwc/autostart`, not as a systemd service. Do not enable `performance-rig.service`.

```bash
# ~/.config/labwc/autostart
bash -c 'until systemctl --user is-active pipewire > /dev/null 2>&1; do sleep 0.5; done; sudo python /home/nmlstyl/rig/controller.py' &
```

See README.md for full setup documentation.
