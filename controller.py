#!/usr/bin/env python3
"""
Live Performance Rig Controller
Handles keyboard input, OLED display, dual audio playback, and MIDI routing
"""

import os
import sys
import time
import threading
import subprocess
import signal
from pathlib import Path
from evdev import InputDevice, categorize, ecodes, list_devices

# Audio
import pygame
import numpy as np

# MIDI
import mido
from mido import MidiFile, Message

# OLED Display
import board
import busio
from adafruit_ssd1306 import SSD1306_I2C
from PIL import Image, ImageDraw, ImageFont

# ============================================================================
# CONFIGURATION
# ============================================================================

MUSIC_ROOT = Path.home() / "rig"  # ~/rig/set-01/song-01/...
PROCESSING_SKETCH = Path.home() / "sketchbook/sticker_spinner/linux-aarch64/sticker_spinner"
VIRTUAL_MIDI_PORT = "RigMIDI"  # Our internal port name
MIDI_PORT_FOR_PROCESSING = "Real Time Sequencer"  # What Processing expects

# Audio device configuration
# Set to None to use system default
# Set to device name or number for Zoom L6
# Examples: 
#   AUDIO_DEVICE = None  # Use default
#   AUDIO_DEVICE = "L6: USB Audio"  # By name
#   AUDIO_DEVICE = 3  # By device index
AUDIO_DEVICE = 3  # Zoom L6 (device 3 from sounddevice list)

# Button mapping (arrow keys)
KEY_UP = ecodes.KEY_UP
KEY_DOWN = ecodes.KEY_DOWN
KEY_LEFT = ecodes.KEY_LEFT
KEY_RIGHT = ecodes.KEY_RIGHT

# Display settings
DISPLAY_WIDTH = 128
DISPLAY_HEIGHT = 64

# ============================================================================
# TRACK MANAGER
# ============================================================================

class Track:
    """Represents a single song/track"""
    def __init__(self, path):
        self.path = Path(path)
        self.title_wav = self.path / "title.wav"
        self.metronome_wav = self.path / "metronome.wav"
        self.midi_file = self.path / "midi-for-processing.midi"
        
        # Extract display info from path
        # ~/rig/set-01/song-01 -> Set: 01, Song: 01
        parts = self.path.parts
        self.set_name = parts[-2] if len(parts) >= 2 else "unknown"
        self.song_name = parts[-1] if len(parts) >= 1 else "unknown"
        
    def exists(self):
        """Check if all required files exist"""
        return (self.title_wav.exists() and 
                self.metronome_wav.exists() and 
                self.midi_file.exists())
    
    def get_display_title(self):
        """Format title for OLED display"""
        # Extract numbers from set-01 and song-01
        set_num = ''.join(filter(str.isdigit, self.set_name))
        song_num = ''.join(filter(str.isdigit, self.song_name))
        return f"Set {set_num} - Song {song_num}"
    
    def get_song_number(self):
        """Get just the song number"""
        song_num = ''.join(filter(str.isdigit, self.song_name))
        return song_num if song_num else "?"


class TrackManager:
    """Manages the playlist and current track position"""
    def __init__(self, music_root):
        self.music_root = Path(music_root)
        self.tracks = []
        self.current_index = 0
        self.scan_tracks()
        
    def scan_tracks(self):
        """Scan directory structure for tracks"""
        self.tracks = []
        
        # Find all directories matching set-*/song-* pattern
        set_dirs = sorted(self.music_root.glob("set-*"))
        
        for set_dir in set_dirs:
            song_dirs = sorted(set_dir.glob("song-*"))
            for song_dir in song_dirs:
                track = Track(song_dir)
                if track.exists():
                    self.tracks.append(track)
                else:
                    print(f"Warning: Incomplete track at {song_dir}")
        
        print(f"Found {len(self.tracks)} tracks")
        for i, track in enumerate(self.tracks, 1):
            print(f"  {i}. {track.get_display_title()}")
    
    def get_current_track(self):
        """Get current track"""
        if not self.tracks:
            return None
        return self.tracks[self.current_index]
    
    def next_track(self):
        """Advance to next track"""
        if self.tracks:
            self.current_index = (self.current_index + 1) % len(self.tracks)
            return self.get_current_track()
        return None
    
    def previous_track(self):
        """Go to previous track"""
        if self.tracks:
            self.current_index = (self.current_index - 1) % len(self.tracks)
            return self.get_current_track()
        return None
    
    def get_track_count(self):
        """Get total number of tracks"""
        return len(self.tracks)
    
    def get_track_position(self):
        """Get current position (1-indexed)"""
        return self.current_index + 1 if self.tracks else 0


