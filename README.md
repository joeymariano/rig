# Live Performance Rig

Python controller for Raspberry Pi (Argon One V5) that synchronizes:
- 2 audio tracks routed to separate Zoom L6 outputs
- MIDI file playback driving a Processing visual sketch
- SSD1306 OLED display (built into Argon case)
- USB keyboard control (4 arrow keys + ESC)

## System Architecture

```
USB Keyboard (←→↑↓ ESC)
       │
  controller.py
  ├── TrackManager  — scans ~/rig/set-*/song-*/
  ├── Player        — sounddevice 4-ch audio + mido MIDI
  ├── Display       — SSD1306 OLED via I2C
  └── Keyboard      — evdev arrow keys
       │                        │
  Processing sketch        Zoom L6
  (HDMI visuals)       Out 1-2: title.wav
                       Out 3-4: metronome.wav
```

## File Structure

```
~/rig/
├── set-01/
│   ├── song-01/
│   │   ├── title.wav              # main audio → L6 outputs 1-2
│   │   ├── metronome.wav          # click track → L6 outputs 3-4
│   │   ├── midi-for-processing.midi
│   │   └── info.txt               # optional metadata (see below)
│   └── song-02/...
└── set-02/...
```

**`info.txt` format** (all fields optional):
```
title: My Song Name
bpm: 120
platform: SMD
timing: 11/4
```

- `title` — display name shown on OLED ticker (defaults to folder name)
- `bpm` — injected as a MIDI tempo event if the MIDI file has none
- `platform` / `timing` — shown on OLED ticker alongside title and BPM
- Legacy: a `bpm.txt` file containing just the BPM number is also accepted as a fallback

## Set Selection Screen

On boot, if more than one `set-*/` folder is found, the OLED shows a set picker before the performance begins:

```
┌────────────────────────┐
│                        │
│        SET 01          │  ← large centered label
│                        │
└────────────────────────┘
```

| Key | Action |
|-----|--------|
| `↑` | Previous set (slides in from top) |
| `↓` | Next set (slides in from bottom) |
| `←` or `→` | Confirm selection and continue |

Sets wrap around. Once confirmed, the rig loads all tracks from the chosen set and enters the performance screen.

If only one set folder exists, this screen is skipped automatically.

---

## Controls

| Key | Action |
|-----|--------|
| `←` | Stop and go to previous track |
| `→` | Stop and go to next track |
| `↓` | Play (starts audio + MIDI simultaneously) |
| `↑` | Pause / Resume |
| `ESC` | Exit (graceful shutdown) |
| `↑` + `←` + `→` | Exit combo (hold all three simultaneously) |

The exit combo is non-destructive: if the combo is never completed, each key fires its normal action on release.

## OLED Layout

```
┌────────────────────────┐
│01 Song Title 120bpm ..→│  ← scrolling ticker: track number + title/BPM/platform
│    3 : 4 2   l e f t   │  ← countdown (or P A U S E D) while playing
│                        │
│        03:42           │  ← large set elapsed time (MM:SS), scales to fill
│                        │
└────────────────────────┘
```

- **Ticker** (top): large bold track number prefix on the left, scrolling small text on the right showing `title bpm platform`
- **Countdown** (middle): remaining time for the current track (`M:SS left`) or `PAUSED` — characters are spaced out for readability
- **Set clock** (bottom half): elapsed time since the first track of the set was started, rendered large and scaled to fit

---

## Raspberry Pi Setup

### 1. Enable I2C (for OLED)

```bash
sudo raspi-config
# Interface Options → I2C → Enable
```

Or add to `/boot/firmware/config.txt`:
```
dtparam=i2c_arm=on
```

Verify the OLED is detected at `0x3C`:
```bash
i2cdetect -y 1
```

### 2. PipeWire Audio

The rig uses `sounddevice` which routes through PipeWire. Verify it's running:
```bash
systemctl --user status pipewire pipewire-pulse wireplumber
```

If not running:
```bash
systemctl --user enable --now pipewire pipewire-pulse wireplumber
```

PipeWire must be alive for the user session before `controller.py` starts — the labwc autostart (section 8) handles this.

### 3. Zoom L6 Multi-Channel Routing

The Zoom L6 presents as a multi-channel USB audio device. Connect via USB; no extra drivers needed on Pi OS.

**`AUDIO_DEVICE = None` is the default** — the controller auto-detects the Zoom by searching sounddevice names for `"zoom"`, `"l6"`, or `"l-6"` with at least 4 output channels. No configuration needed in the normal case.

If auto-detect fails or you want to pin a specific device:
```bash
python3 -c "import sounddevice as sd; print(sd.query_devices())"
```

