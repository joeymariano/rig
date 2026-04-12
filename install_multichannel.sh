#!/bin/bash
# Install all Python dependencies for the performance rig.
# Run from ~/rig (creates venv if it doesn't exist).

set -e

cd "$(dirname "$0")"

if [ ! -d venv ]; then
    echo "Creating virtual environment..."
    python3 -m venv venv
fi

source venv/bin/activate

echo "Installing dependencies..."
pip install \
    sounddevice soundfile numpy \
    mido python-rtmidi \
    evdev \
    adafruit-circuitpython-ssd1306 pillow

echo ""
echo "Checking for Zoom L6..."
python3 - <<'PYEOF'
import sounddevice as sd
devices = sd.query_devices()
found = False
for i, d in enumerate(devices):
    if any(x in d['name'].lower() for x in ('zoom', 'l6', 'l-6')) and d['max_output_channels'] >= 4:
        print(f"  [{i}] {d['name']}  (out={d['max_output_channels']}, {int(d['default_samplerate'])}Hz)")
        found = True
if not found:
    print("  Zoom L6 not detected — plug it in and check AUDIO_DEVICE in controller.py")
PYEOF

echo ""
echo "Done. Set AUDIO_DEVICE in controller.py if needed."
echo "The autostart uses this venv directly:"
echo "  sudo /home/nmlstyl/rig/venv/bin/python /home/nmlstyl/rig/controller.py"