# ============================================================================
# AUDIO/MIDI PLAYER
# ============================================================================

class AudioMidiPlayer:
    """Handles synchronized playback of 2 audio files + MIDI with multi-channel routing"""
    def __init__(self, midi_port_name, audio_device=None):
        self.midi_port_name = midi_port_name
        self.audio_device = audio_device
        self.midi_output = None
        self.is_playing = False
        self.is_paused = False
        self.midi_thread = None
        self.audio_thread = None
        self.stop_flag = threading.Event()
        self.pause_flag = threading.Event()
        
        # Audio playback using sounddevice for multi-channel
        try:
            import sounddevice as sd
            self.use_sounddevice = True
            self.sd = sd
            print("Using sounddevice for multi-channel audio")
        except ImportError:
            print("WARNING: sounddevice not installed. Falling back to pygame (no channel separation)")
            print("Install with: pip install sounddevice --break-system-packages")
            self.use_sounddevice = False
            self.init_pygame_audio()
        
        # Open MIDI output port
        self.open_midi_port()
    
    def init_pygame_audio(self):
        """Fallback: Initialize pygame mixer (mixed stereo output)"""
        os.environ['SDL_AUDIODRIVER'] = 'pipewire,pulseaudio,alsa'
        
        if self.audio_device:
            print(f"Initializing pygame audio with device: {self.audio_device}")
            os.environ['SDL_AUDIO_DEVICE_NAME'] = str(self.audio_device)
        
        pygame.mixer.init(frequency=48000, size=-16, channels=2, buffer=512)
        self.channel_title = pygame.mixer.Channel(0)
        self.channel_metronome = pygame.mixer.Channel(1)
        print(f"Pygame audio initialized: {pygame.mixer.get_init()}")
    
    def open_midi_port(self):
        """Open MIDI output port that Processing can receive from"""
        try:
            available_ports = mido.get_output_names()
            print(f"Available MIDI output ports: {available_ports}")

            # First priority: open the port Processing is already listening on
            target_port = None
            for port in available_ports:
                if MIDI_PORT_FOR_PROCESSING in port:
                    target_port = port
                    break

            if target_port:
                self.midi_output = mido.open_output(target_port)
                print(f"Opened Processing MIDI port: {target_port}")
            elif self.midi_port_name in available_ports:
                self.midi_output = mido.open_output(self.midi_port_name)
                print(f"Opened existing MIDI port: {self.midi_port_name}")
            else:
                # Create virtual port — will need aconnect to wire to Processing
                self.midi_output = mido.open_output(self.midi_port_name, virtual=True)
                print(f"Created virtual MIDI port: {self.midi_port_name}")
        except Exception as e:
            print(f"Error opening MIDI port: {e}")
            self.midi_output = None
    
    def play(self, track):
        """Start playing a track (2 WAVs + MIDI synchronized)"""
        if self.is_playing:
            self.stop()
        
        print(f"Loading track: {track.get_display_title()}")
        
        if self.use_sounddevice:
            return self._play_sounddevice(track)
        else:
            return self._play_pygame(track)
    
    def _play_sounddevice(self, track):
        """Play using sounddevice (multi-channel routing)"""
        try:
            import soundfile as sf
            
            # Load audio files
            title_data, title_sr = sf.read(str(track.title_wav), dtype='float32')
            metronome_data, metronome_sr = sf.read(str(track.metronome_wav), dtype='float32')
            midi_file = MidiFile(str(track.midi_file))
            
            # Verify sample rates match
            if title_sr != metronome_sr:
                print(f"WARNING: Sample rate mismatch! title={title_sr}Hz, metronome={metronome_sr}Hz")
                return False
            
            # Ensure both are stereo
            if title_data.ndim == 1:
                title_data = np.column_stack([title_data, title_data])
            if metronome_data.ndim == 1:
                metronome_data = np.column_stack([metronome_data, metronome_data])
            
            # Pad shorter file to match length
            max_len = max(len(title_data), len(metronome_data))
            if len(title_data) < max_len:
                title_data = np.pad(title_data, ((0, max_len - len(title_data)), (0, 0)))
            if len(metronome_data) < max_len:
                metronome_data = np.pad(metronome_data, ((0, max_len - len(metronome_data)), (0, 0)))
            
            # Create 4-channel output: [title_L, title_R, metronome_L, metronome_R]
            output_data = np.column_stack([
                title_data[:, 0],      # Channel 1 (Output 1)
                title_data[:, 1],      # Channel 2 (Output 2)
                metronome_data[:, 0],  # Channel 3 (Output 3)
                metronome_data[:, 1]   # Channel 4 (Output 4)
            ])
            
            # Find Zoom L6 device
            device_id = self._find_audio_device()
            
            print(f"Starting 4-channel playback on device {device_id}")
            print(f"  Channels 1-2: title.wav")
            print(f"  Channels 3-4: metronome.wav")
            print(f"  Sample rate: {title_sr}Hz")
            print(f"  Duration: {max_len / title_sr:.2f}s")
            
            # Reset flags
            self.stop_flag.clear()
            self.pause_flag.clear()
            self.is_playing = True
            self.is_paused = False
            
            # Start audio playback thread
            self.audio_thread = threading.Thread(
                target=self._play_audio_thread,
                args=(output_data, title_sr, device_id),
                daemon=True
            )
            self.audio_thread.start()
            
            # Start MIDI playback thread
            self.midi_thread = threading.Thread(
                target=self._play_midi_thread,
                args=(midi_file,),
                daemon=True
            )
            self.midi_thread.start()
            
            print("Playback started")
            return True
            
        except ImportError:
            print("ERROR: soundfile not installed")
            print("Install with: pip install soundfile --break-system-packages")
            return False
        except Exception as e:
            print(f"Error loading track files: {e}")
            import traceback
            traceback.print_exc()
            return False
    
    def _play_audio_thread(self, data, samplerate, device):
        """Audio playback thread for sounddevice"""
        try:
            # Create an output stream
            stream = self.sd.OutputStream(
                device=device,
                channels=4,  # 4-channel output
                samplerate=samplerate,
                blocksize=1024,
                dtype='float32'
            )
            
            stream.start()
            
            # Playback loop
            frame = 0
            frames_per_block = 1024
            
            while frame < len(data) and not self.stop_flag.is_set():
                # Handle pause
                while self.is_paused and not self.stop_flag.is_set():
                    time.sleep(0.01)
                
                if self.stop_flag.is_set():
                    break
                
                # Get next block
                end_frame = min(frame + frames_per_block, len(data))
                block = data[frame:end_frame]
                
                # Pad if needed
                if len(block) < frames_per_block:
                    block = np.pad(block, ((0, frames_per_block - len(block)), (0, 0)))
                
                # Write to stream
                stream.write(block)
                frame = end_frame
            
            stream.stop()
            stream.close()
            
        except Exception as e:
            print(f"Error in audio playback: {e}")
            import traceback
            traceback.print_exc()
    
    def _find_audio_device(self):
        """Find Zoom L6 audio device"""
        if self.audio_device is not None:
            return self.audio_device
        
        # Try to find Zoom L6 automatically
        devices = self.sd.query_devices()
        for i, device in enumerate(devices):
            name = device['name'].lower()
            if ('zoom' in name or 'l6' in name or 'l-6' in name) and device['max_output_channels'] >= 4:
                print(f"Auto-detected Zoom L6: {device['name']} (device {i})")
                return i
        
        print("WARNING: Zoom L6 not auto-detected, using default device")
        return None
    
    def _play_pygame(self, track):
        """Fallback: Play using pygame (mixed stereo)"""
        try:
            sound_title = pygame.mixer.Sound(str(track.title_wav))
            sound_metronome = pygame.mixer.Sound(str(track.metronome_wav))
            midi_file = MidiFile(str(track.midi_file))
        except Exception as e:
            print(f"Error loading track files: {e}")
            return False
        
        # Reset flags
        self.stop_flag.clear()
        self.is_playing = True
        self.is_paused = False
        
        # Start both audio channels simultaneously
        self.channel_title.play(sound_title)
        self.channel_metronome.play(sound_metronome)
        
        # Start MIDI playback in separate thread
        self.midi_thread = threading.Thread(
            target=self._play_midi_thread,
            args=(midi_file,),
            daemon=True
        )
        self.midi_thread.start()
        
        print("Playback started (pygame - mixed stereo)")
        return True
    
    def _play_midi_thread(self, midi_file):
        """Play MIDI file in a separate thread"""
        if not self.midi_output:
            print("No MIDI output available")
            return
        
        try:
            start_time = time.time()
            
            for msg in midi_file.play():
                if self.stop_flag.is_set():
                    break
                
                # Wait for pause to be released
                while self.is_paused and not self.stop_flag.is_set():
                    time.sleep(0.01)
                
                if self.stop_flag.is_set():
                    break
                
                # Send MIDI message
                if not msg.is_meta:
                    self.midi_output.send(msg)
            
            # Send all notes off when done
            for channel in range(16):
                self.midi_output.send(Message('control_change', 
                                             control=123, 
                                             value=0, 
                                             channel=channel))
            
        except Exception as e:
            print(f"Error in MIDI playback: {e}")
    
    def pause(self):
        """Pause playback"""
        if self.is_playing and not self.is_paused:
            if self.use_sounddevice:
                self.is_paused = True
            else:
                self.channel_title.pause()
                self.channel_metronome.pause()
                self.is_paused = True
            print("Playback paused")
    
    def unpause(self):
        """Resume playback"""
        if self.is_playing and self.is_paused:
            if self.use_sounddevice:
                self.is_paused = False
            else:
                self.channel_title.unpause()
                self.channel_metronome.unpause()
                self.is_paused = False
            print("Playback resumed")
    
    def toggle_pause(self):
        """Toggle pause state"""
        if self.is_paused:
            self.unpause()
        else:
            self.pause()
    
    def stop(self):
        """Stop playback"""
        if self.is_playing:
            self.stop_flag.set()
            
            if not self.use_sounddevice:
                self.channel_title.stop()
                self.channel_metronome.stop()
            
            # Send all notes off
            if self.midi_output:
                for channel in range(16):
                    try:
                        self.midi_output.send(Message('control_change', 
                                                     control=123, 
                                                     value=0, 
                                                     channel=channel))
                    except:
                        pass
            
            # Wait for threads
            if self.audio_thread:
                self.audio_thread.join(timeout=1.0)
            if self.midi_thread:
                self.midi_thread.join(timeout=1.0)
            
            self.is_playing = False
            self.is_paused = False
            print("Playback stopped")
    
    def is_active(self):
        """Check if currently playing"""
        return self.is_playing
    
    def cleanup(self):
        """Clean up resources"""
        self.stop()
        if self.midi_output:
            self.midi_output.close()
        if not self.use_sounddevice:
            pygame.mixer.quit()


