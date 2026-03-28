#!/usr/bin/env python3
"""Live Performance Rig — keyboard, OLED, 4-ch audio, MIDI"""

import os, re, sys, time, threading, subprocess, signal
from pathlib import Path
from evdev import InputDevice, categorize, ecodes, list_devices
import sounddevice as sd
import soundfile as sf
import numpy as np
import mido
from mido import MidiFile, Message
import board, busio
from adafruit_ssd1306 import SSD1306_I2C
from PIL import Image, ImageDraw, ImageFont

# ── Config ───────────────────────────────────────────────────────────────────
MUSIC_ROOT        = Path("/home/nmlstyl/rig")
PROCESSING_SKETCH = Path("/home/nmlstyl/sketchbook/sticker_spinner/linux-aarch64/sticker_spinner")
VIRTUAL_MIDI_PORT = "RigMIDI"
AUDIO_DEVICE      = 2      # Zoom L6 device index; None = auto-detect by name
KEYBOARD_NAME     = None  # target keyboard name (substring match); None = any arrow-key keyboard
W, H              = 128, 64

KEY_UP,   KEY_DOWN  = ecodes.KEY_UP,    ecodes.KEY_DOWN
KEY_LEFT, KEY_RIGHT = ecodes.KEY_LEFT,  ecodes.KEY_RIGHT
KEY_ESC             = ecodes.KEY_ESC


# ── Track / TrackManager ─────────────────────────────────────────────────────

class Track:
    def __init__(self, path):
        self.path          = Path(path)
        self.title_wav     = self.path / "title.wav"
        self.metronome_wav = self.path / "metronome.wav"
        self.midi_file     = self.path / "midi-for-processing.midi"
        self.song_name     = self.path.parts[-1]
        bpm_file = self.path / "bpm.txt"
        self.bpm = float(bpm_file.read_text().strip()) if bpm_file.exists() else None

    def is_complete(self):
        return self.title_wav.exists() and self.metronome_wav.exists() and self.midi_file.exists()

    def display_title(self):
        sn = ''.join(filter(str.isdigit, self.path.parts[-2]))
        sg = ''.join(filter(str.isdigit, self.song_name))
        return f"Set {sn} - Song {sg}"


class TrackManager:
    def __init__(self, root):
        self.root  = Path(root)
        self.index = 0
        self.tracks = [
            t for set_dir in sorted(self.root.glob("set-*"))
            for t in (Track(p) for p in sorted(set_dir.glob("song-*")))
            if t.is_complete()
        ]
        print(f"Found {len(self.tracks)} tracks")
        for i, t in enumerate(self.tracks, 1):
            print(f"  {i}. {t.display_title()}")

    def current(self):  return self.tracks[self.index] if self.tracks else None
    def next(self):     self.index = (self.index + 1) % len(self.tracks); return self.current()
    def prev(self):     self.index = (self.index - 1) % len(self.tracks); return self.current()
    def position(self): return (self.index + 1, len(self.tracks))


# ── Player ───────────────────────────────────────────────────────────────────

