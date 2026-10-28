"""Probe audio files and stream decoded samples through the ffmpeg CLI."""

import json
import subprocess

import numpy as np


def probe(path):
    """Return sample rate, channel count, codec and duration for the first audio stream."""
    cmd = [
        "ffprobe", "-v", "error", "-select_streams", "a:0",
        "-show_entries", "stream=sample_rate,channels,codec_name:format=duration",
        "-of", "json", str(path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise ValueError(f"Cannot read {path!r}. Check the file exists and is an audio file ffmpeg can decode.")
    data = json.loads(result.stdout or "{}")
    streams = data.get("streams") or []
    if not streams:
        raise ValueError(f"{path!r} has no audio stream.")
    stream = streams[0]
    duration = float(data.get("format", {}).get("duration", 0) or 0)
    if duration <= 0:
        raise ValueError(f"Could not determine the duration of {path!r}.")
    return {
        "sample_rate": int(stream["sample_rate"]),
        "channels": int(stream["channels"]),
        "codec": stream["codec_name"],
        "duration": duration,
    }


def iter_mono_blocks(path, sample_rate, block_seconds=10.0):
    """Yield float32 mono blocks decoded at sample_rate, without holding the whole file in memory.

    The downmix is for analysis only. Export never uses these samples.
    """
    cmd = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(path),
        "-vn", "-ac", "1", "-ar", str(sample_rate), "-f", "f32le", "-",
    ]
    block_bytes = int(block_seconds * sample_rate) * 4
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE)
    try:
        while True:
            raw = proc.stdout.read(block_bytes)
            if not raw:
                break
            usable = len(raw) - len(raw) % 4
            yield np.frombuffer(raw[:usable], dtype=np.float32)
    finally:
        proc.stdout.close()
        proc.wait()
    if proc.returncode not in (0, None):
        raise RuntimeError(f"ffmpeg could not decode {path!r} for analysis (status {proc.returncode}).")