# ============================================================================
# OLED DISPLAY
# ============================================================================

class OLEDDisplay:
    """Manages the Argon case OLED display"""
    def __init__(self):
        # Initialize I2C and display
        i2c = busio.I2C(board.SCL, board.SDA)
        self.oled = SSD1306_I2C(DISPLAY_WIDTH, DISPLAY_HEIGHT, i2c, addr=0x3C)
        
        # Create blank image for drawing
        self.image = Image.new("1", (DISPLAY_WIDTH, DISPLAY_HEIGHT))
        self.draw = ImageDraw.Draw(self.image)
        
        # Try to load a font, fall back to default if not available
        try:
            self.font_large = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 16)
            self.font_medium = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 12)
            self.font_small = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 10)
        except:
            self.font_large = ImageFont.load_default()
            self.font_medium = ImageFont.load_default()
            self.font_small = ImageFont.load_default()
        
        self.clear()
        self.show_startup()
    
    def clear(self):
        """Clear the display"""
        self.draw.rectangle((0, 0, DISPLAY_WIDTH, DISPLAY_HEIGHT), outline=0, fill=0)
        self.oled.image(self.image)
        self.oled.show()
    
    def show_startup(self):
        """Show startup message"""
        self.draw.rectangle((0, 0, DISPLAY_WIDTH, DISPLAY_HEIGHT), outline=0, fill=0)
        self.draw.text((10, 20), "Performance Rig", font=self.font_medium, fill=255)
        self.draw.text((20, 40), "Initializing...", font=self.font_small, fill=255)
        self.oled.image(self.image)
        self.oled.show()
    
    def show_track(self, track, position, total, is_playing=False, is_paused=False):
        """Display current track information"""
        self.draw.rectangle((0, 0, DISPLAY_WIDTH, DISPLAY_HEIGHT), outline=0, fill=0)
        
        # Line 1: Track position
        track_text = f"Track {position}/{total}"
        self.draw.text((2, 2), track_text, font=self.font_small, fill=255)
        
        # Line 2: Set and Song number
        title = track.get_display_title()
        self.draw.text((2, 16), title, font=self.font_large, fill=255)
        
        # Line 3: Song name/folder
        song_name = track.song_name
        self.draw.text((2, 36), song_name, font=self.font_medium, fill=255)
        
        # Line 4: Status
        if is_paused:
            status = "|| PAUSED"
        elif is_playing:
            status = "> PLAYING"
        else:
            status = "Press DOWN to play"
        
        self.draw.text((2, 52), status, font=self.font_small, fill=255)
        
        self.oled.image(self.image)
        self.oled.show()
    
    def show_error(self, message):
        """Display error message"""
        self.draw.rectangle((0, 0, DISPLAY_WIDTH, DISPLAY_HEIGHT), outline=0, fill=0)
        self.draw.text((2, 20), "ERROR:", font=self.font_medium, fill=255)
        self.draw.text((2, 35), message, font=self.font_small, fill=255)
        self.oled.image(self.image)
        self.oled.show()


