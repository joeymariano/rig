# Live Performance Rig Controller

A Python-based live performance system for Raspberry Pi that synchronizes:
- 2 audio tracks (title.wav + metronome.wav)
- MIDI file playback (for Processing visuals)
- OLED display (Argon case)
- USB keyboard control (4-button arrow pad)

## System Architecture

```
┌─────────────────┐
│  USB Keyboard   │ (Arrow keys: ←→↑↓)
│  (4 buttons)    │
└────────┬────────┘
         │
┌────────▼────────────────────────────────────────┐
│                                                  │
│            controller.py (Python)                │
│                                                  │
│  ┌──────────────┐  ┌─────────────────────────┐ │
│  │   Keyboard   │  │    Track Manager        │ │
│  │   Handler    │  │  (scans ~/rig/set-*/    │ │
│  └──────────────┘  │   song-* directories)   │ │
│                    └─────────────────────────┘ │
│  ┌──────────────┐  ┌─────────────────────────┐ │
│  │     OLED     │  │   Audio/MIDI Player     │ │
│  │   Display    │  │  - pygame (2 channels)  │ │
│  │  (SSD1306)   │  │  - mido (MIDI output)   │ │
│  └──────────────┘  └─────────────────────────┘ │
│                                                  │
└──────────────┬───────────────────────┬───────────┘
               │                       │
               │ Launches              │ MIDI: "RigMIDI"
               │                       │ virtual port
               │                       │
        ┌──────▼─────────┐    ┌────────▼────────┐
        │   Processing   │◄───┤  MIDI Messages  │
        │     Sketch     │    └─────────────────┘
        │ (sticker_      │
        │  spinner)      │
        └────────────────┘
               │
        ┌──────▼──────┐
        │ HDMI Output │
        │  (Visuals)  │
        └─────────────┘
```

## File Structure

Your music files should be organized as:

```
~/rig/
├── set-01/
│   ├── song-01/
│   │   ├── title.wav
│   │   ├── metronome.wav
│   │   └── midi-for-processing.midi
│   ├── song-02/
│   │   ├── title.wav
│   │   ├── metronome.wav
│   │   └── midi-for-processing.midi
│   └── ...
├── set-02/
│   └── ...
└── venv/
    └── (your Python virtual environment)
```

## Control Mapping

| Button | Function |
|--------|----------|
| `←` LEFT  | Previous track |
| `→` RIGHT | Next track |
| `↓` DOWN  | Play current track (starts 2 WAVs + MIDI simultaneously) |
| `↑` UP    | Pause/Resume playback |

## OLED Display Shows

```
┌────────────────────────┐
│ Track 1/8              │  ← Position in playlist
│ Set 1 - Song 1         │  ← Set/Song number
│ song-01                │  ← Folder name
│ > PLAYING              │  ← Status
└────────────────────────┘
```

Status indicators:
- `Press DOWN to play` - Ready to start
- `> PLAYING` - Currently playing
- `|| PAUSED` - Playback paused

## How It Works

### Startup Sequence
1. Controller launches Processing sketch (`~/sketchbook/sticker_spinner/linux-aarch64/sticker_spinner`)
2. Creates virtual MIDI port "RigMIDI"
3. Processing connects to MIDI as "Real Time Sequencer"
4. Scans ~/rig/ for track directories
5. Shows first track on OLED
6. Waits for keyboard input

### Playback Flow
When you press `↓ DOWN`:
1. Loads `title.wav` and `metronome.wav` into pygame mixer
2. Loads `midi-for-processing.midi` using mido
3. **Simultaneously** starts:
   - Audio channel 0: title.wav
   - Audio channel 1: metronome.wav
   - MIDI thread: sends MIDI messages to "RigMIDI" port
4. Processing receives MIDI messages in real-time
5. OLED updates to show "PLAYING" status

### Track Navigation
- `← →` changes tracks without stopping playback
- If playing, track stops before switching
- OLED immediately shows new track info

## Installation

### 1. Copy Files
```bash
# Copy controller to your rig directory
cp controller.py ~/rig/

# Make it executable
chmod +x ~/rig/controller.py
```

### 2. Root Permission & Sudoers Setup

`controller.py` must run as root (for evdev keyboard access and I2C/OLED).

It also needs to stop/start the `argononed` service to prevent OLED conflicts. Grant
passwordless `systemctl` access for just those services:

```bash
sudo bash ~/rig/setup_sudoers.sh
```

This writes `/etc/sudoers.d/rig-argon` allowing your user to stop/start
`argononed` and `argone-oled` without a password prompt.

Run the rig as root:
```bash
sudo python3 ~/rig/controller.py
```

### 3. Test Manually
```bash
cd ~/rig
sudo python3 controller.py
```