Look for `L6: USB Audio` or `Zoom L-6`, then set the index in `controller.py`:
```python
AUDIO_DEVICE = 2      # pin to a specific device index
```

If the pinned index is missing or has fewer than 4 output channels, the controller warns and falls back to auto-detect. If you see `PaErrorCode -9998` with a pinned index, the enumeration order changed after a reconnect — use `None` instead.

**Zoom L6 must be in 4-ch (multi-track) mode**, not stereo. On the device:
- Menu → USB → Mode → Multi Track

**ALSA config for consistent device naming** — add to `/etc/udev/rules.d/99-zoom.rules`:
```
SUBSYSTEM=="sound", ATTRS{idVendor}=="1686", ATTRS{idProduct}=="0045", ATTR{id}="ZoomL6"
```
Then `sudo udevadm control --reload && sudo udevadm trigger`. Now the device always appears as `hw:ZoomL6` for ALSA-level tools (this does not affect sounddevice auto-detection).

### 4. VirMIDI Kernel Module (MIDI bridge to Processing)

The controller creates a virtual ALSA MIDI port (`RigMIDI`) and bridges it to VirMIDI, which Processing can open as a raw MIDI device.

**Load the module:**
```bash
sudo modprobe snd_virmidi midi_devs=1
```

**Make it persistent** — add to `/etc/modules`:
```
snd_virmidi
```

**Verify it loaded:**
```bash
aconnect -l
# Should show: "Virtual Raw MIDI 0-0" or similar
```

If VirMIDI doesn't appear, or after a fresh clone, run the full setup helper:
```bash
sudo bash ~/rig/patch_midi.sh
```

`patch_midi.sh` does three things:
1. Loads `snd_virmidi` and makes it persistent in `/etc/modules`
2. Rewrites `MidiHandler.java` in the Processing sketch source to open the VirMIDI device instead of a named ALSA port
3. Recompiles `MidiHandler.java` and hot-patches the compiled class back into `sticker_spinner.jar`

This is a one-time operation. After running it, the Processing sketch will automatically connect to VirMIDI on every launch.

### 5. Argon One V5 — Stopping the OLED Daemon

The Argon One daemon controls the case OLED. On startup the rig tries to stop all three possible service names (`argononed`, `argone-oled`, `argonone-led`) and records which ones were actually running. On exit it restarts only those services.

**Grant passwordless systemctl access** (only needed if running as a non-root user — see section 8):
```bash
sudo bash ~/rig/setup_sudoers.sh
```

This writes `/etc/sudoers.d/rig-argon` covering all three service names.

**I2C address:** the Argon One OLED is at `0x3C` on I2C bus 1 (the default).

### 6. Keyboard and I2C Permissions

The controller normally runs as root via the autostart (see section 8), so no group changes are needed for production use.

If you want to run as a non-root user, add yourself to the required groups:

```bash
sudo usermod -a -G input,i2c nmlstyl
# log out and back in for group membership to take effect
```

`evdev` keyboard access requires the `input` group; I2C requires the `i2c` group. Running without root and without these groups will cause keyboard grab and OLED display to fail.

### 7. Desktop Taskbar (labwc / wf-panel-pi)

The rig runs under the labwc Wayland compositor. On startup, `controller.py` kills `lwrespawn wf-panel-pi` and `wf-panel-pi` to hide the taskbar during the performance. On exit it relaunches `lwrespawn wf-panel-pi` to restore it cleanly under its supervisor.

**Do not kill the panel in autostart.** An earlier hack added these lines to `~/.config/labwc/autostart`:
```bash
# BAD — causes panel to glitch / shell to break after controller exits
sleep 1 && pkill -f "lwrespawn.*wf-panel-pi" && pkill wf-panel-pi &
```
Killing `lwrespawn` externally and then trying to restart the panel manually causes it to oscillate between hide/show and eventually the shell stops responding.

The controller handles the panel lifecycle itself — the autostart just needs to launch the controller (see section 8).

### 8. Autostart (recommended) vs Systemd Service

**Use the labwc autostart** (`~/.config/labwc/autostart`) — this is the correct launch mechanism because `controller.py` needs the desktop session (Processing sketch needs a display, taskbar management requires labwc to be running).

The autostart waits for PipeWire before launching:
```bash
bash -c 'until systemctl --user is-active pipewire > /dev/null 2>&1; do sleep 0.5; done; sudo python /home/nmlstyl/rig/controller.py' &
```