# ============================================================================
# KEYBOARD INPUT HANDLER
# ============================================================================

class KeyboardHandler:
    """Handles USB keyboard input (arrow keys)"""
    def __init__(self, on_key_callback):
        self.on_key_callback = on_key_callback
        self.devices = []
        self.running = False
        self.threads = []
        self.find_keyboards()

    def find_keyboards(self):
        """Find all input devices with arrow keys"""
        for path in list_devices():
            try:
                device = InputDevice(path)
                capabilities = device.capabilities(verbose=False)
                if ecodes.EV_KEY in capabilities:
                    keys = capabilities[ecodes.EV_KEY]
                    if (KEY_UP in keys and KEY_DOWN in keys and
                            KEY_LEFT in keys and KEY_RIGHT in keys):
                        self.devices.append(device)
                        print(f"Found keyboard: {device.name} at {device.path}")
            except Exception:
                pass

        if not self.devices:
            print("Warning: No keyboard with arrow keys found")
        return bool(self.devices)

    def start(self):
        """Start listening for keyboard input"""
        if not self.devices:
            print("No keyboard device available")
            return False

        self.running = True
        for device in self.devices:
            t = threading.Thread(target=self._input_loop, args=(device,), daemon=True)
            t.start()
            self.threads.append(t)
        return True

    def _input_loop(self, device):
        """Main input loop (runs in separate thread per device)"""
        print(f"Keyboard handler started: {device.name}")
        try:
            for event in device.read_loop():
                if not self.running:
                    break

                # Only process key down events
                if event.type == ecodes.EV_KEY:
                    key_event = categorize(event)
                    if key_event.keystate == 1:  # Key down
                        self.on_key_callback(event.code)

        except Exception as e:
            print(f"Keyboard handler error ({device.name}): {e}")

    def stop(self):
        """Stop the keyboard handler"""
        self.running = False
        for t in self.threads:
            t.join(timeout=1.0)