class Player:
    """Synchronized 4-channel audio + MIDI playback."""
    def __init__(self, midi_port, audio_device=None):
        self.audio_device = audio_device
        self.is_playing   = False
        self.is_paused    = False
        self._stop        = threading.Event()
        self._audio_t     = self._midi_t = None
        self._cleanup_stale_midi()
        self.midi_out = mido.open_output(midi_port, virtual=True)
        print(f"Virtual MIDI port: {midi_port}")

    def _cleanup_stale_midi(self):
        try:
            out = subprocess.run(['aconnect', '-i'], capture_output=True, text=True)
            for ln in out.stdout.split('\n'):
                if 'RtMidiOut' in ln and f'pid={os.getpid()}' not in ln:
                    m = re.search(r'pid=(\d+)', ln)
                    if m: subprocess.run(['kill', m.group(1)], capture_output=True)
            time.sleep(0.5)
        except Exception as e:
            print(f"MIDI cleanup: {e}")

    def _find_device(self):
        devs = sd.query_devices()
        if self.audio_device is not None:
            try:
                d = devs[self.audio_device]
                if d['max_output_channels'] >= 4:
                    return self.audio_device
                print(f"WARNING: device {self.audio_device} has only {d['max_output_channels']} output channels")
            except (IndexError, KeyError):
                print(f"WARNING: device {self.audio_device} not found")
            print("Available output devices:")
            for i, d in enumerate(devs):
                if d['max_output_channels'] > 0:
                    print(f"  [{i}] {d['name']}  (out={d['max_output_channels']})")
        for i, d in enumerate(devs):
            n = d['name'].lower()
            if ('zoom' in n or 'l6' in n or 'l-6' in n) and d['max_output_channels'] >= 4:
                print(f"Auto-detected Zoom L6: {d['name']} (device {i})")
                return i
        print("WARNING: Zoom L6 not found, using default")
        return None

    def play(self, track):
        if self.is_playing:
            self.stop()
        try:
            title_data, sr  = sf.read(str(track.title_wav),     dtype='float32')
            metro_data, msr = sf.read(str(track.metronome_wav), dtype='float32')
            midi            = MidiFile(str(track.midi_file))
        except Exception as e:
            print(f"Error loading track: {e}"); return False

        if sr != msr:
            print(f"WARNING: sample rate mismatch ({sr} vs {msr})"); return False

        # Inject BPM tempo if bpm.txt exists and MIDI has no set_tempo
        if track.bpm and not any(m.type == 'set_tempo' for tr in midi.tracks for m in tr):
            midi.tracks[0].insert(0, mido.MetaMessage('set_tempo', tempo=int(mido.bpm2tempo(track.bpm)), time=0))
            print(f"Injected tempo: {track.bpm} BPM")

        # Ensure stereo, pad to equal length
        if title_data.ndim == 1: title_data = np.column_stack([title_data, title_data])
        if metro_data.ndim == 1: metro_data = np.column_stack([metro_data, metro_data])
        n = max(len(title_data), len(metro_data))
        title_data = np.pad(title_data, ((0, n - len(title_data)), (0, 0)))
        metro_data = np.pad(metro_data, ((0, n - len(metro_data)), (0, 0)))

        # 4-channel interleave: [title_L, title_R, metro_L, metro_R]
        out = np.column_stack([title_data[:, 0], title_data[:, 1], metro_data[:, 0], metro_data[:, 1]])
        dev = self._find_device()

        self._stop.clear()
        self.is_playing = self.is_paused = False
        start = threading.Event()

        self._audio_t = threading.Thread(target=self._audio_loop, args=(out, sr, dev, start), daemon=True)
        self._midi_t  = threading.Thread(target=self._midi_loop,  args=(midi, start, 1024/sr), daemon=True)
        self._audio_t.start(); self._midi_t.start()
        time.sleep(0.2); start.set()
        self.is_playing = True
        print(f"Playing: {track.display_title()}  ({n/sr:.1f}s, device {dev})")
        return True

    def _audio_loop(self, data, sr, device, start):
        stream = sd.OutputStream(device=device, channels=4, samplerate=sr, blocksize=1024, dtype='float32')
        start.wait(); stream.start()
        frame, bsize = 0, 1024
        while frame < len(data) and not self._stop.is_set():
            while self.is_paused and not self._stop.is_set(): time.sleep(0.01)
            if self._stop.is_set(): break
            block = data[frame:frame+bsize]
            if len(block) < bsize: block = np.pad(block, ((0, bsize - len(block)), (0, 0)))
            stream.write(block); frame += bsize
        stream.stop(); stream.close()

    def _midi_loop(self, midi, start, delay):
        start.wait(); time.sleep(delay)
        try:
            for msg in midi.play():
                while self.is_paused and not self._stop.is_set(): time.sleep(0.01)
                if self._stop.is_set(): break
                if not msg.is_meta: self.midi_out.send(msg)
        except Exception as e:
            print(f"MIDI error: {e}")
        finally:
            self._all_notes_off()

    def _all_notes_off(self):
        for ch in range(16):
            try: self.midi_out.send(Message('control_change', control=123, value=0, channel=ch))
            except: pass

    def toggle_pause(self):
        if not self.is_playing: return
        self.is_paused = not self.is_paused
        print("Paused" if self.is_paused else "Resumed")

    def stop(self):
        if not self.is_playing: return
        self._stop.set(); self._all_notes_off()
        if self._audio_t: self._audio_t.join(timeout=1.0)
        if self._midi_t:  self._midi_t.join(timeout=1.0)
        self.is_playing = self.is_paused = False

    def cleanup(self):
        self.stop(); self.midi_out.close()


# ── Display ──────────────────────────────────────────────────────────────────

class Display:
    """SSD1306 128×64 OLED via I2C."""
    def __init__(self):
        i2c = busio.I2C(board.SCL, board.SDA)
        self.oled = SSD1306_I2C(W, H, i2c, addr=0x3C)
        self.img  = Image.new("1", (W, H))
        self.draw = ImageDraw.Draw(self.img)
        try:
            base = "/usr/share/fonts/truetype/dejavu/DejaVuSans"
            self.fl = ImageFont.truetype(f"{base}-Bold.ttf", 16)
            self.fm = ImageFont.truetype(f"{base}.ttf", 12)
            self.fs = ImageFont.truetype(f"{base}.ttf", 10)
        except:
            self.fl = self.fm = self.fs = ImageFont.load_default()
        self._clear(); self._text(10, 20, "Performance Rig", self.fm)
        self._text(20, 40, "Initializing...", self.fs); self._show()

    def _clear(self): self.draw.rectangle((0, 0, W, H), fill=0)
    def _text(self, x, y, s, f): self.draw.text((x, y), s, font=f, fill=255)
    def _show(self): self.oled.image(self.img); self.oled.show()

    def update(self, track, pos, total, playing, paused):
        self._clear()
        self._text(2,  2, f"Track {pos}/{total}", self.fs)
        self._text(2, 16, track.display_title(), self.fl)
        self._text(2, 36, track.song_name, self.fm)
        status = "|| PAUSED" if paused else "> PLAYING" if playing else "Press DOWN to play"
        self._text(2, 52, status, self.fs)
        self._show()

    def error(self, msg):
        self._clear(); self._text(2, 20, "ERROR:", self.fm); self._text(2, 35, msg, self.fs); self._show()

    def clear(self):
        self._clear(); self._show()


