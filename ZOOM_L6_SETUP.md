# Zoom L6 Multi-Channel Audio Routing Setup

## Overview

The controller now supports **TRUE MULTI-CHANNEL ROUTING**:
- **title.wav** → Zoom L6 Outputs 1-2 (stereo pair)
- **metronome.wav** → Zoom L6 Outputs 3-4 (stereo pair)

This uses the `sounddevice` library for 4-channel audio output.

## Quick Setup

### Step 1: Install Multi-Channel Dependencies

```bash
cd ~/rig
source venv/bin/activate
bash install_multichannel.sh
```

This installs:
- `sounddevice` - Multi-channel audio output
- `soundfile` - WAV file reading
- `numpy` - Audio data processing

### Step 2: Detect Your Zoom L6

After installation, the script will show available devices. Look for:
```
✓ Found Zoom L6: Zoom L-6
  Device ID: 1
  Max output channels: 6
  Default sample rate: 48000.0
```

### Step 3: Configure controller.py

Edit `controller.py` and update `AUDIO_DEVICE`:

```python
# Option 1: By device name (recommended)
AUDIO_DEVICE = "Zoom L-6"

# Option 2: By device ID number
AUDIO_DEVICE = 1

# Option 3: Auto-detect (searches for "zoom" or "l6" in name)
AUDIO_DEVICE = None
```

### Step 4: Test

```bash
cd ~/rig
source venv/bin/activate
python controller.py
```

You should see:
```
Using sounddevice for multi-channel audio
Auto-detected Zoom L6: Zoom L-6 (device 1)
Starting 4-channel playback on device 1
  Channels 1-2: title.wav
  Channels 3-4: metronome.wav
```

## How It Works

The controller creates a 4-channel audio stream:
```
Channel 1 (Output 1) ← title.wav LEFT
Channel 2 (Output 2) ← title.wav RIGHT
Channel 3 (Output 3) ← metronome.wav LEFT
Channel 4 (Output 4) ← metronome.wav RIGHT
```

## Fallback Mode

If `sounddevice` is not installed, the controller falls back to pygame mixer, which mixes both tracks together (no channel separation). You'll see:

```
WARNING: sounddevice not installed. Falling back to pygame
Playback started (pygame - mixed stereo)
```

To get multi-channel routing, you must install sounddevice.

## Troubleshooting

### "ModuleNotFoundError: No module named 'sounddevice'"
```bash
source ~/rig/venv/bin/activate
pip install sounddevice soundfile --break-system-packages
```

### "Invalid number of channels" / `PaErrorCode -9998`

The hardcoded `AUDIO_DEVICE` index (e.g. `2`) pointed to a device that doesn't
support 4-channel output. This happens when USB device enumeration order changes
after a reboot or USB reconnect.

The controller now validates the device channel count and prints available devices
if the check fails, then auto-detects by name. Check the output for the correct index:

```
Available output devices:
  [0] bcm2835 Headphones  (out=8)
  [1] ...
  [2] L6: USB Audio       (out=4)   ← use this index
```

Update `AUDIO_DEVICE` in `controller.py` to match, or set `AUDIO_DEVICE = None`
to always auto-detect by name.

### "No audio device found" or Wrong Device Selected
```bash
# List all devices
python3 -c "import sounddevice as sd; print(sd.query_devices())"

# Find Zoom L6 in the list, note device ID or name
# Update AUDIO_DEVICE in controller.py
```

### "Zoom L6 only has 2 output channels"
Your Zoom L6 might be configured in stereo mode. Check:
```bash
# Check ALSA config
aplay -l | grep -i zoom

# The device should show as surround40 or multi-channel
# If not, you may need to configure ALSA
```

### Audio Clicks or Dropouts
Increase buffer size in the audio playback thread:
```python
# In controller.py, _play_audio_thread method:
stream = self.sd.OutputStream(
    device=device,
    channels=4,
    samplerate=samplerate,
    blocksize=2048,  # Increase from 1024
    dtype='float32'
)
```

### Sample Rate Mismatch Warning
Ensure your WAV files are all 48kHz (Zoom L6's native rate):
```bash
# Check file sample rate
soxi your_file.wav | grep "Sample Rate"

# Convert if needed
sox input.wav -r 48000 output.wav
```

### Latency Between Audio and MIDI
The audio and MIDI start simultaneously in separate threads. If you notice drift:
- Audio has ~20ms latency (blocksize 1024 at 48kHz)
- MIDI is real-time

If sync is critical, you can add a small delay to MIDI:
```python
# In _play_midi_thread, add after start_time:
time.sleep(0.020)  # 20ms to match audio latency
```

### Channels Reversed or Wrong Mapping
Verify your Zoom L6 channel mapping:
```bash
# Test individual channels
speaker-test -D hw:CARD=L6 -c 4 -t sine
# Should play: Front-Left, Front-Right, Rear-Left, Rear-Right
```

If channels are wrong, you may need to remap in the code:
```python
# In _play_sounddevice method, adjust channel order:
output_data = np.column_stack([
    title_data[:, 0],      # Your desired Output 1
    title_data[:, 1],      # Your desired Output 2
    metronome_data[:, 0],  # Your desired Output 3
    metronome_data[:, 1]   # Your desired Output 4
])
```