You should see:
```
Found keyboard: [your keyboard name]
Created virtual MIDI port: RigMIDI
Found 8 tracks
  1. Set 1 - Song 1
  2. Set 1 - Song 2
  ...
Processing sketch launched (PID: 12345)
Performance Rig ready!
```

### 3. Install as System Service (Optional)
```bash
# Copy service file
sudo cp performance-rig.service /etc/systemd/system/

# Enable and start
sudo systemctl daemon-reload
sudo systemctl enable performance-rig.service
sudo systemctl start performance-rig.service

# Check status
sudo systemctl status performance-rig.service

# View live logs
journalctl -u performance-rig.service -f
```

## Requirements

All dependencies should already be in your venv:
- `pygame` - Audio playback (2 channels)
- `mido` - MIDI file reading/playback
- `python-rtmidi` - Virtual MIDI port creation
- `evdev` - USB keyboard input
- `adafruit-circuitpython-ssd1306` - OLED display
- `Pillow` - Image/font rendering for OLED

## Configuration

Edit these constants in `controller.py` if needed:

```python
MUSIC_ROOT = Path.home() / "rig"  # Where your set-*/song-* folders are
PROCESSING_SKETCH = Path.home() / "sketchbook/sticker_spinner/linux-aarch64/sticker_spinner"
VIRTUAL_MIDI_PORT = "RigMIDI"  # Internal MIDI port name
DISPLAY_WIDTH = 128  # OLED width
DISPLAY_HEIGHT = 64  # OLED height
```

## Troubleshooting

### "No keyboard with arrow keys found"
```bash
# List input devices
sudo apt install evtest
evtest

# Add your user to input group
sudo usermod -a -G input $USER
# Log out and back in
```

### "Error opening MIDI port"
Your `mido` installation shows version 0.0.0, which might indicate an issue:

```bash
source ~/rig/venv/bin/activate
pip uninstall mido
pip install mido --break-system-packages

# If still issues, install rtmidi
sudo apt install python3-rtmidi
pip install python-rtmidi --break-system-packages
```

### OLED Not Working
```bash
# Enable I2C
sudo raspi-config
# Interface Options → I2C → Enable

# Check I2C address (should see 0x3C)
i2cdetect -y 1

# If not showing, check connections
```

### Processing Sketch Not Launching
```bash
# Check path exists
ls -la ~/sketchbook/sticker_spinner/linux-aarch64/sticker_spinner

# Make executable
chmod +x ~/sketchbook/sticker_spinner/linux-aarch64/sticker_spinner

# Test manually
~/sketchbook/sticker_spinner/linux-aarch64/sticker_spinner
```

### Audio/MIDI Not Synced
The system uses pygame for audio and a separate thread for MIDI. They start simultaneously but:
- Audio latency: ~10ms (buffer=512 at 48kHz)
- MIDI has no latency (direct output)

If sync is off, you can adjust:
```python
pygame.mixer.init(frequency=48000, size=-16, channels=2, buffer=512)
# Increase buffer for more stability: buffer=1024
# Decrease for lower latency: buffer=256
```

### Tracks Not Found
```bash
# Check directory structure
tree ~/rig/

# Should look like:
# rig/
# ├── set-01/
# │   └── song-01/
# │       ├── title.wav
# │       ├── metronome.wav
# │       └── midi-for-processing.midi
```

Make sure:
- Folders are named `set-XX` and `song-XX`
- All three files exist in each song folder
- WAV files are readable (24-bit is fine)

## Future Enhancements

The UP button currently pauses, but your notes mention:
> "up arrow will pause the track or swap the main file for a drumless version in a future feature"

To implement drum-less switching:
1. Add `title-nodrums.wav` to each song folder
2. Modify `Track` class to detect this file
3. Update `AudioMidiPlayer.play()` to use alternate file
4. Change UP button handler to toggle between versions

## Technical Notes

### MIDI Port Naming
- Python creates: `"RigMIDI"` (virtual port)
- Processing expects: `"Real Time Sequencer"`

Your Processing sketch currently looks for "Real Time Sequencer" by index:
```java
midiBus = new MidiBus("Real Time Sequencer", 0, -1);
```

The Python controller creates "RigMIDI" which should appear as index 0 in Processing's MIDI input list. If Processing can't find it, check:

```bash
# List MIDI ports
aconnect -l
# or
amidi -l
```

### PipeWire Integration
You mentioned you've already set up PipeWire. The controller uses pygame which should automatically work with PipeWire. If you have issues:

```bash
# Check PipeWire is running
systemctl --user status pipewire

# Route audio through specific device if needed
pw-cli list-objects | grep -i audio
```

## Stopping the Service

```bash
# Stop service
sudo systemctl stop performance-rig.service

# Disable auto-start
sudo systemctl disable performance-rig.service

# Run manually instead
cd ~/rig
source venv/bin/activate
python controller.py
```

## License

Custom performance rig for live shows. Modify as needed for your setup.
