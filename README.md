# Live Performance Rig

Python controller for Raspberry Pi (Argon One V5) that synchronizes:
- 2 audio tracks routed to separate Zoom L6 outputs
- MIDI file playback driving a Processing visual sketch
- SSD1306 OLED display (built into Argon case)
- USB keyboard control (4 arrow keys)

## System Architecture

```
USB Keyboard (←→↑↓)
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
│   │   └── bpm.txt                # optional, e.g. "120"
│   └── song-02/...
└── set-02/...
```

## Controls

| Key | Action |
|-----|--------|
| `←` | Previous track |
| `→` | Next track |
| `↓` | Play (starts audio + MIDI simultaneously) |
| `↑` | Pause / Resume |
| `ESC` | Exit (graceful shutdown) |
| `↑` + `←` + `→` | Exit combo (hold all three) |

Combo keys (`↑ ← →`) are non-destructive: if the combo is never completed, each key fires its normal action on release.

## OLED Layout

```
┌────────────────────────┐
│ Track 1/8              │  position
│ Set 1 - Song 1         │  set/song number
│ song-01                │  folder name
│ > PLAYING              │  status
└────────────────────────┘
```

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

To keep PipeWire alive for root (the rig runs as root):
```bash
# Add to /etc/systemd/system/performance-rig.service environment:
Environment=XDG_RUNTIME_DIR=/run/user/1000
```
See the service file section below.

### 3. Zoom L6 Multi-Channel Routing

The Zoom L6 presents as a 6-channel USB audio device. Connect via USB; no extra drivers needed on Pi OS.

**Find the device index after connecting:**
```bash
python3 -c "import sounddevice as sd; print(sd.query_devices())"
```

Look for `L6: USB Audio` or `Zoom L-6`. Update `AUDIO_DEVICE` in `controller.py`:
```python
AUDIO_DEVICE = 2      # use device index from above
# or
AUDIO_DEVICE = None   # auto-detect by name (searches for "zoom"/"l6")
```

**USB enumeration order can change after reboot.** If you see `PaErrorCode -9998`, the
index is wrong. Set `AUDIO_DEVICE = None` to always auto-detect, or check:
```bash
aplay -l | grep -i zoom
```

**Zoom L6 must be in 4-ch (multi-track) mode**, not stereo. On the device:
- Menu → USB → Mode → Multi Track

**ALSA config for consistent device naming** — add to `/etc/udev/rules.d/99-zoom.rules`:
```
SUBSYSTEM=="sound", ATTRS{idVendor}=="1686", ATTRS{idProduct}=="0045", ATTR{id}="ZoomL6"
```
Then `sudo udevadm control --reload && sudo udevadm trigger`. Now the device always appears as `hw:ZoomL6`.

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

Or add to `/etc/modprobe.d/virmidi.conf`:
```
options snd_virmidi midi_devs=1
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

The Argon One daemon (`argononed`) controls the case OLED. The rig stops it on startup
to take exclusive control, then restarts it on exit.

**Grant passwordless systemctl access** (so the rig can stop/start the service without a password):
```bash
sudo bash ~/rig/setup_sudoers.sh
```

This writes `/etc/sudoers.d/rig-argon` with:
```
nmlstyl ALL=(ALL) NOPASSWD: /bin/systemctl stop argononed, /bin/systemctl start argononed, \
    /bin/systemctl stop argone-oled, /bin/systemctl start argone-oled
