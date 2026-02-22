#!/bin/bash
# Performance Rig Setup Script

echo "=============================================="
echo "Performance Rig - Setup & Installation"
echo "=============================================="
echo ""

# Check if mido is properly installed
echo "Checking MIDI setup..."
python3 -c "import mido; print(f'mido version: {mido.__version__}')" 2>/dev/null
if [ $? -ne 0 ]; then
    echo "WARNING: mido shows version 0.0.0 in pip list"
    echo "Reinstalling mido..."
    source ~/rig/venv/bin/activate
    pip uninstall -y mido
    pip install mido --break-system-packages
    echo "mido reinstalled"
fi

# Check MIDI backend
echo ""
echo "Checking MIDI backend..."
python3 -c "import mido; print(f'MIDI backend: {mido.backend}')"

# Test MIDI port creation
echo ""
echo "Testing MIDI port creation..."
python3 << 'PYEOF'
import mido
try:
    port = mido.open_output('TestPort', virtual=True)
    print("✓ Virtual MIDI port creation successful")
    port.close()
except Exception as e:
    print(f"✗ Error creating virtual MIDI port: {e}")
    print("  You may need to install python3-rtmidi:")
    print("  sudo apt install python3-rtmidi")
PYEOF

echo ""
echo "=============================================="
echo "Installation Instructions:"
echo "=============================================="
echo ""
echo "1. Copy controller.py to your rig directory:"
echo "   cp controller.py ~/rig/"
echo ""
echo "2. Make it executable:"
echo "   chmod +x ~/rig/controller.py"
echo ""
echo "3. Test run manually:"
echo "   cd ~/rig"
echo "   source venv/bin/activate"
echo "   python controller.py"
echo ""
echo "4. If everything works, install systemd service:"
echo "   sudo cp performance-rig.service /etc/systemd/system/"
echo "   sudo systemctl daemon-reload"
echo "   sudo systemctl enable performance-rig.service"
echo "   sudo systemctl start performance-rig.service"
echo ""
echo "5. Check status:"
echo "   sudo systemctl status performance-rig.service"
echo ""
echo "6. View logs:"
echo "   journalctl -u performance-rig.service -f"
echo ""
echo "=============================================="
echo "Troubleshooting:"
echo "=============================================="
echo ""
echo "If MIDI port creation fails:"
echo "  sudo apt install python3-rtmidi"
echo ""
echo "If keyboard not detected:"
echo "  - Check with: evtest (sudo apt install evtest)"
echo "  - Make sure your user is in the 'input' group"
echo "  - sudo usermod -a -G input pi"
echo ""
echo "If OLED not working:"
echo "  - Check I2C is enabled: sudo raspi-config"
echo "  - Check I2C address: i2cdetect -y 1"
echo "  - Should see 0x3C"
echo ""
echo "If Processing sketch doesn't launch:"
echo "  - Check path: ~/sketchbook/sticker_spinner/linux-aarch64/sticker_spinner"
echo "  - Make executable: chmod +x ~/sketchbook/sticker_spinner/linux-aarch64/sticker_spinner"
echo "  - Test manually: ~/sketchbook/sticker_spinner/linux-aarch64/sticker_spinner"
echo ""
