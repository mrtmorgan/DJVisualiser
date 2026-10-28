# Quincy visualiser (prototype 0.1)

Offline renderer that turns a finished DJ mix into a 1080p30 MP4 of procedural, warm abstract
visuals that move with the audio. The original mix is muxed in as AAC.

Status: a working command-line prototype. Visual calibration is provisional. The full-hour export
has **not** been run.

## Stack and why

- **Python + numpy + the ffmpeg CLI.** Everything runs headless and offline, and each frame is a pure
  function of `(preset, features, time)`, so seeking, previews and parallel rendering all use the
  same code.
- ffmpeg decodes the mix for analysis and encodes the video, so no audio-library dependencies.
- Not chosen for v1: TouchDesigner (GUI, not runnable here), Butterchurn / MediaRecorder (realtime
  browser capture, not a reliable hour-long offline export), StreamDiffusion / TPoS (GPU-heavy and not
  temporally stable), projectM (more engineering than a first gradient style needs). The eight linked
  repositories were **not inspected** in this build; see "Not done".

## Requirements

- Python 3.10+ and `numpy` (`pip install -r requirements.txt`)
- `ffmpeg` and `ffprobe` on PATH (with libx264 and AAC; the stock builds have both)
- Any CPU. Measured on 4 vCPUs, 15 GB RAM, Linux, ffmpeg 6.1.1, Python 3.13, numpy 2.5.

## Run

Run from this folder (`visualiser/`).

```bash
# 1. Analyse a mix once (cached; reused by previews and exports)
python -m quincy_vis analyse --audio "/path/to/mix.wav"

# 2. Look at one frame at 1080p (absolute time in seconds)
python -m quincy_vis still --audio "/path/to/mix.wav" --preset presets/warm_haze.json --time 120 --out frame.png

# 3. Preview a section at lower resolution (same timeline and look as the full export)
python -m quincy_vis render --audio "/path/to/mix.wav" --preset presets/warm_haze.json \
  --out outputs/preview.mp4 --start 600 --duration 30 --width 960 --height 540

# 4. Full export (default 1920x1080, 30 fps, CRF 18)
python -m quincy_vis render --audio "/path/to/mix.wav" --preset presets/warm_haze.json --out outputs/mix_visual.mp4
```

Options: `--seed` overrides the preset seed, `--workers` sets render processes (default CPU count minus
one), `--crf` sets x264 quality, `--start` and `--duration` select a section. Any audio format ffmpeg
reads is accepted.

## What the export does

- Frame *n* is rendered at `t = start + n / fps`. The frame count is `round(duration * fps)`.
- Frames are rendered in batches by parallel workers and streamed into one ffmpeg process over a pipe.
  Memory is bounded by the batch size (about 4 frames per worker, roughly 6 MB each at 1080p).
- Output goes to `<name>.partial.mp4` and is renamed only after ffmpeg succeeds and the result passes
  a check (size, frame rate, audio stream, duration within one frame). Cancelled or failed runs leave
  the `.partial` file, which is not presented as complete.
- A disk-space check runs before the render, using a conservative 48 Mb/s ceiling.

## Audio analysis

Analysis uses a mono downmix at 22.05 kHz, streamed in 10-second blocks. Features are sampled at 60 Hz,
each centred on a 2048-sample Hann window. The export audio is never altered.

| Feature | Definition | Used for |
|---|---|---|
| `bass` | dB energy, 30–180 Hz | `expansion` (rounded outward push) |
| `mid` | dB energy, 180–2000 Hz | `contour` (ripple density in the field) |
| `high` | dB energy, 2000 Hz to Nyquist (11.025 kHz) | `texture` (fine detail) |
| `rms` | dB level of the window | `warp` (short-term activity) and `contrast` (slow envelope, 8 s attack / 20 s release) |
| `onset` | mean positive change across 24 log bands | `ripple` (impulse with threshold, refractory period and decay) |

Each curve is normalised by its own 5th and 95th percentiles over the whole file. Attack/release
smoothing is set per channel in the preset. Analysis for a file is cached under `cache/`, keyed by
path, size, modification time and analysis version.

## Render queue (for batches and external drives)

Write a JSON list of jobs and run it. Jobs run one after another, each to its own output path:

```bash
python -m quincy_vis queue --jobs examples/jobs.example.json
python -m quincy_vis queue --jobs examples/jobs.example.json --force   # re-render finished outputs
```

Each job takes `audio`, `out`, and either `genre` or `preset` (not both). A `genre` such as `afro_house`
picks its preset from `presets/genres/genres.json`; the list of genres and their families is in
`docs/genre_styles.md`. Optional fields are `width`, `height`, `fps`, `start`, `duration`,
`seed` and `crf`. Relative `audio` and `preset` paths are resolved against the jobs file. `out` can be
any writable path, including `/Volumes/<drive>/...`.

