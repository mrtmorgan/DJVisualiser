"""Time-stamped audio features for a local file.

Analysis uses a mono downmix at ANALYSIS_RATE and never touches the audio that is muxed into the export.
Each feature is sampled FEATURE_RATE times per second, centred on the window it describes, so the
value at time t refers to audio around t. Raw features are cached as .npz files keyed by the source
file's path, size and modification time, plus the analysis settings.
"""

import hashlib
import itertools
import math
import os
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .audio import iter_mono_blocks, probe

ANALYSIS_VERSION = 1
ANALYSIS_RATE = 22050
FEATURE_RATE = 60.0
WINDOW = 2048
ONSET_BANDS = 24
BANDS = {"bass": (30.0, 180.0), "mid": (180.0, 2000.0), "high": (2000.0, 12000.0)}
FEATURE_NAMES = ("rms", "bass", "mid", "high", "onset")


@dataclass
class Features:
    """Normalised feature curves (0..1) on a shared time grid."""

    times: np.ndarray
    values: dict
    duration: float

    def save(self, path):
        arrays = {name: value for name, value in self.values.items()}
        np.savez(path, duration=np.array(self.duration), **arrays)

    @classmethod
    def load(cls, path):
        with np.load(path) as data:
            values = {name: data[name].astype(np.float32) for name in FEATURE_NAMES}
            duration = float(data["duration"])
        times = np.arange(values["rms"].size) / FEATURE_RATE
        return cls(times=times, values=values, duration=duration)


def cache_key(audio_path):
    path = Path(audio_path).resolve()
    stat = path.stat()
    text = (
        f"{path}|{stat.st_size}|{stat.st_mtime_ns}|{ANALYSIS_VERSION}|"
        f"{ANALYSIS_RATE}|{FEATURE_RATE}|{WINDOW}|{ONSET_BANDS}"
    )
    return hashlib.sha256(text.encode()).hexdigest()[:20]


def ensure_analysis(audio_path, cache_dir, log=print):
    """Analyse the file if needed and return the path of the cached feature file."""
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    target = cache_dir / f"{cache_key(audio_path)}.npz"
    if target.exists():
        return target
    log(f"Analysing {Path(audio_path).name} (one pass, cached afterwards)...")
    info = probe(audio_path)
    raw = _analyse_blocks(iter_mono_blocks(audio_path, ANALYSIS_RATE), ANALYSIS_RATE, info["duration"])
    features = Features(
        times=np.arange(raw["rms"].size) / FEATURE_RATE,
        values={name: _normalise(raw[name]) for name in FEATURE_NAMES},
        duration=info["duration"],
    )
    partial = target.with_name(target.stem + ".tmp.npz")
    features.save(partial)
    os.replace(partial, target)
    return target


def _normalise(x):
    """Map a raw curve to 0..1 using its 5th and 95th percentiles, so one loud file does not flatten the rest."""
    low, high = np.percentile(x, [5, 95])
    return np.clip((x - low) / max(high - low, 1e-6), 0.0, 1.0).astype(np.float32)


def _log_band_matrix(freqs, low, high, bands):
    edges = np.geomspace(low, high, bands + 1)
    matrix = np.zeros((freqs.size, bands))
    for j in range(bands):
        mask = (freqs >= edges[j]) & (freqs < edges[j + 1])
        if not mask.any():
            # Very low bands can be narrower than one FFT bin; use the nearest bin instead.
            mask[np.argmin(np.abs(freqs - math.sqrt(edges[j] * edges[j + 1])))] = True
        matrix[:, j] = mask
    return matrix


def _analyse_blocks(blocks, sample_rate, duration):
    """Compute raw features from streamed mono blocks. Memory stays bounded by the block size."""
    half = WINDOW // 2
    n_frames = int(math.ceil(duration * FEATURE_RATE)) + 1
    raw = {name: np.zeros(n_frames, np.float32) for name in FEATURE_NAMES}

    window = np.hanning(WINDOW)
    window_power = float(np.sum(window ** 2))
    freqs = np.fft.rfftfreq(WINDOW, 1.0 / sample_rate)
    nyquist = sample_rate / 2
    band_masks = {
        name: (freqs >= low) & (freqs < min(high, nyquist))
        for name, (low, high) in BANDS.items()
    }
    onset_matrix = _log_band_matrix(freqs, 30.0, nyquist * 0.95, ONSET_BANDS)
    offsets = np.arange(WINDOW)

    # buf[0] is absolute sample -half, so the first frame's window has zeros in front of the audio.
    buf = np.zeros(half, np.float32)
    buf_start = -half  # absolute sample index of buf[0]
    next_frame = 0
    prev_db = None
    padding = [np.zeros(WINDOW, np.float32)]  # lets the final frames see a full window

    for block in itertools.chain(blocks, padding):
        buf = np.concatenate([buf, block])
        buf_end = buf_start + buf.size
        last = min(n_frames, int((buf_end - half) * FEATURE_RATE / sample_rate) + 2)
        if last > next_frame:
            ks = np.arange(next_frame, last)
            centres = np.rint(ks * sample_rate / FEATURE_RATE).astype(np.int64)
            ok = centres + half <= buf_end
            count = int(np.argmin(ok)) if not ok.all() else ks.size
            if count > 0:
                ks = ks[:count]
                centres = centres[:count]
                segments = buf[(centres - half - buf_start)[:, None] + offsets[None, :]]
                raw["rms"][ks] = 10 * np.log10(np.mean(segments.astype(np.float64) ** 2, axis=1) + 1e-12)
                spectrum = np.abs(np.fft.rfft(segments * window, axis=1)) ** 2 / window_power
                for name, mask in band_masks.items():
                    raw[name][ks] = 10 * np.log10(spectrum[:, mask].sum(axis=1) + 1e-12)
                bands_db = 10 * np.log10(spectrum @ onset_matrix + 1e-12)
                if prev_db is None:
                    prev_db = bands_db[0]
                stacked = np.vstack([prev_db[None, :], bands_db])
                raw["onset"][ks] = np.maximum(np.diff(stacked, axis=0), 0).mean(axis=1)
                prev_db = bands_db[-1]
                next_frame = int(ks[-1]) + 1

        # Discard samples no later frame needs, keeping buf_start aligned with buf[0].
        keep_from = int(np.rint(next_frame * sample_rate / FEATURE_RATE)) - half
        drop = min(keep_from - buf_start, buf.size)
        if drop > 0:
            buf = buf[drop:]
            buf_start += drop

    return raw


def follow(x, attack_s, release_s):
    """One-pole attack/release smoothing on a feature curve sampled at FEATURE_RATE."""
    rise = 1 - math.exp(-1 / max(attack_s * FEATURE_RATE, 1e-9))
    fall = 1 - math.exp(-1 / max(release_s * FEATURE_RATE, 1e-9))
    out = []
    y = 0.0
    for v in x.tolist():
        y += (rise if v > y else fall) * (v - y)
        out.append(y)
    return np.asarray(out, np.float32)


def impulse(x, threshold, refractory_s, decay_s):
    """Rising-edge triggers that jump to 1 and decay, ignoring re-triggers inside the refractory period."""
    decay = math.exp(-1 / max(decay_s * FEATURE_RATE, 1e-9))
    refractory = int(round(refractory_s * FEATURE_RATE))
    out = []
    env = 0.0
    cool = 0
    prev = 0.0
    for v in x.tolist():
        env *= decay
        if cool > 0:
            cool -= 1
        elif v >= threshold > prev:
            env = 1.0
            cool = refractory
        prev = v
        out.append(env)
    return np.asarray(out, np.float32)