# ============================================================================
# MAIN CONTROLLER
# ============================================================================

class PerformanceRig:
    """Main controller coordinating all components"""
    def __init__(self):
        print("Initializing Performance Rig...")

        # Stop Argon One daemon so it doesn't clobber our I2C/OLED writes
        self._stop_argon_daemon()

        # Initialize components
        self.display = OLEDDisplay()
        self.track_manager = TrackManager(MUSIC_ROOT)
        self.player = AudioMidiPlayer(VIRTUAL_MIDI_PORT, audio_device=AUDIO_DEVICE)
        self.keyboard = KeyboardHandler(self.on_key_press)
        self.processing_process = None
        
        # Check if we have tracks
        if self.track_manager.get_track_count() == 0:
            self.display.show_error("No tracks found!")
            print("ERROR: No tracks found in", MUSIC_ROOT)
            sys.exit(1)
        
        # Start Processing sketch
        self.start_processing()
        
        # Show initial track
        self.update_display()
        
        # Start keyboard handler
        self.keyboard.start()
        
        print("Performance Rig ready!")
        print("Controls:")
        print("  LEFT  = Previous track")
        print("  RIGHT = Next track")
        print("  DOWN  = Play current track")
        print("  UP    = Pause/Resume")
    
    def start_processing(self):
        """Launch the Processing sketch"""
        if not PROCESSING_SKETCH.exists():
            print(f"Warning: Processing sketch not found at {PROCESSING_SKETCH}")
            return

        try:
            # Make sure it's executable
            os.chmod(PROCESSING_SKETCH, 0o755)

            # Launch Processing sketch
            self.processing_process = subprocess.Popen(
                [str(PROCESSING_SKETCH)],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE
            )
            print(f"Processing sketch launched (PID: {self.processing_process.pid})")

            # Give Processing time to start and register MIDI
            time.sleep(3)

            # Wire virtual MIDI port to Processing's input if needed
            self._connect_midi_to_processing()

        except Exception as e:
            print(f"Error launching Processing sketch: {e}")

    def _connect_midi_to_processing(self):
        """Use aconnect to bridge our virtual MIDI port to Processing's input"""
        try:
            # Re-check available ports — Processing may have opened one by now
            available_ports = mido.get_output_names()
            for port in available_ports:
                if MIDI_PORT_FOR_PROCESSING in port:
                    # Processing's port is already directly openable — nothing to bridge
                    return

            # Virtual port is in use; try to connect it via aconnect
            result = subprocess.run(
                ['aconnect', f'{VIRTUAL_MIDI_PORT}:0', f'{MIDI_PORT_FOR_PROCESSING}:0'],
                capture_output=True, text=True
            )
            if result.returncode == 0:
                print(f"aconnect: {VIRTUAL_MIDI_PORT} -> {MIDI_PORT_FOR_PROCESSING}")
            else:
                # Log available ALSA ports to help debug
                ports = subprocess.run(['aconnect', '-i'], capture_output=True, text=True)
                print(f"aconnect failed: {result.stderr.strip()}")
                print(f"Available ALSA inputs:\n{ports.stdout}")
        except FileNotFoundError:
            print("aconnect not found — install with: sudo apt install alsa-utils")
        except Exception as e:
            print(f"MIDI bridge error: {e}")
    
    def on_key_press(self, key_code):
        """Handle keyboard input"""
        if key_code == KEY_LEFT:
            print("← Previous track")
            self.player.stop()
            self.track_manager.previous_track()
            self.update_display()
            
        elif key_code == KEY_RIGHT:
            print("→ Next track")
            self.player.stop()
            self.track_manager.next_track()
            self.update_display()
            
        elif key_code == KEY_DOWN:
            print("↓ Play")
            track = self.track_manager.get_current_track()
            if track:
                self.player.play(track)
                self.update_display()
            
        elif key_code == KEY_UP:
            print("↑ Pause/Resume")
            self.player.toggle_pause()
            self.update_display()
    
    def update_display(self):
        """Update OLED with current track info"""
        track = self.track_manager.get_current_track()
        if track:
            self.display.show_track(
                track,
                self.track_manager.get_track_position(),
                self.track_manager.get_track_count(),
                self.player.is_active(),
                self.player.is_paused
            )
    
    def run(self):
        """Main run loop"""
        try:
            while True:
                time.sleep(0.1)
                
        except KeyboardInterrupt:
            print("\nShutting down...")
            self.cleanup()
    
    def _stop_argon_daemon(self):
        """Stop Argon One daemon to prevent I2C/OLED conflicts during operation"""
        for service in ('argononed', 'argone-oled'):
            try:
                result = subprocess.run(
                    ['systemctl', 'stop', service],
                    capture_output=True, text=True
                )
                if result.returncode == 0:
                    print(f"Stopped {service}")
                    self._argon_service = service
                    return
            except Exception:
                pass
        print("Note: argononed service not found or already stopped")
        self._argon_service = None

    def _start_argon_daemon(self):
        """Restart Argon One daemon after we release the display"""
        if self._argon_service:
            try:
                subprocess.run(['systemctl', 'start', self._argon_service], capture_output=True)
                print(f"Restarted {self._argon_service}")
            except Exception as e:
                print(f"Could not restart {self._argon_service}: {e}")

    def cleanup(self):
        """Clean up all resources"""
        print("Cleaning up...")
        self.keyboard.stop()
        self.player.cleanup()

        if self.processing_process:
            print("Stopping Processing sketch...")
            self.processing_process.terminate()
            try:
                self.processing_process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.processing_process.kill()

        self.display.clear()
        self._start_argon_daemon()
        print("Shutdown complete")


# ============================================================================
# ENTRY POINT
# ============================================================================

def main():
    # Check if running as root (needed for some GPIO operations)
    if os.geteuid() != 0:
        print("Warning: Not running as root. Some features may not work.")
    
    # Create and run the rig
    rig = PerformanceRig()
    rig.run()


if __name__ == "__main__":
    main()
