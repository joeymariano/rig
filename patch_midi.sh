#!/bin/bash
# patch_midi.sh
# Sets up the full MIDI pipeline for the performance rig:
#   controller.py → RigMIDI (ALSA seq) → aconnect → VirMIDI (raw MIDI) → Java → Processing
#
# Run once with sudo: sudo bash ~/rig/patch_midi.sh

set -e

# Resolve the real user's home even when run with sudo
REAL_HOME=$(eval echo ~${SUDO_USER:-$USER})
SKETCH_DIR="$REAL_HOME/sketchbook/sticker_spinner"
BUILD_DIR="$SKETCH_DIR/linux-aarch64"
JAVAC="$BUILD_DIR/java/bin/javac"
JAR_TOOL="$BUILD_DIR/java/bin/jar"
SKETCH_JAR="$BUILD_DIR/lib/sticker_spinner.jar"
CORE_JAR="$BUILD_DIR/lib/core-4.5.2.jar"
SOURCE_DIR="$BUILD_DIR/source"

# ── 1. Load snd_virmidi now and on every boot ────────────────────────────────
echo "==> Loading snd_virmidi..."
modprobe snd_virmidi

if ! grep -q snd_virmidi /etc/modules 2>/dev/null; then
    echo snd_virmidi >> /etc/modules
    echo "==> Added snd_virmidi to /etc/modules (loads on boot)"
else
    echo "==> snd_virmidi already in /etc/modules"
fi

# ── 2. Patch MidiHandler.java to use VirMIDI ────────────────────────────────
echo "==> Patching source files..."

PATCHED_CONTENT='import javax.sound.midi.*;

public class MidiHandler {
  private MidiDevice device;
  private sticker_spinner parent;

  public MidiHandler(sticker_spinner parent) {
    this.parent = parent;
  }

  public void setup() {
    try {
      MidiDevice.Info[] infos = MidiSystem.getMidiDeviceInfo();

      System.out.println("Available MIDI devices:");
      for (int i = 0; i < infos.length; i++) {
        MidiDevice dev = MidiSystem.getMidiDevice(infos[i]);
        System.out.println("  [" + i + "] " + infos[i].getName()
            + " maxTx=" + dev.getMaxTransmitters());
      }

      // Open the first VirMIDI device that can transmit to us.
      // VirMIDI bridges the ALSA sequencer port (RigMIDI, from controller.py)
      // to a raw MIDI device that Java can enumerate.
      for (int i = 0; i < infos.length; i++) {
        String name = infos[i].getName();
        if (name.startsWith("VirMIDI")) {
          MidiDevice dev = MidiSystem.getMidiDevice(infos[i]);
          if (dev.getMaxTransmitters() != 0) {
            device = dev;
            device.open();
            device.getTransmitter().setReceiver(new Receiver() {
              public void send(MidiMessage msg, long timestamp) {
                if (msg instanceof ShortMessage) {
                  ShortMessage sm = (ShortMessage) msg;
                  if (sm.getCommand() == ShortMessage.NOTE_ON && sm.getData2() > 0) {
                    parent.handleMidiInput(sm.getData1(), sm.getData2());
                  }
                }
              }
              public void close() {}
            });
            System.out.println("MIDI: Connected to " + name);
            return;
          }
        }
      }
      System.out.println("MIDI: VirMIDI not found — is snd_virmidi loaded?");
    } catch (Exception e) {
      System.out.println("MIDI error: " + e);
    }
  }

  public void close() {
    if (device != null) device.close();
  }
}'

echo "$PATCHED_CONTENT" > "$SKETCH_DIR/MidiHandler.java"
echo "$PATCHED_CONTENT" > "$SOURCE_DIR/MidiHandler.java"

# ── 3. Recompile and update JAR ──────────────────────────────────────────────
echo "==> Setting up temp build directory..."
TMPDIR=$(mktemp -d)
trap "rm -rf $TMPDIR" EXIT

echo "==> Extracting sticker_spinner.class for compilation..."
cd "$TMPDIR"
"$JAR_TOOL" xf "$SKETCH_JAR" sticker_spinner.class

echo "==> Compiling MidiHandler.java..."
"$JAVAC" \
    -cp "$TMPDIR:$SKETCH_JAR:$CORE_JAR" \
    -source 11 -target 11 \
    -d "$TMPDIR" \
    "$SOURCE_DIR/MidiHandler.java"

echo "==> Updating JAR..."
"$JAR_TOOL" uf "$SKETCH_JAR" -C "$TMPDIR" MidiHandler.class
for f in "$TMPDIR"/MidiHandler\$*.class; do
    [ -f "$f" ] && "$JAR_TOOL" uf "$SKETCH_JAR" -C "$TMPDIR" "$(basename $f)"
done

echo ""
echo "==> Done."
echo "    snd_virmidi loaded and persistent."
echo "    sticker_spinner.jar patched for VirMIDI."
echo "    Restart controller.py — MIDI will connect automatically."