# ── Keyboard ─────────────────────────────────────────────────────────────────

class Keyboard:
    """evdev arrow-key reader; spawns one thread per device.

    Combo keys (UP+LEFT+RIGHT): individual actions are suppressed while combo
    keys are forming; fired on release if the combo was never completed.
    """
    EXCLUDE    = ('vc4-hdmi', 'cec', 'consumer control')
    COMBO_EXIT = frozenset([KEY_UP, KEY_LEFT, KEY_RIGHT])

    def __init__(self, callback, on_exit=None, name_filter=None):
        self.callback = callback
        self.on_exit  = on_exit
        self.running  = False
        self._held             = set()
        self._combo_triggered  = False
        self._lock             = threading.Lock()
        self.devices           = self._find_devices(name_filter)

    def _find_devices(self, name_filter):
        def _scan(filter_fn=None):
            found = []
            for path in list_devices():
                try:
                    dev  = InputDevice(path)
                    if any(x in dev.name.lower() for x in self.EXCLUDE): continue
                    if filter_fn and not filter_fn(dev.name): continue
                    keys = dev.capabilities(verbose=False).get(ecodes.EV_KEY, [])
                    if all(k in keys for k in (KEY_UP, KEY_DOWN, KEY_LEFT, KEY_RIGHT)):
                        found.append(dev)
                        print(f"Keyboard: {dev.name}")
                except Exception: pass
            return found

        if name_filter:
            devs = _scan(lambda n: name_filter.lower() in n.lower())
            if devs:
                return devs
            print(f"Warning: '{name_filter}' not found — falling back to any arrow-key keyboard")

        devs = _scan()
        if not devs:
            print("Warning: no keyboard with arrow keys found")
        return devs

    def start(self):
        self.running = True
        for d in self.devices:
            threading.Thread(target=self._loop, args=(d,), daemon=True).start()

    def _loop(self, dev):
        try:
            for ev in dev.read_loop():
                if not self.running: break
                if ev.type != ecodes.EV_KEY: continue
                state = categorize(ev).keystate
                code  = ev.code

                if state == 1:   # key down
                    with self._lock:
                        self._held.add(code)
                        held = frozenset(self._held)
                    if code == KEY_ESC and self.on_exit:
                        self.on_exit()
                    elif held >= self.COMBO_EXIT and self.on_exit:
                        with self._lock:
                            self._combo_triggered = True
                        self.on_exit()
                    elif code not in self.COMBO_EXIT:
                        # Non-combo key: dispatch immediately
                        self.callback(code)
                    # else: combo key held but combo not yet complete — wait

                elif state == 0:  # key up
                    with self._lock:
                        was_combo_key     = code in self.COMBO_EXIT and code in self._held
                        was_triggered     = self._combo_triggered
                        self._held.discard(code)
                        # Reset combo flag once all combo keys are released
                        if not (self._held & self.COMBO_EXIT):
                            self._combo_triggered = False
                    # Fire individual action on release if combo never completed
                    if was_combo_key and not was_triggered:
                        self.callback(code)

        except Exception as e:
            print(f"Keyboard error ({dev.name}): {e}")

    def stop(self): self.running = False


# ── Rig ───────────────────────────────────────────────────────────────────────