- A job whose output already exists is skipped, so an interrupted queue can be restarted safely.
- A failed job is recorded and the queue continues. A cancelled job leaves `*.partial.mp4` and stops the queue.
- If a `/Volumes/<drive>` is not connected, that job fails with a message instead of writing to the internal disk.
- A `<jobs>.report.json` next to the jobs file records the status, time and size of each job.

The live Metal preview and the Swift renderer are planned but not built. The queue is the path that
produces finished files now.

## Presets

`presets/warm_haze.json` is the only preset. Its fields are documented by the code:

- `seed`: fixes the noise. The same audio, preset and seed give the same pixels in the same environment.
- `palette.stops`: colour stops as `[position, "#rrggbb"]`, from 0 to 1.
- `field`: `scale`, `warp_scale`, `base_warp`, `drift_speed` (noise evolution rate), `octaves`,
  `softness` (reduces fine-octave weight), `texture_base`, `contour_frequency`, `contrast`.
- `grain.amount`: standard deviation of per-pixel grain as a fraction of full scale (0.045 ≈ 11 levels).
- `response`: one entry per channel (`expansion`, `warp`, `contour`, `texture`, `ripple`, `contrast`),
  each with its source `feature`, `mode` (`follow` or `impulse`), smoothing times, `gain` and `max`.

Noise is procedural 3D value noise (time is the third axis). It uses an integer hash rather than a
stored table, so it never repeats on a loop.

## Tests

```bash
python -m unittest discover -s tests -t .
```

18 tests, all passing in the build environment: envelope timing, impulse refractory behaviour, noise
continuity, palette endpoints, preset validation, frame determinism, grain toggle, analysis timing on
a synthetic kick track (bass and onsets align with the kicks), caching, and a short end-to-end export
(duration, AAC audio, no leftover `.partial` file).

## Measured in the build environment (synthetic audio, not music)

| Check | Result |
|---|---|
| Unit and end-to-end tests | 18 / 18 pass |
| Still frame 1920×1080 (warm_haze, CRF not involved) | 0.84 s including ffmpeg PNG write; ~0.23 s per frame in-process |
| 30 s export, 1920×1080, 30 fps, grain 0.045, CRF 18, 3 workers | 900 frames in 241 s (3.9 fps); output 491 MB; audio AAC; duration check passed |
| Size at 10 s, same grain: CRF 18 / 23 / 26 | 164 MB / 94 MB / 34 MB |

**The hour-long goal is not yet met.** At 3.9 fps, a 108,000-frame hour is about 7.7 hours on this
4-vCPU machine. At CRF 18 the grain alone pushes the bitrate to roughly 130 Mb/s, about 60 GB per hour.
Both numbers come from the preset's grain level, so the grain level is the first thing to tune.

## Known limits and open items

- **Visual calibration is provisional.** The palette and grain were tuned by eye on three still frames
  against the three reference screenshots (see below). Nothing has been reviewed on a real mix yet.
- **Speed.** Now about 0.15 s per 1080p frame and 0.63 s per 4K frame on one core, measured after moving
  the warp to a coarser grid, colour to the field grid and grain to a seeded tile. Further gains need the GPU
  (Metal on the M1), which is planned but not built.
- **File size.** Grain drives the bitrate. Options: lower grain, a higher CRF for delivery (test
  26–28 on YouTube-style footage), or a filtered grain that is less high-frequency.
- **Preview and export** share the field grid, so the look matches across resolutions. Grain is a
  fixed amplitude per output pixel, so its visual size scales with output resolution.
- **Resumability.** Frames are stateless, so a partial export can be resumed by rendering from a later
  start time and concatenating. This is not built yet.
- **No audio-reactive timing has been checked against real music.** The synthetic kick track verifies
  alignment only.
- **Seeks are exact** because the renderer uses explicit time. Frame bits are reproducible on the same
  software and hardware; no claim is made across machines.

## Reference screenshots (provisional calibration)

The three screenshots supplied mid-session were treated as visual references. Branding and text in them
(for example "APF Studio", "Heat wave" and the artist names) are excluded from the work.

1. **Grainy multicolour blobs** (red, gold, green, pink with heavy speckle). This suggests a grain-led
   variant with stronger texture.
2. **Heat-wave gradient** (orange to cream, stepped and dithered edges, glitch bands). This is the
   "pixelated" direction, which the current renderer does not do.
3. **Fluid orange-cream streaks with blue-violet accents** (soft, light grain). This is the softest
   direction. The current warm palette has no blue-violet accent.

The current `warm_haze.json` sits between 1 and 3: saturated orange-red with moderate grain.

## Not done

- The eight repositories from the brief were not inspected or benchmarked. They are not needed for this
  route, but their reuse potential is unchecked.
- No full-hour render, no 10-minute memory-growth test, no review on a real DJ mix.
- No live preview, UI, or preset editor. The CLI and JSON preset are the interface for now.
- No Windows or macOS test. The code uses `spawn` workers, so it should work there, but it is untested.
