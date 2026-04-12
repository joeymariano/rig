#!/usr/bin/env python3
"""
Live Performance Rig — Raspberry Pi (Argon One V5)
====================================================
Synchronizes four systems for live stage performance:

  - USB Keyboard   evdev arrow keys + ESC/combo detection  (Keyboard)
  - OLED Display   SSD1306 128×64 via I2C                  (Display)
  - 4-ch Audio     sounddevice → Zoom L6:
                     ch 1-2: title.wav  (FOH)
                     ch 3-4: metronome.wav (in-ear click)  (Player)
  - Argon DAC      title audio mirrored to front 3.5mm     (Player)
  - MIDI Playback  mido virtual port → VirMIDI → Processing sketch  (Player)

Thread model:
  main             Rig.run() — monitors Processing exit, drives shutdown
  keyboard(s)      one thread per evdev device — read loop, enqueues key codes
  key_dispatch     Rig._key_dispatch_loop() — drains key queue, calls _on_key
  audio            Player._audio_loop() — streams 4-ch interleaved blocks to Zoom L6
  dac              Player._dac_loop() — streams 2-ch title blocks to Argon DAC
  midi             Player._midi_loop() — sends timed MIDI messages
  render           Rig._display_loop() — ticks ticker, refreshes state, calls render_if_dirty

Design notes:
  - Keyboard callbacks only enqueue a key code.  All slow work (file I/O, thread
    joins) happens in the key-dispatch thread so the evdev read loop never blocks.
  - Display renders *outside* the lock (I2C ≈10 ms) so keyboard events are never
    stalled waiting for the bus.
  - Elapsed-clock image (80 pt font, LANCZOS rescale) is cached per integer second
    and reused across all 20 fps render calls within that second.
"""

import os, re, sys, time, threading, subprocess, signal, queue, select as _select

# Mirror all stdout/stderr to a log file so boot issues can be reviewed after the fact.
# Appends across reboots; each session is separated by a timestamped header.
class _Tee:
    def __init__(self, stream, path):
        self._s   = stream
        self._f   = open(path, 'a', buffering=1)
        self._bol = True   # at beginning of line — prepend timestamp on next write
    def write(self, d):
        if not d:
            return
        # Prepend HH:MM:SS at the start of each line
        if self._bol:
            ts = time.strftime('%H:%M:%S') + ' '
            self._s.write(ts); self._f.write(ts)
        self._s.write(d); self._f.write(d)
        self._bol = d.endswith('\n')
    def flush(self):
        self._s.flush()
        self._f.flush()
    def fileno(self):
        return self._s.fileno()
_log_path = '/home/nmlstyl/rig/boot.log'
sys.stdout = _Tee(sys.stdout, _log_path)
sys.stderr = _Tee(sys.stderr, _log_path)
print(f"\n{'='*60}\nSESSION START {time.strftime('%Y-%m-%d %H:%M:%S')}\n{'='*60}")
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
AUDIO_DEVICE      = None   # always auto-detect Zoom L6 by name on any USB port
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
        # Parse info.txt for title, bpm, platform, timing
        info = {}
        info_file = self.path / "info.txt"
        if info_file.exists():
            for line in info_file.read_text().splitlines():
                if ':' in line:
                    k, v = line.split(':', 1)
                    info[k.strip().lower()] = v.strip()
        self.title    = info.get('title', self.song_name)
        self.platform = info.get('platform', '')
        self.timing   = info.get('timing', '')
        # BPM: prefer info.txt, fall back to bpm.txt
        bpm_str = info.get('bpm')
        if not bpm_str:
            bpm_file = self.path / "bpm.txt"
            bpm_str = bpm_file.read_text().strip() if bpm_file.exists() else None
        try:    self.bpm = float(bpm_str)
        except (TypeError, ValueError): self.bpm = None

    def is_complete(self):
        return self.title_wav.exists() and self.metronome_wav.exists() and self.midi_file.exists()

    def display_title(self):
        sn = ''.join(filter(str.isdigit, self.path.parts[-2]))
        sg = ''.join(filter(str.isdigit, self.song_name))
        return f"Set {sn} - Song {sg}"