**Do NOT enable `performance-rig.service` at the same time.** Running both causes a double-launch: the service fires at boot before the desktop exists, fails, then retries — colliding with the autostart once the desktop loads. Keep the service file disabled:
```bash
sudo systemctl disable performance-rig.service
```

To check it's not running twice:
```bash
pgrep -a python | grep controller
```

### 9. Python Virtual Environment

```bash
bash ~/rig/install_multichannel.sh
```

This creates `~/rig/venv` if needed and installs all dependencies. To install manually:

```bash
cd ~/rig && python3 -m venv venv && source venv/bin/activate
pip install sounddevice soundfile numpy mido python-rtmidi evdev \
            adafruit-circuitpython-ssd1306 pillow
```

---

## Configuration

Edit constants at the top of `controller.py`:

```python
MUSIC_ROOT        = Path("/home/nmlstyl/rig")       # set-*/song-* root
PROCESSING_SKETCH = Path("/home/nmlstyl/sketchbook/sticker_spinner/linux-aarch64/sticker_spinner")
VIRTUAL_MIDI_PORT = "RigMIDI"
AUDIO_DEVICE      = None   # auto-detect Zoom L6 by name (or set to a device index)
KEYBOARD_NAME     = None   # target keyboard substring (None = any arrow-key keyboard)
W, H              = 128, 64  # OLED dimensions
```

If multiple keyboards are attached (e.g. a USB hub plus a built-in), set `KEYBOARD_NAME` to a substring of the target device name:
```python
KEYBOARD_NAME = "USB Keyboard"   # matches any device whose name contains this string
```
Falls back to any keyboard with arrow keys if the named device isn't found.

---

## Troubleshooting

**No keyboard detected**
```bash
sudo usermod -a -G input $USER   # then log out/in
evtest                            # list and test input devices
```

**Keyboard grab fails at startup**
The display server may be holding an exclusive grab on the keyboard. The rig will automatically attempt a USB unbind/rebind to force re-enumeration and break the grab, then reconnect.

**OLED not working**
```bash
sudo raspi-config   # Interface Options → I2C → Enable
i2cdetect -y 1      # should show 0x3C
```

**`Invalid number of channels` / `PaErrorCode -9998`**
Only occurs when `AUDIO_DEVICE` is set to a fixed index and the enumeration order changed after a reconnect. Check:
```bash
python3 -c "import sounddevice as sd; print(sd.query_devices())"
```
Set `AUDIO_DEVICE = None` to use auto-detect instead.

**`ModuleNotFoundError: sounddevice` or `soundfile`**
```bash
source ~/rig/venv/bin/activate
pip install sounddevice soundfile --break-system-packages
```

**MIDI port creation fails**
```bash
sudo apt install python3-rtmidi alsa-utils
pip install python-rtmidi mido --break-system-packages
```

**VirMIDI not found**
```bash
sudo modprobe snd_virmidi
sudo bash ~/rig/patch_midi.sh
aconnect -l   # verify Virtual Raw MIDI appears
```

**Processing sketch doesn't launch**
```bash
chmod +x ~/sketchbook/sticker_spinner/linux-aarch64/sticker_spinner
# test manually:
DISPLAY=:0 ~/sketchbook/sticker_spinner/linux-aarch64/sticker_spinner
```

**Sample rate mismatch**
Zoom L6 runs at 48kHz. Convert files if needed:
```bash
sox input.wav -r 48000 output.wav
soxi your_file.wav | grep "Sample Rate"
```

**Audio clicks or dropouts**
Increase blocksize in `_audio_loop`:
```python
stream = sd.OutputStream(..., blocksize=2048, ...)
```

**Audio and MIDI out of sync**
The MIDI thread is delayed by `blocksize/samplerate` (≈21ms) to compensate for the audio stream's internal buffer. If still drifting, adjust the delay passed to `_midi_loop` in `Player.play()`:
```python
self._midi_t = threading.Thread(target=self._midi_loop, args=(midi, start, 0.030), ...)
# increase the last value if MIDI leads audio
```

**Tracks not found**
```bash
tree ~/rig/ | head -30
# Each song-XX folder needs title.wav, metronome.wav, midi-for-processing.midi
```

**Intermittent boot to terminal instead of GUI**
Caused by a race between Plymouth (boot splash) and lightdm's VT switch. Fix: remove Plymouth entirely.
```bash
sudo apt purge plymouth
```
To diagnose future boot failures, enable persistent journald logs:
```bash
sudo mkdir -p /var/log/journal
sudo systemd-tmpfiles --create --prefix /var/log/journal
sudo systemctl restart systemd-journald
```
Then after a bad boot: `sudo journalctl -b -1 -u lightdm`
