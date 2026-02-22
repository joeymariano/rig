#!/bin/bash
# Install dependencies for multi-channel Zoom L6 audio routing

echo "=============================================="
echo "Installing Multi-Channel Audio Dependencies"
echo "=============================================="
echo ""

cd ~/rig
source venv/bin/activate

echo "Installing sounddevice (for multi-channel routing)..."
pip install sounddevice --break-system-packages

echo ""
echo "Installing soundfile (for WAV file reading)..."
pip install soundfile --break-system-packages

echo ""
echo "Checking numpy (should already be installed)..."
pip install numpy --break-system-packages

echo ""
echo "=============================================="
echo "Installation Complete"
echo "=============================================="
echo ""
echo "Testing audio device detection..."
python3 << 'PYEOF'
import sounddevice as sd
print("\nAvailable audio devices:")
print(sd.query_devices())
print("\n")
print("Looking for Zoom L6...")
devices = sd.query_devices()
found = False
for i, device in enumerate(devices):
    name = device['name'].lower()
    if 'zoom' in name or 'l6' in name or 'l-6' in name:
        print(f"✓ Found Zoom L6: {device['name']}")
        print(f"  Device ID: {i}")
        print(f"  Max output channels: {device['max_output_channels']}")
        print(f"  Default sample rate: {device['default_samplerate']}")
        found = True

if not found:
    print("✗ Zoom L6 not detected")
    print("  Make sure it's plugged in via USB")
PYEOF

echo ""
echo "=============================================="
echo "Next Steps:"
echo "=============================================="
echo ""
echo "1. If Zoom L6 was detected above, note the device name"
echo "2. Edit controller.py and set AUDIO_DEVICE:"
echo "   AUDIO_DEVICE = \"Zoom L-6\"  # Use exact name from above"
echo "   # or"
echo "   AUDIO_DEVICE = 1  # Use device ID number"
echo ""
echo "3. Test the controller:"
echo "   python controller.py"
echo ""
echo "The controller will now route:"
echo "  - title.wav → Zoom L6 Outputs 1-2"
echo "  - metronome.wav → Zoom L6 Outputs 3-4"
echo ""
