# Quick Start Guide

## FOR ZOOM L6 USERS - MULTI-CHANNEL ROUTING

Your setup routes audio to separate outputs:
- title.wav → Outputs 1-2
- metronome.wav → Outputs 3-4

### Step 1: Install Multi-Channel Support
```bash
cd ~/rig
source venv/bin/activate
bash install_multichannel.sh
```

This installs `sounddevice`, `soundfile`, and `numpy` for 4-channel output.

### Step 2: Copy the controller
```bash
cp controller.py ~/rig/
chmod +x ~/rig/controller.py
```

### Step 3: Configure for Zoom L6

Edit `~/rig/controller.py` and set:
```python
AUDIO_DEVICE = "Zoom L-6"  # Use exact device name
# or
AUDIO_DEVICE = 1  # Use device ID from install script output
```

### Step 4: Fix mido if needed
Your pip list shows `mido 0.0.0` which might be broken:
```bash
source ~/rig/venv/bin/activate
pip uninstall -y mido
pip install mido --break-system-packages
```

### Step 5: Test run
```bash
cd ~/rig
source venv/bin/activate
python controller.py
```

## What You Should See:
```
Using sounddevice for multi-channel audio
Auto-detected Zoom L6: Zoom L-6 (device 1)
Created virtual MIDI port: RigMIDI
Found X tracks
Processing sketch launched
Performance Rig ready!

[Press DOWN to play]

Starting 4-channel playback on device 1
  Channels 1-2: title.wav
  Channels 3-4: metronome.wav
  Sample rate: 48000Hz
```

## Testing:
1. Press `←` or `→` to browse tracks (OLED updates)
2. Press `↓` to play
   - title.wav plays from Zoom L6 Outputs 1-2
   - metronome.wav plays from Zoom L6 Outputs 3-4
   - MIDI sends to Processing sketch
3. Press `↑` to pause/resume
4. Press `Ctrl+C` to exit

## If It Works:
Install as a service so it auto-starts on boot:
```bash
sudo cp performance-rig.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable performance-rig.service
sudo systemctl start performance-rig.service
```

## Troubleshooting:

**Keyboard not detected:**
```bash
sudo usermod -a -G input $USER
# Then log out and back in
```

**MIDI port creation fails:**
```bash
sudo apt install python3-rtmidi
pip install python-rtmidi --break-system-packages
```

**No tracks found:**
Check your directory structure:
```bash
tree ~/rig/ | head -20
```

Should be:
```
~/rig/set-01/song-01/title.wav
~/rig/set-01/song-01/metronome.wav
~/rig/set-01/song-01/midi-for-processing.midi
```

**OLED not working:**
```bash
# Enable I2C
sudo raspi-config  # Interface Options → I2C → Enable

# Check address
i2cdetect -y 1  # Should show 0x3C
```

**Processing sketch doesn't launch:**
```bash
chmod +x ~/sketchbook/sticker_spinner/linux-aarch64/sticker_spinner
# Test manually:
~/sketchbook/sticker_spinner/linux-aarch64/sticker_spinner
```

## Files Provided:
- `controller.py` - Main Python controller
- `performance-rig.service` - Systemd service for auto-start
- `setup.sh` - Diagnostic and setup helper
- `README.md` - Complete documentation
- `QUICKSTART.md` - This file

## Controls Reference:
- `←` LEFT: Previous track
- `→` RIGHT: Next track  
- `↓` DOWN: Play (starts 2 WAVs + MIDI)
- `↑` UP: Pause/Resume

The OLED shows:
- Current track position (e.g., "Track 3/8")
- Set and song number (e.g., "Set 1 - Song 2")
- Folder name
- Status (PLAYING / PAUSED / Press DOWN to play)
