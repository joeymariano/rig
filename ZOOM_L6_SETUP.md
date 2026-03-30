# Zoom L6 Multi-Channel Audio Routing

## Overview

The controller routes audio to separate Zoom L6 outputs:
- `title.wav` → Outputs 1-2 (stereo pair)
- `metronome.wav` → Outputs 3-4 (stereo pair)

This uses `sounddevice` for 4-channel interleaved output:
```
Channel 1 (Output 1) ← title.wav LEFT
Channel 2 (Output 2) ← title.wav RIGHT
Channel 3 (Output 3) ← metronome.wav LEFT
Channel 4 (Output 4) ← metronome.wav RIGHT
```

## Setup

### Step 1: Install dependencies

```bash
bash ~/rig/install_multichannel.sh
```

Installs `sounddevice`, `soundfile`, and `numpy`, then prints available audio devices.

### Step 2: Set the device

Find the Zoom L6 in the device list:
```bash
python3 -c "import sounddevice as sd; print(sd.query_devices())"
```

Update `AUDIO_DEVICE` in `controller.py`:
```python
AUDIO_DEVICE = 2      # device index from above
# or
AUDIO_DEVICE = None   # auto-detect by name (searches for "zoom"/"l6")
```

### Step 3: Set Zoom L6 to multi-track mode

On the device: Menu → USB → Mode → **Multi Track**

Without this the device only presents 2 channels and playback will fail.

## Troubleshooting

### `Invalid number of channels` / `PaErrorCode -9998`

USB device enumeration order changed. Check the current index:
```bash
python3 -c "import sounddevice as sd; print(sd.query_devices())"
```
Update `AUDIO_DEVICE` to match, or set it to `None` to always auto-detect.

### `ModuleNotFoundError: No module named 'sounddevice'`
```bash
source ~/rig/venv/bin/activate
pip install sounddevice soundfile --break-system-packages
```

### Zoom L6 only shows 2 output channels

The device is in stereo mode. Switch to Multi Track mode as above.

### Audio clicks or dropouts

Increase the blocksize in `_audio_loop` in `controller.py`:
```python
stream = sd.OutputStream(device=device, channels=4, samplerate=sr, blocksize=2048, ...)
```

### Sample rate mismatch

The Zoom L6 runs at 48kHz. Convert files if needed:
```bash
soxi your_file.wav | grep "Sample Rate"
sox input.wav -r 48000 output.wav
```

### Audio and MIDI out of sync

The MIDI thread is delayed by `blocksize/samplerate` (~21ms) to compensate for the audio buffer. If MIDI still leads audio, increase the delay in `Player.play()`:
```python
self._midi_t = threading.Thread(target=self._midi_loop, args=(midi, start, 0.030), ...)
```

### Verify channel mapping

```bash
speaker-test -D hw:CARD=L6 -c 4 -t sine
# Plays: Front-Left, Front-Right, Rear-Left, Rear-Right
```

### Stable device naming (optional)

Add to `/etc/udev/rules.d/99-zoom.rules`:
```
SUBSYSTEM=="sound", ATTRS{idVendor}=="1686", ATTRS{idProduct}=="0045", ATTR{id}="ZoomL6"
```
Then:
```bash
sudo udevadm control --reload && sudo udevadm trigger
```
The device will always appear as `hw:ZoomL6` regardless of USB enumeration order.