class TrackManager:
    def __init__(self, root, selected_set=None):
        self.root  = Path(root)
        self.index = 0
        set_dirs = [Path(selected_set)] if selected_set else sorted(self.root.glob("set-*"))
        self.tracks = [
            t for sdir in set_dirs
            for t in (Track(p) for p in sorted(sdir.glob("song-*")))
            if t.is_complete()
        ]
        print(f"Found {len(self.tracks)} tracks")
        for i, t in enumerate(self.tracks, 1):
            print(f"  {i}. {t.display_title()}")

    def current(self):  return self.tracks[self.index] if self.tracks else None
    def next(self):     self.index = (self.index + 1) % len(self.tracks); return self.current()
    def prev(self):     self.index = (self.index - 1) % len(self.tracks); return self.current()


# ── Player ───────────────────────────────────────────────────────────────────

class Player:
    """Synchronized 4-channel audio + MIDI playback."""
    def __init__(self, midi_port, audio_device=None):
        self.audio_device   = audio_device
        self.is_playing     = False
        self.is_paused      = False
        self._stop          = threading.Event()
        self._audio_t       = self._midi_t = self._dac_t = None
        self._play_start    = None
        self._pause_start   = None
        self._total_paused  = 0.0
        self.track_duration = 0.0
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
            if 'zoom' in n or 'l6' in n or 'l-6' in n:
                print(f"Auto-detected Zoom L6: {d['name']} (device {i})")
                return i
        print("WARNING: Zoom L6 not found, using default output")
        return None

    def _find_dac_device(self):
        """Find the Argon One DAC (front 3.5mm jack) — USB Audio Device (hw:X,0)."""
        devs = sd.query_devices()
        for i, d in enumerate(devs):
            n = d['name'].lower()
            if 'hw:' not in n:
                continue
            if 'zoom' in n or 'l6' in n or 'l-6' in n:
                continue
            if 'hdmi' in n or 'vc4' in n:
                continue
            print(f"Auto-detected DAC: {d['name']} (device {i})")
            return i
        print("WARNING: Argon DAC not found; headphone output disabled")
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

        # Inject BPM tempo if set in info.txt and MIDI has no set_tempo
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
        dev     = self._find_device()
        dac_dev = self._find_dac_device()

        self._stop.clear()
        self.is_playing = self.is_paused = False
        start = threading.Event()

        self._audio_t = threading.Thread(target=self._audio_loop, args=(out, sr, dev, start), daemon=True)
        self._midi_t  = threading.Thread(target=self._midi_loop,  args=(midi, start, 1024/sr), daemon=True)
        self._dac_t   = threading.Thread(target=self._dac_loop,   args=(title_data, sr, dac_dev, start), daemon=True)
        self._audio_t.start(); self._midi_t.start(); self._dac_t.start()
        # Set is_playing immediately so stop() can find the threads even during
        # the 200ms startup delay before start.set() fires.
        self.is_playing    = True
        self._play_start   = time.time()
        self._pause_start  = None
        self._total_paused = 0.0
        self.track_duration = n / sr
        print(f"Playing: {track.display_title()}  ({n/sr:.1f}s, device {dev})")
        time.sleep(0.2); start.set()
        return True

    def _stream_loop(self, data, sr, device, channels, start, label):
        """Write *data* to an output stream one 1024-frame block at a time.
        Honors self._stop and self.is_paused; device=None is allowed (uses
        sounddevice default).  Called by _audio_loop and _dac_loop."""
        stream = None
        try:
            stream = sd.OutputStream(device=device, channels=channels,
                                     samplerate=sr, blocksize=1024, dtype='float32')
            start.wait(); stream.start()
            frame, bsize = 0, 1024
            while frame < len(data) and not self._stop.is_set():
                while self.is_paused and not self._stop.is_set(): time.sleep(0.01)
                if self._stop.is_set(): break
                block = data[frame:frame+bsize]
                if len(block) < bsize:
                    block = np.pad(block, ((0, bsize - len(block)), (0, 0)))
                stream.write(block)
                frame += bsize
        except Exception as e:
            print(f"{label} error: {e}")
        finally:
            if stream is not None:
                stream.stop(); stream.close()

    def _audio_loop(self, data, sr, device, start):
        """4-channel output to Zoom L6: ch1-2 title, ch3-4 metronome."""
        self._stream_loop(data, sr, device, 4, start, "Main audio")

    def _dac_loop(self, data, sr, device, start):
        """Mirror title stereo to the Argon One front DAC (3.5mm jack)."""
        if device is None:
            return   # no DAC detected — skip silently
        self._stream_loop(data, sr, device, 2, start, "DAC output")

    def _midi_loop(self, midi, start, delay):
        start.wait(); time.sleep(delay)
        try:
            tempo = 500000  # default 120 BPM
            ticks_per_beat = midi.ticks_per_beat
            now = time.time()
            for msg in mido.merge_tracks(midi.tracks):
                delta_s = mido.tick2second(msg.time, ticks_per_beat, tempo)
                target = now + delta_s
                while True:
                    if self._stop.is_set(): return
                    remaining = target - time.time()
                    if remaining <= 0:
                        break
                    if self.is_paused:
                        pause_at = time.time()
                        while self.is_paused and not self._stop.is_set():
                            time.sleep(0.01)
                        if self._stop.is_set(): return
                        target += time.time() - pause_at  # shift deadline by pause duration
                    else:
                        time.sleep(min(0.005, remaining))
                now = time.time()
                if msg.type == 'set_tempo':
                    tempo = msg.tempo
                if not msg.is_meta:
                    self.midi_out.send(msg)
        except Exception as e:
            print(f"MIDI error: {e}")
        finally:
            self._all_notes_off()

    def _all_notes_off(self):
        for ch in range(16):
            try: self.midi_out.send(Message('control_change', control=123, value=0, channel=ch))
            except: pass

    def playback_info(self):
        """Returns (elapsed_s, remaining_s) based on wall time minus pauses."""
        if not self.is_playing or self._play_start is None:
            return 0.0, 0.0
        paused = self._total_paused
        if self.is_paused and self._pause_start:
            paused += time.time() - self._pause_start
        elapsed   = max(0.0, min(time.time() - self._play_start - paused, self.track_duration))
        remaining = max(0.0, self.track_duration - elapsed)
        return elapsed, remaining

    def toggle_pause(self):
        if not self.is_playing: return
        self.is_paused = not self.is_paused
        if self.is_paused:
            self._pause_start = time.time()
        elif self._pause_start:
            self._total_paused += time.time() - self._pause_start
            self._pause_start = None
        print("Paused" if self.is_paused else "Resumed")

    def stop(self):
        if not self.is_playing: return
        self._stop.set(); self._all_notes_off()
        if self._audio_t: self._audio_t.join(timeout=1.0)
        if self._midi_t:  self._midi_t.join(timeout=1.0)
        if self._dac_t:   self._dac_t.join(timeout=1.0)
        self.is_playing = self.is_paused = False
        self._play_start = self._pause_start = None
        self._total_paused = 0.0

    def cleanup(self):
        self.stop(); self.midi_out.close()


