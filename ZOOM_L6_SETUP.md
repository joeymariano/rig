# Zoom L6 Multi-Channel Audio

Connect the Zoom L6 via USB and set it to **Multi Track** mode:
> Menu → USB → Mode → Multi Track

For full setup (device detection, auto vs. pinned index, udev naming) see **README.md §3**.

## Channel Mapping

Four-channel interleaved output via `sounddevice`:

| Channel | Source | Zoom Output |
|---------|--------|-------------|
| 1 | `title.wav` LEFT  | Output 1 |
| 2 | `title.wav` RIGHT | Output 2 |
| 3 | `metronome.wav` LEFT  | Output 3 |
| 4 | `metronome.wav` RIGHT | Output 4 |

`title.wav` (FOH mix) goes to the front-of-house outputs; `metronome.wav` (click track) goes to the in-ear monitor outputs.

The playing screen shows elapsed set time in the lower half and a per-track countdown above it — both run from these two audio streams staying in sync:

![Playing screen](docs/screen_playing.png)

### Verify channel mapping

```bash
speaker-test -D hw:CARD=L6 -c 4 -t sine
# Plays sine tone to: Front-Left, Front-Right, Rear-Left, Rear-Right
```

## Troubleshooting

**`Invalid number of channels` / `PaErrorCode -9998`**
USB enumeration changed. Set `AUDIO_DEVICE = None` to auto-detect, or find the new index:
```bash
python3 -c "import sounddevice as sd; print(sd.query_devices())"
```

**Only 2 output channels**
Device is in stereo mode. Switch to Multi Track mode as above.

**Clicks or dropouts**
Increase `blocksize` in `_audio_loop` in `controller.py`:
```python
stream = sd.OutputStream(device=device, channels=4, samplerate=sr, blocksize=2048, ...)
```

**Sample rate mismatch** (Zoom L6 runs at 48 kHz)
```bash
soxi your_file.wav | grep "Sample Rate"
sox input.wav -r 48000 output.wav
```

**Audio and MIDI out of sync**
Increase the MIDI delay in `Player.play()`:
```python
self._midi_t = threading.Thread(target=self._midi_loop, args=(midi, start, 0.030), ...)
# Increase the last value if MIDI leads audio
```

**`ModuleNotFoundError: sounddevice`**
```bash
source ~/rig/venv/bin/activate
pip install sounddevice soundfile
```

**Stable device naming (optional)**
Add to `/etc/udev/rules.d/99-zoom.rules`:
```
SUBSYSTEM=="sound", ATTRS{idVendor}=="1686", ATTRS{idProduct}=="0045", ATTR{id}="ZoomL6"
```
```bash
sudo udevadm control --reload && sudo udevadm trigger
```
Device always appears as `hw:ZoomL6` regardless of USB port order.