```

**I2C address:** the Argon One OLED is at `0x3C` on I2C bus 1 (the default).

### 6. Keyboard and I2C Permissions

`evdev` keyboard access requires the running user to be in the `input` group; I2C requires `i2c` group membership (or root).

```bash
sudo usermod -a -G input,i2c nmlstyl
# log out and back in for group membership to take effect
```

For a quick manual test you can still run as root:
```bash
sudo python3 ~/rig/controller.py
```

### 7. Desktop Taskbar (labwc / wf-panel-pi)

The rig runs under the labwc Wayland compositor with the `wf-panel-pi` taskbar. The taskbar should be left running — it does not interfere with controller.py.

**Do not kill the panel in autostart.** An earlier hack added these lines to `~/.config/labwc/autostart`:
```bash
# BAD — causes panel to glitch / shell to break after controller exits
sleep 1 && pkill -f "lwrespawn.*wf-panel-pi" && pkill wf-panel-pi &
```
Killing `lwrespawn` (the panel supervisor) and then trying to restart the panel manually via `wf-panel-pi` causes the panel to oscillate between hide/show and eventually the shell stops responding.

**Correct autostart** (`~/.config/labwc/autostart`):
```bash
bash -c 'until systemctl --user is-active pipewire > /dev/null 2>&1; do sleep 0.5; done; sudo python /home/nmlstyl/rig/controller.py' &
```
This polls until PipeWire is ready before launching, avoiding the race condition from a fixed `sleep`. `controller.py` then manages the panel directly:
- On startup: kills `lwrespawn wf-panel-pi` + `wf-panel-pi` so the taskbar is hidden during the performance
- On exit: relaunches `lwrespawn wf-panel-pi` to restore it cleanly under its supervisor

This mirrors how the rig already handles the argon OLED daemon.

### 8. Autostart (recommended) vs Systemd Service

**Use the labwc autostart** (`~/.config/labwc/autostart`) — this is the correct launch mechanism because controller.py needs the desktop session (Processing sketch needs a display, taskbar management requires labwc to be running).

The autostart waits for PipeWire before launching:
```bash
bash -c 'until systemctl --user is-active pipewire > /dev/null 2>&1; do sleep 0.5; done; sudo python /home/nmlstyl/rig/controller.py' &
```

**Do NOT enable `performance-rig.service` at the same time.** Running both causes a double-launch: the service fires at boot before the desktop exists, fails, then retries — colliding with the autostart once the desktop loads.

The service file is kept as a reference but should remain **disabled**:
```bash
sudo systemctl disable performance-rig.service
```

To check it's not running twice:
```bash
pgrep -a python | grep controller
```

### 9. Python Virtual Environment

```bash
cd ~/rig
python3 -m venv venv
source venv/bin/activate
pip install sounddevice soundfile numpy mido python-rtmidi evdev \
            adafruit-circuitpython-ssd1306 pillow
```

Or run the installer:
```bash
bash ~/rig/install_multichannel.sh
```

---

## Configuration

Edit constants at the top of `controller.py`:

```python
MUSIC_ROOT        = Path("/home/nmlstyl/rig")       # set-*/song-* root
PROCESSING_SKETCH = Path("/home/nmlstyl/sketchbook/sticker_spinner/linux-aarch64/sticker_spinner")
VIRTUAL_MIDI_PORT = "RigMIDI"
AUDIO_DEVICE      = 2      # Zoom L6 index (None = auto-detect)
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

**OLED not working**
```bash
sudo raspi-config   # Interface Options → I2C → Enable
i2cdetect -y 1      # should show 0x3C
```

**`Invalid number of channels` / `PaErrorCode -9998`**
Device index changed after reconnect. Check:
```bash
python3 -c "import sounddevice as sd; print(sd.query_devices())"
```
Update `AUDIO_DEVICE` or set to `None`.

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
The MIDI thread is delayed by `blocksize/samplerate` (≈21ms) to compensate for the
audio stream's internal buffer. If still drifting, adjust:
```python
# In Player.play():
delay = 0.030   # increase if MIDI leads audio
```

**Tracks not found**
```bash
tree ~/rig/ | head -30
# Each song-XX folder needs title.wav, metronome.wav, midi-for-processing.midi
```

**Intermittent boot to terminal instead of GUI**
Caused by a race between Plymouth (boot splash) and lightdm's VT switch. Plymouth
runs on VT1 but lightdm requires VT7 — if Plymouth doesn't quit in time, the session
fails and falls back to a getty. Fix: remove Plymouth entirely.
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
