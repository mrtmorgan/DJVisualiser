# Handover: DJ / Quincy visualiser

Last updated: 28 October 2026. Latest commit on `main`: see `git log -1`.

## What this is

An offline tool that turns a finished DJ mix into a video of procedural, warm, abstract visuals that move with the music. The original mix is muxed in as AAC. Built for Quincy (DJ mixes for YouTube). Target: 1080p30 first, 4K later.

## Where things are

- Repo: `mrtmorgan/DJVisualiser` (GitHub). Branch: `main`.
- The old home was the cookie website repo (`mrtmorgan/RylesCookiesWebsite`, branch `claude/quincy-visualiser`). The branch still exists there and should be deleted on GitHub by hand. Its commits are copied here.
- The session's clone of the repo was at `/home/user/djvisualiser`. That path may not exist in a new chat.

## Layout

- `quincy_vis/` — Python package
  - `analysis.py` — streaming audio features (bass, mid, high, rms, onset), cached as `.npz`
  - `renderer.py` — procedural renderer (noise field, palette, grain)
  - `export.py` — offline render, streams frames into ffmpeg, muxes the mix, writes `.partial` until verified
  - `queue.py` — render queue from a JSON jobs file; each job has its own output path, may be on `/Volumes/...`
  - `presets.py` — preset validation, genre resolution
  - `__main__.py` — CLI
- `presets/warm_haze.json` — base preset
- `presets/genres/` — one preset per genre, plus `genres.json` mapping genre → preset
- `docs/genre_styles.md` — genre table (palette, family, response emphasis). Provisional.
- `examples/jobs.example.json` — jobs file template (paths need editing)
- `scripts/setup_mac.sh` — one-time macOS setup (Homebrew ffmpeg/python, venv, tests)
- `tests/test_core.py` — 22 tests

## Commands

```
python -m unittest discover -s tests -t .
python -m quincy_vis analyse --audio mix.wav
python -m quincy_vis still --audio mix.wav --preset presets/genres/afro_house.json --time 120 --out frame.png
python -m quincy_vis render --audio mix.wav --preset presets/genres/afro_house.json --start 120 --duration 30 --width 960 --height 540 --out preview.mp4
python -m quincy_vis queue --jobs my_jobs.json
```

A queue job takes `audio`, `out`, and either `genre` or `preset` (not both). Optional: `width`, `height`, `fps`, `start`, `duration`, `seed`, `crf`.

## Status

Tested here (Linux sandbox, 4 vCPU, numpy 2.5, ffmpeg 6.1, Python 3.13):
- 22 tests pass.
- Synthetic kick track: bass and onsets line up with the kicks (after fixing a 46 ms offset bug).
- 1080p still frame. 30 s 1080p export: 900 frames, output 491 MB at CRF 18 with the old grain level. Duration and stream check pass.
- Per-frame cost on one core: 153 ms at 1080p, 633 ms at 4K (down from 236 and 1,062 ms).
- File size: grain dominates the bitrate. CRF 18 ≈ 130 Mb/s, CRF 26 ≈ 27 Mb/s at grain 0.045.

Not tested:
- macOS / Apple Silicon. Nothing has run on a Mac yet.
- Any real mix. All tests used synthetic audio.
- Full-length renders (10 minutes, one hour). Memory growth and timing over long runs are unknown.
- Visual quality of the genre presets. Palettes and response values are first guesses.

Known limits:
- CPU only. Speed will be a problem for a full hour. The planned fix is a Metal/Swift renderer on the M1, not started (no Swift toolchain in the sandbox, so it can't be compiled here).
- Grain tile repeats every 1,024 px with a per-frame offset. Check it visually on a real output.
- The preview and export share the same field grid, so the look matches across resolutions. Grain size scales with output resolution.

## Decisions still open (Quincy / Tom)

- Genre scope: per mix or per track? Mixes that change genre partway need timestamped segments.
- Mixed sets: one look for the whole mix, or a crossfade between genre looks?
- Genre-to-family map: confirm the table in `docs/genre_styles.md`. Afrobeats and afro house are separate; afro house is the calmer one (Black Coffee-style).
- Grain level and delivery bitrate for YouTube (CRF choice, or a lower grain setting).
- Palette variants: which colours for each genre.
- Reference images: the three poster screenshots and the Pinterest board stills are the calibration set. The Pinterest pin itself could not be fetched (network policy).

## Blocked or waiting

- Pinterest link (`pin.it/7FOZYhGsn`): the sandbox can't resolve it. Screenshots are the route instead.
- Creating GitHub repos from the session is refused (403). The repo was created by hand and attached.
- Deleting the old branch on the cookie repo is refused from the session (403). Delete `claude/quincy-visualiser` on GitHub by hand.
- The Mac has not been set up. Quincy's Mac: M1, 16 GB. Run `bash scripts/setup_mac.sh` from a clone.
- No real mix has been provided yet.

## Next steps, in order

1. Run `scripts/setup_mac.sh` on the Mac and send back the output. Fix anything that fails.
2. Render a still per genre and compare with the references. Adjust palettes and grain.
3. Test on a 2–3 minute excerpt of a real mix (send the file or its path). Check ripple and expansion response.
4. Ten-minute render with memory and time logged, to confirm nothing grows or resets.
5. Decide the open questions above.
6. Only then: full-hour render, and the Metal renderer for speed.

## Notes for the next session

- Work in `mrtmorgan/DJVisualiser` on `main`.
- Commit messages end with the Claude co-author and session trailer lines already used in this repo.
- Keep outputs and caches out of git (`cache/`, `outputs/`, `*.partial.mp4` are ignored).
- Don't claim a render or a look is good without viewing a still or a preview.
