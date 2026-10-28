"""Core checks. Run from the visualiser folder: python -m unittest discover -s tests -t ."""

import json
import shutil
import tempfile
import unittest
import wave
from pathlib import Path

import numpy as np

from quincy_vis.analysis import (
    FEATURE_NAMES, FEATURE_RATE, Features, _analyse_blocks, _normalise, follow, impulse, ensure_analysis,
)
from quincy_vis.audio import iter_mono_blocks
from quincy_vis.export import render_video
from quincy_vis.presets import PresetError, load_preset, validate_preset
from quincy_vis.renderer import Renderer, _hash3, interp_matrix, palette_lut, vnoise

ROOT = Path(__file__).resolve().parents[1]
PRESET = ROOT / "presets" / "warm_haze.json"
RATE = 44100


def write_kick_wav(path, seconds=20.0, kick_every=0.5, first_kick=2.0):
    """Mono 16-bit WAV: a decaying 60 Hz kick every half second, otherwise silence."""
    t = np.arange(int(seconds * RATE)) / RATE
    signal = np.zeros_like(t)
    kicks = np.arange(first_kick, seconds - 1, kick_every)
    for k in kicks:
        local = t - k
        mask = local >= 0  # no hard cut: a cut would itself be a broadband onset
        signal[mask] += 0.8 * np.sin(2 * np.pi * 60 * local[mask]) * np.exp(-local[mask] * 25)
    pcm = (np.clip(signal, -1, 1) * 32767).astype("<i2")
    with wave.open(str(path), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(RATE)
        out.writeframes(pcm.tobytes())
    return kicks


def synthetic_features(seconds=4.0):
    n = int(seconds * FEATURE_RATE)
    values = {name: np.linspace(0, 1, n, dtype=np.float32) for name in FEATURE_NAMES}
    return Features(times=np.arange(n) / FEATURE_RATE, values=values, duration=seconds)


class SignalTests(unittest.TestCase):
    def test_follow_reaches_63_percent_after_attack_time(self):
        x = np.ones(600, np.float32)
        out = follow(x, attack_s=0.5, release_s=1.0)
        at_attack = out[int(0.5 * FEATURE_RATE) - 1]
        self.assertAlmostEqual(at_attack, 1 - np.exp(-1), delta=0.03)

    def test_follow_falls_to_1_over_e_after_release_time(self):
        x = np.concatenate([np.ones(60), np.zeros(30)]).astype(np.float32)
        out = follow(x, attack_s=0.05, release_s=0.5)  # 30 samples = 0.5 s of release
        self.assertAlmostEqual(out[-1], np.exp(-1), delta=0.03)

    def test_impulse_fires_once_inside_refractory_period(self):
        x = np.zeros(120, np.float32)
        x[10] = 0.9
        x[12] = 0.9  # bounces back above threshold within 0.18 s; must not re-trigger
        out = impulse(x, threshold=0.6, refractory_s=0.18, decay_s=0.4)
        self.assertEqual(np.count_nonzero(out == 1.0), 1)
        self.assertAlmostEqual(out[10], 1.0)

    def test_normalise_is_bounded(self):
        x = np.linspace(-80, -10, 500)
        out = _normalise(x)
        self.assertGreaterEqual(out.min(), 0.0)
        self.assertLessEqual(out.max(), 1.0)


class NoiseAndPaletteTests(unittest.TestCase):
    def test_hash_is_deterministic_and_bounded(self):
        ix = np.arange(-50, 50).reshape(10, 10)
        a = _hash3(ix, ix + 3, 7, seed=42)
        b = _hash3(ix, ix + 3, 7, seed=42)
        np.testing.assert_array_equal(a, b)
        self.assertTrue(np.all(a >= -1) and np.all(a <= 1))

    def test_value_noise_is_continuous_in_time(self):
        x = np.linspace(0, 3, 64, dtype=np.float32).reshape(8, 8)
        y = x.T.copy()
        a = vnoise(x, y, 10.000, seed=5)
        b = vnoise(x, y, 10.001, seed=5)
        self.assertLess(np.max(np.abs(a - b)), 0.01)

    def test_interp_matrix_rows_sum_to_one(self):
        m = interp_matrix(1920, 480)
        np.testing.assert_allclose(m.sum(axis=1), 1.0, rtol=1e-5)

    def test_palette_lut_hits_endpoints(self):
        lut = palette_lut([[0.0, "#000000"], [1.0, "#ffffff"]], size=5)
        np.testing.assert_allclose(lut[0], [0, 0, 0])
        np.testing.assert_allclose(lut[-1], [255, 255, 255])


class PresetTests(unittest.TestCase):
    def test_bundled_preset_loads(self):
        preset = load_preset(PRESET)
        self.assertEqual(preset["id"], "warm_haze")

    def test_seed_override(self):
        self.assertEqual(load_preset(PRESET, seed=7)["seed"], 7)

    def test_unknown_channel_is_rejected(self):
        data = json.loads(PRESET.read_text())
        data["response"]["sparkles"] = {"feature": "bass", "gain": 1, "max": 1}
        with self.assertRaises(PresetError):
            validate_preset(data)


class RendererTests(unittest.TestCase):
    def setUp(self):
        self.preset = load_preset(PRESET)
        self.renderer = Renderer(self.preset, synthetic_features(), width=160, height=90, fps=30)

    def test_frame_shape_and_type(self):
        frame = self.renderer.render(1.0)
        self.assertEqual(frame.shape, (90, 160, 3))
        self.assertEqual(frame.dtype, np.uint8)

    def test_same_time_gives_identical_pixels(self):
        np.testing.assert_array_equal(self.renderer.render(2.5), self.renderer.render(2.5))

    def test_different_times_change_the_image(self):
        self.assertFalse(np.array_equal(self.renderer.render(1.0), self.renderer.render(9.0)))

    def test_grain_can_be_disabled(self):
        self.preset["grain"]["amount"] = 0.0
        renderer = Renderer(self.preset, synthetic_features(), width=160, height=90, fps=30)
        np.testing.assert_array_equal(renderer.render(1.0), renderer.render(1.0))


class AnalysisTests(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_kick_drum_drives_bass_and_onsets(self):
        wav = self.dir / "kicks.wav"
        kicks = write_kick_wav(wav)
        raw = _analyse_blocks(iter_mono_blocks(wav, 22050, block_seconds=3.0), 22050, 20.0)
        times = np.arange(raw["bass"].size) / FEATURE_RATE

        bass = raw["bass"]
        on_kick = np.array([bass[np.argmin(np.abs(times - (k + 0.03)))] for k in kicks])
        between = np.array([bass[np.argmin(np.abs(times - (k + 0.25)))] for k in kicks])
        self.assertGreater(on_kick.mean(), between.mean() + 3.0)

        onset = raw["onset"]
        for k in kicks[:10]:
            near = onset[(times >= k - 0.02) & (times <= k + 0.08)].max()
            later = onset[(times >= k + 0.2) & (times <= k + 0.3)].max()
            self.assertGreater(near, later)

    def test_ensure_analysis_caches_and_returns_normalised_features(self):
        wav = self.dir / "kicks.wav"
        write_kick_wav(wav, seconds=6.0)
        cache = self.dir / "cache"
        first = ensure_analysis(wav, cache, log=lambda *_: None)
        second = ensure_analysis(wav, cache, log=lambda *_: None)
        self.assertEqual(first, second)
        features = Features.load(first)
        for name in FEATURE_NAMES:
            self.assertGreaterEqual(features.values[name].min(), 0.0)
            self.assertLessEqual(features.values[name].max(), 1.0)


class ExportTests(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_short_export_has_expected_duration_size_and_audio(self):
        wav = self.dir / "mix.wav"
        write_kick_wav(wav, seconds=3.0)
        out = self.dir / "out.mp4"
        summary = render_video(
            wav, PRESET, out, width=320, height=180, fps=10, workers=1,
            cache_dir=self.dir / "cache", log=lambda *_: None,
        )
        self.assertTrue(out.exists())
        self.assertFalse((self.dir / "out.partial.mp4").exists())
        self.assertAlmostEqual(summary["duration"], 3.0, delta=0.15)
        self.assertEqual(summary["audio_codec"], "aac")


if __name__ == "__main__":
    unittest.main()