# ── Display ──────────────────────────────────────────────────────────────────

class Display:
    """SSD1306 128×64 OLED via I2C with scrolling tickers."""
    TICKER_SPEED = 1    # px per tick
    TICKER_DIR   = 1    # +1 = LTR scroll
    TICKER_GAP   = 14   # px gap between wrap-around repeats
    TICKER_Y     = 1    # y position for the ticker row

    def __init__(self):
        i2c = busio.I2C(board.SCL, board.SDA)
        self.oled = SSD1306_I2C(W, H, i2c, addr=0x3C)
        self.img  = Image.new("1", (W, H))
        self.draw = ImageDraw.Draw(self.img)
        try:
            sans  = "/usr/share/fonts/truetype/liberation/LiberationSans"
            narrow = "/usr/share/fonts/truetype/liberation/LiberationSansNarrow"
            self.fb = ImageFont.truetype(f"{sans}-Bold.ttf", 18)
            self.fm = ImageFont.truetype(f"{sans}-Regular.ttf", 12)
            self.fs = ImageFont.truetype(f"{sans}-Regular.ttf", 10)
            self.fp = ImageFont.truetype(f"{sans}-Bold.ttf", 20)    # track number prefix (2× ticker)
            self.fe = ImageFont.truetype(f"{narrow}-Bold.ttf", 80)  # elapsed stretch-render
            self.fl = ImageFont.truetype(f"{sans}-Bold.ttf", 30)    # set selector large label
        except:
            self.fb = self.fm = self.fs = self.fe = self.fp = self.fl = ImageFont.load_default()
        self._lock           = threading.Lock()
        self._state          = None
        self._ticker_text    = ''
        self._ticker_prefix  = ''   # static track-number prefix
        self._ticker_offset  = 0.0
        self._dirty          = False
        # Cache the rendered elapsed-clock image keyed by integer second.
        # PIL+LANCZOS is ~5 ms; re-running it 20× per second wastes CPU since
        # the value only changes once per second.  Tuple: (se_int, img, x, y).
        self._elapsed_cache  = None
        self._clear()
        self._text(10, 20, "Performance Rig", self.fm)
        self._text(20, 40, "Initializing...", self.fs)
        self._show()

    def _clear(self): self.draw.rectangle((0, 0, W, H), fill=0)
    def _text(self, x, y, s, f): self.draw.text((x, y), s, font=f, fill=255)
    def _show(self): self.oled.image(self.img); self.oled.show()

    def _tw(self, s, f):
        bb = self.draw.textbbox((0, 0), s, font=f)
        return bb[2] - bb[0]

    def update(self, track, playing, paused, remaining_s=0.0, set_elapsed_s=0.0):
        with self._lock:
            sg = int(''.join(filter(str.isdigit, track.song_name)) or 0)
            self._ticker_prefix = f"{sg:02d}"
            bpm_s = f"{int(track.bpm)}bpm" if track.bpm else ""
            ticker_txt = ' '.join(filter(None, [track.title, bpm_s, track.platform]))
            if ticker_txt != self._ticker_text:
                self._ticker_text   = ticker_txt
                self._ticker_offset = 0.0
            self._state = dict(track=track, playing=playing, paused=paused,
                               remaining_s=remaining_s, set_elapsed_s=set_elapsed_s)
            self._dirty = True   # render thread will pick this up

    def tick(self):
        with self._lock:
            if self._state is None:
                return
            if self._ticker_text:
                self._ticker_offset += self.TICKER_SPEED * self.TICKER_DIR
            self._dirty = True

    def render_if_dirty(self):
        """Snapshot state under lock, then render + I2C *outside* the lock.
        Only called from the single render thread — never from the keyboard thread."""
        with self._lock:
            if not self._dirty or self._state is None:
                return
            self._dirty  = False
            state  = self._state.copy()
            txt    = self._ticker_text
            offset = self._ticker_offset
            prefix = self._ticker_prefix
        # I2C happens here, outside the lock so keyboard callbacks never block on it
        self._do_render(state, txt, offset, prefix)

    def _draw_ticker(self, txt, tx, prefix=''):
        """Render a full-width scrolling ticker.

        prefix: drawn statically at x=2 in the large font; scrolling text fills the rest.
        Copies are placed via modulo so there is never a blank gap.
        """
        tmp = Image.new("1", (W, 28 if prefix else 24), 0)
        tdr = ImageDraw.Draw(tmp)

        scroll_x = 2
        if prefix:
            tdr.text((2, self.TICKER_Y), prefix, font=self.fp, fill=255)
            scroll_x = 2 + self._tw(prefix, self.fp) + 3

        scroll_w = W - scroll_x
        if scroll_w > 0 and txt:
            tw     = self._tw(txt, self.fs)
            period = max(1, tw + self.TICKER_GAP)
            base   = int(tx) % period
            if base > 0:
                base -= period
            n_copies = scroll_w // period + 2
            stmp = Image.new("1", (scroll_w, 24), 0)
            stdr = ImageDraw.Draw(stmp)
            for n in range(n_copies):
                stdr.text((base + n * period, self.TICKER_Y), txt, font=self.fs, fill=255)
            tmp.paste(stmp, (scroll_x, 0))

        self.img.paste(tmp, (0, 0))

    def _do_render(self, state, ticker_text, ticker_offset, ticker_prefix):
        """Redraw full screen and push to OLED. Called from render thread only (no lock needed)."""
        self._clear()

        self._draw_ticker(ticker_text, ticker_offset, prefix=ticker_prefix)

        # Countdown
        cnt = None
        if state['playing']:
            cnt = "PAUSED" if state['paused'] else f"{int(state['remaining_s'])//60}:{int(state['remaining_s'])%60:02d} left"
        if cnt:
            self._text((W - self._tw(cnt, self.fs)) // 2, 15, cnt, self.fs)

        # Elapsed clock — 80pt narrow bold, letter-spaced, scaled to fill the
        # lower half of the OLED.  Rendering costs ~5 ms (PIL + LANCZOS), so
        # cache the result by integer second and reuse it across all 20 fps frames.
        se      = int(state['set_elapsed_s'])
        el_y0   = 28
        el_h    = H - el_y0 - 2

        if self._elapsed_cache is None or self._elapsed_cache[0] != se:
            elapsed_str = f"{se // 60:02d}:{se % 60:02d}"
            # Render characters individually so we can control inter-character spacing.
            tmp = Image.new("L", (512, 200), 0)
            tdr = ImageDraw.Draw(tmp)
            char_bbs = [tdr.textbbox((0, 0), c, font=self.fe) for c in elapsed_str]
            char_ws  = [bb[2] - bb[0] for bb in char_bbs]
            top      = min(bb[1] for bb in char_bbs)
            char_h   = max(bb[3] for bb in char_bbs) - top
            spacing  = max(1, int(sum(char_ws) / len(char_ws) * 0.25))
            total_w  = sum(char_ws) + spacing * (len(elapsed_str) - 1)
            x = 0
            for c, bb, cw in zip(elapsed_str, char_bbs, char_ws):
                tdr.text((x - bb[0], -top), c, font=self.fe, fill=255)
                x += cw + spacing
            text_img     = tmp.crop((0, 0, max(1, total_w), max(1, char_h)))
            tw, th       = text_img.size
            scale        = min(W / tw, el_h / th)
            new_w, new_h = int(tw * scale), int(th * scale)
            scaled       = text_img.resize((new_w, new_h), Image.LANCZOS).point(
                               lambda p: 255 if p > 64 else 0, '1')
            x_off = max(0, (W - new_w) // 2)
            y_off = max(el_y0, el_y0 + (el_h - new_h) // 2)
            self._elapsed_cache = (se, scaled, x_off, y_off)

        _, scaled, x_off, y_off = self._elapsed_cache
        self.img.paste(scaled, (x_off, y_off))

        self._show()

    def _draw_set_name(self, name, y_offset=0):
        """Draw SET XX centered with a vertical offset (for animation). No lock — caller holds it."""
        tw = self._tw(name, self.fl)
        x  = (W - tw) // 2
        y  = (H // 2 - 18) + y_offset   # 18 ≈ half of 30pt cap height
        self._text(x, y, name, self.fl)

    def show_set_name(self, name, hint=''):
        """Render set name centered and push to OLED. hint= small text at bottom."""
        with self._lock:
            self._clear()
            self._draw_set_name(name)
            if hint:
                hw = self._tw(hint, self.fs)
                self._text((W - hw) // 2, H - 11, hint, self.fs)
            self._show()

    def show_hint(self, line1, line2=''):
        """Two-line centered message (e.g. keyboard replug prompt)."""
        with self._lock:
            self._clear()
            y1 = 16 if line2 else (H // 2 - 6)
            x1 = (W - self._tw(line1, self.fm)) // 2
            self._text(x1, y1, line1, self.fm)
            if line2:
                x2 = (W - self._tw(line2, self.fm)) // 2
                self._text(x2, y1 + 22, line2, self.fm)
            self._show()

    def animate_set_transition(self, old_name, new_name, direction_up):
        """200ms slide: direction_up=True → new slides in from top, old exits bottom."""
        frames = 8   # 8 × 25 ms ≈ 200 ms
        travel = H + 36  # slightly more than screen height so text fully exits
        for i in range(frames + 1):
            t = i / frames
            if direction_up:
                old_y = int(t * travel)         # slides down off screen
                new_y = int((t - 1) * travel)   # slides in from top
            else:
                old_y = int(-t * travel)         # slides up off screen
                new_y = int((1 - t) * travel)    # slides in from bottom
            with self._lock:
                self._clear()
                self._draw_set_name(old_name, old_y)
                self._draw_set_name(new_name, new_y)
                self._show()
            time.sleep(0.025)

    def error(self, msg):
        self._clear(); self._text(2, 20, "ERROR:", self.fm); self._text(2, 35, msg, self.fs); self._show()

    def clear(self):
        self._clear(); self._show()


# ── Keyboard ─────────────────────────────────────────────────────────────────

class Keyboard:
    """evdev arrow-key reader; spawns one thread per device.

    Grabs each device exclusively so the display server cannot consume events.
    Reconnects automatically if the keyboard is unplugged and replugged.

    Combo keys (UP+LEFT+RIGHT): individual actions are suppressed while combo
    keys are forming; fired on release if the combo was never completed.
    """
    EXCLUDE         = ('vc4-hdmi', 'cec', 'consumer control', 'zoom', 'l6')
    COMBO_EXIT      = frozenset([KEY_UP, KEY_LEFT, KEY_RIGHT])
    RECONNECT_DELAY = 0.5   # seconds between reconnect attempts

    def __init__(self, callback, on_exit=None, name_filter=None):
        self.callback         = callback
        self.on_exit          = on_exit
        self.running          = False
        self.name_filter      = name_filter
        self._held            = set()
        self._combo_triggered = False
        self._lock            = threading.Lock()
        self._stop_evt        = threading.Event()
        self.devices          = self._find_devices(name_filter)

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
                except Exception: pass
            # For multi-interface HID devices (same USB path, multiple /inputN),
            # keep only the lowest-numbered interface — input0 is the standard
            # keyboard interface (has EV_LED/EV_REP) and is the one that fires
            # actual key events; higher interfaces are system-control/consumer pages.
            usb_base = lambda d: re.sub(r'/input\d+$', '', d.phys or d.path)
            by_base = {}
            for dev in found:
                base = usb_base(dev)
                iface = int(m.group(1)) if (m := re.search(r'/input(\d+)$', dev.phys or '')) else 0
                if base not in by_base or iface < by_base[base][0]:
                    by_base[base] = (iface, dev)
            deduped = [v for _, v in by_base.values()]
            for dev in deduped:
                print(f"Keyboard: {dev.name} ({dev.path})")
            return deduped

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
        if self.devices:
            for d in self.devices:
                threading.Thread(target=self._loop, args=(d,), daemon=True).start()
        else:
            # No device found at startup — spawn a reconnect watcher
            threading.Thread(target=self._reconnect_loop, daemon=True).start()

    def _read_dev(self, dev):
        """Grab and read one device until disconnect or stop. Returns True if should reconnect."""
        # Retry grab for up to 2s — compositor may briefly hold device after
        # a previous KB releases it (e.g. the set-selection keyboard handing off).
        grabbed = False
        for _ in range(20):
            try:
                dev.grab()
                print(f"Keyboard grabbed: {dev.name} ({dev.path})")
                grabbed = True
                break
            except OSError:
                time.sleep(0.1)
        if not grabbed:
            print(f"Keyboard grab failed ({dev.name}) after retries — will retry")
            return True

        # Clear any keys that were "held" before disconnect — they'll never get a
        # key-up event and would cause phantom callbacks on the next session.
        with self._lock:
            if self._held:
                print(f"Keyboard: clearing {len(self._held)} stuck key(s): {self._held}")
            self._held.clear()
            self._combo_triggered = False

        try:
            while self.running:
                ready, _, _ = _select.select([dev.fd], [], [], 1.0)
                if not self.running:
                    return False
                if not ready:
                    continue

                for ev in dev.read():
                    if not self.running:
                        return False
                    if ev.type != ecodes.EV_KEY:
                        continue
                    state = categorize(ev).keystate
                    code  = ev.code
                    if state not in (0, 1):   # ignore key-repeat (state==2)
                        continue

                    if state == 1:   # key down
                        with self._lock:
                            self._held.add(code)
                            held = frozenset(self._held)
                        print(f"Keyboard: DOWN code={code} held={set(held)}")
                        if code == KEY_ESC and self.on_exit:
                            self.on_exit()
                        elif held >= self.COMBO_EXIT and self.on_exit:
                            with self._lock:
                                self._combo_triggered = True
                            self.on_exit()
                        elif code not in self.COMBO_EXIT:
                            self.callback(code)

                    elif state == 0:  # key up
                        with self._lock:
                            was_combo_key = code in self.COMBO_EXIT and code in self._held
                            was_triggered = self._combo_triggered
                            self._held.discard(code)
                            if not (self._held & self.COMBO_EXIT):
                                self._combo_triggered = False
                        print(f"Keyboard: UP   code={code} combo_key={was_combo_key} triggered={was_triggered}")
                        if was_combo_key and not was_triggered:
                            self.callback(code)

        except OSError as e:
            print(f"Keyboard disconnected ({dev.name}): {e}")
            return True   # reconnect
        except Exception as e:
            print(f"Keyboard error ({dev.name}): {e}")
            return False
        finally:
            try: dev.ungrab()
            except: pass

        return False

    def _loop(self, dev):
        while self.running:
            if not self._read_dev(dev):
                break
            if not self.running:
                break
            print(f"Keyboard: waiting to reconnect...")
            dev = self._wait_for_device()
            if dev is None:
                break   # stop() was called

    def _reconnect_loop(self):
        """Watcher for when no keyboard is present at startup."""
        print("Keyboard: waiting for device to appear...")
        dev = self._wait_for_device()
        if dev:
            self._loop(dev)  # startup=True was a leftover bug — _loop takes only dev

    def _wait_for_device(self):
        """Block until a matching keyboard appears or stop() is called. Returns device or None."""
        while self.running:
            self._stop_evt.wait(self.RECONNECT_DELAY)
            if not self.running:
                return None
            devs = self._find_devices(self.name_filter)
            if devs:
                return devs[0]
        return None

    def stop(self):
        self.running = False
        self._stop_evt.set()


# ── Rig ───────────────────────────────────────────────────────────────────────

class Rig:
    def __init__(self):
        print("Starting Performance Rig...")
        self._exit_evt       = threading.Event()
        self._set_start_time = None
        self._proc           = None
        self._stop_panel()
        self._stop_argon()
        self.display = Display()
        self.player   = Player(VIRTUAL_MIDI_PORT, AUDIO_DEVICE)
        self._launch_processing()
        selected_set  = self._select_set()
        self.tracks   = TrackManager(MUSIC_ROOT, selected_set)
        self._key_q   = queue.Queue()
        self.keyboard = Keyboard(lambda code: self._key_q.put_nowait(code),
                                 on_exit=self._request_exit,
                                 name_filter=KEYBOARD_NAME)

        if not self.tracks.tracks:
            self.display.error("No tracks!"); sys.exit(1)
        self._refresh_display()
        self.keyboard.start()
        threading.Thread(target=self._display_loop, daemon=True).start()
        threading.Thread(target=self._key_dispatch_loop, daemon=True).start()
        print("Ready!  ← prev  → next  ↓ play  ↑ pause  ESC/↑←→ quit")

    def _select_set(self):
        """Block on OLED set-picker until user confirms with ↓/↑. Returns chosen set Path."""
        sets = sorted(MUSIC_ROOT.glob("set-*"))
        if len(sets) <= 1:
            return sets[0] if sets else None

        idx   = 0
        label = lambda i: f"SET {i+1:02d}"
        self.display.show_set_name(label(idx))

        key_q = queue.Queue()
        kb = Keyboard(lambda code: key_q.put(code), name_filter=KEYBOARD_NAME)
        kb.start()

        selected = None

        while selected is None:
            try:
                code = key_q.get(timeout=0.5)
            except queue.Empty:
                continue

            if code in (KEY_LEFT, KEY_RIGHT):
                selected = sets[idx]
            elif code == KEY_UP:
                new_idx = (idx - 1) % len(sets)
                self.display.animate_set_transition(label(idx), label(new_idx), direction_up=True)
                idx = new_idx
            elif code == KEY_DOWN:
                new_idx = (idx + 1) % len(sets)
                self.display.animate_set_transition(label(idx), label(new_idx), direction_up=False)
                idx = new_idx

        kb.stop()
        time.sleep(0.1)   # let keyboard thread release device grab before performance KB starts
        print(f"Selected: {selected.name}")
        return selected

    def _launch_processing(self):
        if not PROCESSING_SKETCH.exists():
            print(f"Warning: sketch not found at {PROCESSING_SKETCH}"); return
        os.chmod(PROCESSING_SKETCH, 0o755)
        self._proc = subprocess.Popen(
            [str(PROCESSING_SKETCH)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            preexec_fn=os.setsid,   # new process group so killpg reaches the JVM too
            env={**os.environ, 'DISPLAY': ':0', 'XAUTHORITY': '/home/nmlstyl/.Xauthority'},
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
        print("\nShutdown (key)")
        self._exit_evt.set()

    def _key_dispatch_loop(self):
        """Drain the key queue in a dedicated thread so the keyboard read loop is never
        blocked by slow operations (file reads, thread joins) in _on_key."""
        while not self._exit_evt.is_set():
            try:
                code = self._key_q.get(timeout=0.1)
            except queue.Empty:
                continue
            self._on_key(code)

    def _on_key(self, code):
        if   code == KEY_LEFT:  self.player.stop();          self.tracks.prev(); self._refresh_display()
        elif code == KEY_RIGHT: self.player.stop();          self.tracks.next(); self._refresh_display()
        elif code == KEY_DOWN:
            t = self.tracks.current()
            if t and self.player.play(t):
                if self._set_start_time is None:
                    self._set_start_time = time.time()
                self._refresh_display()
        elif code == KEY_UP:    self.player.toggle_pause();  self._refresh_display()

    def _display_loop(self):
        """Single render thread at ~20fps: advances ticker, refreshes state every second,
        then renders to OLED. Keyboard callbacks only set a dirty flag so they never
        block on I2C."""
        _last_state = 0.0
        while not self._exit_evt.is_set():
            time.sleep(0.05)
            now = time.time()
            self.display.tick()
            if now - _last_state >= 0.25 and (self.player.is_playing or self._set_start_time):
                self._refresh_display()
                _last_state = now
            self.display.render_if_dirty()

    def _refresh_display(self):
        t = self.tracks.current()
        if t:
            _, remaining = self.player.playback_info()
            set_elapsed = (time.time() - self._set_start_time) if self._set_start_time else 0.0
            self.display.update(t, self.player.is_playing, self.player.is_paused,
                                remaining, set_elapsed)

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
        # Kill any ghost lwrespawn left over from a previous (failed) restore attempt
        subprocess.run(['pkill', '-f', 'lwrespawn.*wf-panel-pi'], capture_output=True)
        time.sleep(0.2)
        uid_r = subprocess.run(['id', '-u', 'nmlstyl'], capture_output=True, text=True)
        uid   = uid_r.stdout.strip() or '1000'
        xdg   = f'/run/user/{uid}'
        # Detect the active Wayland socket from XDG_RUNTIME_DIR
        wayland = 'wayland-0'
        try:
            socks = [p for p in Path(xdg).iterdir()
                     if p.name.startswith('wayland-') and p.suffix != '.lock']
            if socks:
                wayland = socks[0].name
        except Exception:
            pass
        subprocess.Popen(
            ['sudo', '-u', 'nmlstyl', 'env',
             f'XDG_RUNTIME_DIR={xdg}',
             f'WAYLAND_DISPLAY={wayland}',
             'DISPLAY=:0',
             'XAUTHORITY=/home/nmlstyl/.Xauthority',
             'lwrespawn', 'wf-panel-pi'],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        print(f"Taskbar restored (WAYLAND_DISPLAY={wayland})")

    def _stop_argon(self):
        self._argon_svcs = []
        for svc in ('argononed', 'argone-oled', 'argonone-led'):
            r = self._systemctl('stop', svc)
            if r.returncode == 0:
                print(f"Stopped {svc}")
                self._argon_svcs.append(svc)
        if not self._argon_svcs:
            print("Note: no argon services found to stop")

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
        for svc in self._argon_svcs:
            r = self._systemctl('start', svc)
            if r.returncode == 0:
                print(f"Restarted {svc}")
            else:
                print(f"Failed to restart {svc}: {r.stderr.decode().strip()}")
        self._restore_panel()


if __name__ == "__main__":
    if os.geteuid() != 0:
        print("Warning: not running as root — keyboard/I2C may fail")
    Rig().run()
