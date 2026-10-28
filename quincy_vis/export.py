"""Offline export and still rendering.

Frame n is rendered at t = start + n / fps. Frames go to parallel workers in bounded batches, then
stream into one ffmpeg process that encodes video and muxes the original audio. Output is written to a
.partial file and renamed only after ffmpeg succeeds, so an interrupted run never looks finished.
"""

import multiprocessing as mp
import json
import os
import shutil
import subprocess
import time
from pathlib import Path

from .analysis import Features, ensure_analysis
from .audio import probe
from .presets import load_preset
from .renderer import Renderer

BATCH_PER_WORKER = 4
ESTIMATED_BYTES_PER_SECOND = 6_000_000  # about 48 Mb/s, a conservative ceiling for the disk-space check

_worker_renderer = None


def _init_worker(config):
    global _worker_renderer
    features = Features.load(config["features_path"])
    _worker_renderer = Renderer(config["preset"], features, config["width"], config["height"], config["fps"])


def _render_one(t):
    return _worker_renderer.render(t).tobytes()


def _check_disk(directory, seconds):
    directory.mkdir(parents=True, exist_ok=True)
    free = shutil.disk_usage(directory).free
    needed = seconds * ESTIMATED_BYTES_PER_SECOND * 1.1
    if free < needed:
        raise OSError(
            f"A {seconds:.0f} s export needs about {needed / 1e9:.1f} GB free in {directory}; "
            f"{free / 1e9:.1f} GB is available. Free some space or choose another output folder."
        )


def _ffmpeg_command(partial, audio, start, total, width, height, fps, crf):
    return [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{width}x{height}", "-r", str(fps), "-i", "pipe:0",
        "-ss", f"{start:.6f}", "-i", str(audio),
        "-map", "0:v:0", "-map", "1:a:0",
        "-c:v", "libx264", "-preset", "medium", "-crf", str(crf), "-pix_fmt", "yuv420p", "-profile:v", "high",
        "-c:a", "aac", "-b:a", "320k",
        "-t", f"{total:.6f}", "-movflags", "+faststart",
        str(partial),
    ]


def verify_output(path, expected_duration, width, height, fps):
    """Check the finished file's streams and duration. Returns a summary dict or raises RuntimeError."""
    cmd = ["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)]
    data = json.loads(subprocess.run(cmd, capture_output=True, text=True, check=True).stdout)
    video = next((s for s in data["streams"] if s["codec_type"] == "video"), None)
    audio = next((s for s in data["streams"] if s["codec_type"] == "audio"), None)
    problems = []
    if video is None:
        problems.append("no video stream")
    else:
        if (video["width"], video["height"]) != (width, height):
            problems.append(f"size is {video['width']}x{video['height']}")
        num, den = (int(v) for v in video["r_frame_rate"].split("/"))
        if abs(num / den - fps) > 1e-3:
            problems.append(f"frame rate is {num / den:.3f}")
    if audio is None:
        problems.append("no audio stream")
    duration = float(data["format"]["duration"])
    if abs(duration - expected_duration) > 1.0 / fps + 0.05:
        problems.append(f"duration {duration:.3f}s differs from expected {expected_duration:.3f}s")
    if problems:
        raise RuntimeError("Output check failed: " + "; ".join(problems))
    return {"duration": duration, "width": width, "height": height, "fps": fps,
            "audio_codec": audio["codec_name"], "bytes": os.path.getsize(path)}


def render_video(audio_path, preset_path, out_path, *, width=1920, height=1080, fps=30,
                 start=0.0, duration=None, workers=None, seed=None, crf=18, cache_dir, log=print):
    audio_path = Path(audio_path)
    out_path = Path(out_path)
    info = probe(audio_path)
    available = info["duration"] - start
    if available <= 0:
        raise ValueError(f"Start {start:.2f}s is beyond the end of the mix ({info['duration']:.2f}s).")
    total = available if duration is None else min(duration, available)
    n_frames = int(round(total * fps))
    if n_frames < 1:
        raise ValueError("The requested section is shorter than one frame.")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    _check_disk(out_path.parent, total)
    preset = load_preset(preset_path, seed=seed)
    features_path = ensure_analysis(audio_path, cache_dir, log=log)

    workers = workers or max(1, (os.cpu_count() or 2) - 1)
    partial = out_path.with_name(out_path.stem + ".partial" + out_path.suffix)
    config = {"features_path": str(features_path), "preset": preset,
              "width": width, "height": height, "fps": fps}
    cmd = _ffmpeg_command(partial, audio_path, start, total, width, height, fps, crf)

    log(f"Rendering {n_frames} frames ({total:.2f}s) at {width}x{height} {fps} fps with {workers} workers")
    started = time.monotonic()
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    try:
        context = mp.get_context("spawn")
        with context.Pool(workers, initializer=_init_worker, initargs=(config,)) as pool:
            batch = workers * BATCH_PER_WORKER
            for first in range(0, n_frames, batch):
                ids = range(first, min(first + batch, n_frames))
                times = [start + i / fps for i in ids]
                for frame in pool.map(_render_one, times, chunksize=1):
                    proc.stdin.write(frame)
                elapsed = time.monotonic() - started
                done = ids.stop
                rate = done / elapsed
                log(f"  {done}/{n_frames} frames  {rate:.2f} fps rendered  ETA {(n_frames - done) / rate:.0f}s")
        proc.stdin.close()
        if proc.wait() != 0:
            raise RuntimeError(f"ffmpeg exited with status {proc.returncode}. The partial file is {partial}.")
    except BaseException:
        proc.kill()
        proc.wait()
        log(f"Stopped. Partial output left at {partial} and not presented as complete.")
        raise

    os.replace(partial, out_path)
    summary = verify_output(out_path, total, width, height, fps)
    log(f"Done: {out_path} ({summary['duration']:.2f}s, {summary['width']}x{summary['height']}, "
        f"{summary['fps']} fps, audio {summary['audio_codec']}, {summary['bytes'] / 1e6:.1f} MB, "
        f"{time.monotonic() - started:.0f}s elapsed)")
    return summary


def render_still(audio_path, preset_path, time_s, out_png, *, width=1920, height=1080, fps=30,
                 seed=None, cache_dir, log=print):
    """Render one frame at an absolute time, using the same code path as export."""
    features_path = ensure_analysis(audio_path, cache_dir, log=log)
    preset = load_preset(preset_path, seed=seed)
    renderer = Renderer(preset, Features.load(features_path), width, height, fps)
    rgb = renderer.render(time_s)
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
           "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{width}x{height}", "-i", "pipe:0",
           "-frames:v", "1", str(out_png)]
    subprocess.run(cmd, input=rgb.tobytes(), check=True)
    return Path(out_png)