class Rig:
    def __init__(self):
        print("Starting Performance Rig...")
        self._exit_evt = threading.Event()
        self._stop_panel()
        self._stop_argon()
        self.display  = Display()
        self.tracks   = TrackManager(MUSIC_ROOT)
        self.player   = Player(VIRTUAL_MIDI_PORT, AUDIO_DEVICE)
        self.keyboard = Keyboard(self._on_key, on_exit=self._request_exit, name_filter=KEYBOARD_NAME)
        self._proc    = None

        if not self.tracks.tracks:
            self.display.error("No tracks!"); sys.exit(1)

        self._launch_processing()
        self._refresh_display()
        self.keyboard.start()
        print("Ready!  ← prev  → next  ↓ play  ↑ pause  ESC/↑←→ quit")

    def _launch_processing(self):
        if not PROCESSING_SKETCH.exists():
            print(f"Warning: sketch not found at {PROCESSING_SKETCH}"); return
        os.chmod(PROCESSING_SKETCH, 0o755)
        self._proc = subprocess.Popen(
            [str(PROCESSING_SKETCH)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            preexec_fn=os.setsid,   # new process group so killpg reaches the JVM too
        )
        self._bridge_midi()
        print(f"Processing launched (PID {self._proc.pid})")
        time.sleep(3)

    def _bridge_midi(self):
        """Wire RigMIDI → VirMIDI via aconnect (must run before Processing opens the port)."""
        try:
            ins  = subprocess.run(['aconnect', '-i'], capture_output=True, text=True)
            outs = subprocess.run(['aconnect', '-o'], capture_output=True, text=True)
            our = vir = None
            for ln in ins.stdout.split('\n'):
                if 'RtMidiOut' in ln:
                    m = re.search(r'client (\d+):', ln)
                    if m: our = m.group(1); break
            for ln in outs.stdout.split('\n'):
                if 'Virtual Raw MIDI' in ln or 'VirMIDI' in ln:
                    m = re.search(r'client (\d+):', ln)
                    if m: vir = m.group(1); break
            if not our: print("MIDI bridge: RigMIDI not found"); return
            if not vir: print("MIDI bridge: VirMIDI not found — run patch_midi.sh"); return
            r = subprocess.run(['aconnect', f'{our}:0', f'{vir}:0'], capture_output=True, text=True)
            print(f"MIDI bridged {our}:0 → {vir}:0" if r.returncode == 0 else f"aconnect: {r.stderr.strip()}")
        except FileNotFoundError:
            print("aconnect not found — sudo apt install alsa-utils")

    def _request_exit(self):
        print("\nShutting down (key)")
        self._exit_evt.set()

    def _on_key(self, code):
        if   code == KEY_LEFT:  self.player.stop();          self.tracks.prev(); self._refresh_display()
        elif code == KEY_RIGHT: self.player.stop();          self.tracks.next(); self._refresh_display()
        elif code == KEY_DOWN:
            t = self.tracks.current()
            if t: self.player.play(t); self._refresh_display()
        elif code == KEY_UP:    self.player.toggle_pause();  self._refresh_display()

    def _refresh_display(self):
        t = self.tracks.current()
        if t:
            pos, total = self.tracks.position()
            self.display.update(t, pos, total, self.player.is_playing, self.player.is_paused)

    def _systemctl(self, action, svc):
        cmd = ['systemctl', action, svc]
        if os.geteuid() != 0: cmd = ['sudo', '-n'] + cmd
        return subprocess.run(cmd, capture_output=True)

    def _stop_panel(self):
        """Hide the desktop taskbar while the rig is running."""
        subprocess.run(['pkill', '-f', 'lwrespawn.*wf-panel-pi'], capture_output=True)
        subprocess.run(['pkill', 'wf-panel-pi'], capture_output=True)
        time.sleep(0.5)
        print("Taskbar hidden")

    def _restore_panel(self):
        """Bring the taskbar back under its supervisor so it stays stable."""
        env = {**os.environ, 'DISPLAY': ':0'}
        xauth = '/home/nmlstyl/.Xauthority'
        if os.path.exists(xauth):
            env['XAUTHORITY'] = xauth
        subprocess.Popen(['lwrespawn', 'wf-panel-pi'], env=env,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        print("Taskbar restored")

    def _stop_argon(self):
        self._argon_svc = None
        for svc in ('argononed', 'argone-oled'):
            if self._systemctl('stop', svc).returncode == 0:
                print(f"Stopped {svc}"); self._argon_svc = svc; return
        print("Note: argononed not stopped (service not found or missing sudoers rule)")

    def run(self):
        try:
            while not self._exit_evt.is_set():
                time.sleep(0.1)
                if self._proc and self._proc.poll() is not None:
                    print("Processing exited — shutting down"); break
        except KeyboardInterrupt:
            print("\nShutting down")
        finally:
            self._cleanup()

    def _cleanup(self):
        self.keyboard.stop()
        self.player.cleanup()
        if self._proc and self._proc.poll() is None:
            try:
                os.killpg(os.getpgid(self._proc.pid), signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                self._proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(os.getpgid(self._proc.pid), signal.SIGKILL)
                except ProcessLookupError:
                    pass
        try:
            self.display.clear()
        except Exception as e:
            print(f"Display clear: {e}")
        if self._argon_svc:
            self._systemctl('start', self._argon_svc)
            print(f"Restarted {self._argon_svc}")
        self._restore_panel()


if __name__ == "__main__":
    if os.geteuid() != 0:
        print("Warning: not running as root — keyboard/I2C may fail")
    Rig().run()
