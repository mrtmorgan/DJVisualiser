"""Procedural warm-haze renderer.

A frame is a pure function of (preset, features, time). Nothing is carried from one frame to the next,
so any frame can be rendered in isolation, in any order, in parallel workers.

The field is computed on a fixed grid (FIELD_SIZE) whatever the output size, then bilinearly upsampled.
Preview and export therefore share the same look and timing; only the pixel count changes.
"""

import math

import numpy as np

from .analysis import follow, impulse
from .presets import CHANNELS

FIELD_SIZE = (480, 270)
WARP_SIZE = (120, 68)  # the domain warp is smooth, so it is computed on a coarser grid
GRAIN_TILE = 1024
LUT_SIZE = 1024


def _hash3(ix, iy, iz, seed):
    """Integer hash of lattice points into [-1, 1]. Unlike a stored table, it never repeats along time."""
    h = ix.astype(np.uint32) * np.uint32(0x27D4EB2D)
    h ^= iy.astype(np.uint32) * np.uint32(0x165667B1)
    h ^= np.uint32((iz * 0x9E3779B1) & 0xFFFFFFFF)
    h ^= np.uint32(seed & 0xFFFFFFFF)
    h ^= h >> np.uint32(15)
    h *= np.uint32(0x2C1B3C6D)
    h ^= h >> np.uint32(12)
    h *= np.uint32(0x297A2D39)
    h ^= h >> np.uint32(15)
    return h.astype(np.float32) * np.float32(2.0 / 4294967295.0) - 1.0


def vnoise(x, y, z, seed):
    """Smooth 3D value noise in roughly [-1, 1]. z is time, so the noise evolves continuously."""
    x0 = np.floor(x)
    y0 = np.floor(y)
    z0 = math.floor(z)
    fx = x - x0
    fy = y - y0
    fz = z - z0
    ux = fx * fx * (3 - 2 * fx)
    uy = fy * fy * (3 - 2 * fy)
    uz = fz * fz * (3 - 2 * fz)
    ix = x0.astype(np.int64)
    iy = y0.astype(np.int64)

    def corner(dx, dy, dz):
        return _hash3(ix + dx, iy + dy, z0 + dz, seed)

    def lerp(a, b, u):
        return a + (b - a) * u

    z_low = lerp(lerp(corner(0, 0, 0), corner(1, 0, 0), ux), lerp(corner(0, 1, 0), corner(1, 1, 0), ux), uy)
    z_high = lerp(lerp(corner(0, 0, 1), corner(1, 0, 1), ux), lerp(corner(0, 1, 1), corner(1, 1, 1), ux), uy)
    return lerp(z_low, z_high, uz)


def fbm(x, y, z, octaves, seed, gain):
    """Layered value noise. Each octave doubles frequency and is scaled by gain."""
    total = np.zeros_like(x)
    amplitude = 1.0
    norm = 0.0
    for octave in range(octaves):
        frequency = 2.0 ** octave
        total += amplitude * vnoise(x * frequency, y * frequency, z, seed + 101 * octave)
        norm += amplitude
        amplitude *= gain
    return total / norm


def interp_matrix(n_out, n_in):
    """Dense bilinear resampling matrix from n_in samples to n_out samples, aligned at pixel centres."""
    x = (np.arange(n_out) + 0.5) * n_in / n_out - 0.5
    x = np.clip(x, 0, n_in - 1)
    i0 = np.floor(x).astype(np.int64)
    i1 = np.minimum(i0 + 1, n_in - 1)
    w = (x - i0).astype(np.float32)
    matrix = np.zeros((n_out, n_in), np.float32)
    rows = np.arange(n_out)
    matrix[rows, i0] += 1 - w
    matrix[rows, i1] += w
    return matrix


def palette_lut(stops, size=LUT_SIZE):
    """Interpolate colour stops into a lookup table of RGB values in 0..255."""
    positions = [stop[0] for stop in stops]
    colours = [[int(stop[1][i:i + 2], 16) for i in (1, 3, 5)] for stop in stops]
    grid = np.linspace(0, 1, size)
    channels = [np.interp(grid, positions, [c[i] for c in colours]) for i in range(3)]
    return np.stack(channels, axis=1).astype(np.float32)


def _channel_envelope(spec, features):
    x = features.values[spec["feature"]]
    if spec.get("mode", "follow") == "impulse":
        return impulse(x, spec["threshold"], spec["refractory_s"], spec["decay_s"])
    return follow(x, spec["attack_s"], spec["release_s"])


class Renderer:
    def __init__(self, preset, features, width, height, fps):
        self.preset = preset
        self.seed = int(preset["seed"])
        self.fps = fps
        self.width = width
        self.height = height
        self.times = features.times

        field_w, field_h = FIELD_SIZE
        aspect = width / height
        xs = np.linspace(-aspect, aspect, field_w, dtype=np.float32)
        ys = np.linspace(-1, 1, field_h, dtype=np.float32)
        self.X, self.Y = np.meshgrid(xs, ys)
        self.radius = np.sqrt(self.X ** 2 + self.Y ** 2)
        self.up_x = interp_matrix(width, field_w).T.copy()
        self.up_y = interp_matrix(height, field_h)

        warp_w, warp_h = WARP_SIZE
        wxs = np.linspace(-aspect, aspect, warp_w, dtype=np.float32)
        wys = np.linspace(-1, 1, warp_h, dtype=np.float32)
        self.warp_X, self.warp_Y = np.meshgrid(wxs, wys)
        self.warp_up_x = interp_matrix(field_w, warp_w).T.copy()
        self.warp_up_y = interp_matrix(field_h, warp_h)

        # One seeded grain tile, read at a different offset each frame. Cheap, deterministic, no per-frame RNG.
        self.grain_tile = np.random.default_rng(self.seed).standard_normal(
            (GRAIN_TILE, GRAIN_TILE), dtype=np.float32
        )
        self.lut = palette_lut(preset["palette"]["stops"])
        self.envelopes = {
            name: _channel_envelope(spec, features) for name, spec in preset["response"].items()
        }
        self.response = preset["response"]

    def channels(self, t):
        values = {name: 0.0 for name in CHANNELS}
        for name, spec in self.response.items():
            level = float(np.interp(t, self.times, self.envelopes[name]))
            values[name] = min(spec["gain"] * level, spec["max"])
        return values

    def field(self, t):
        p = self.preset["field"]
        c = self.channels(t)
        scale = p["scale"]
        warp_scale = p["warp_scale"]
        z = t * p["drift_speed"]
        gain = 0.5 - 0.25 * p["softness"]

        warp = p["base_warp"] + c["warp"]
        wx = fbm(self.warp_X * warp_scale, self.warp_Y * warp_scale, z, 2, self.seed + 1, 0.5)
        wy = fbm(self.warp_X * warp_scale + 7.3, self.warp_Y * warp_scale - 3.1, z + 11.0, 2, self.seed + 2, 0.5)
        wx = self.warp_up_y @ wx @ self.warp_up_x
        wy = self.warp_up_y @ wy @ self.warp_up_x
        keep = 1 - c["expansion"]
        xw = (self.X + warp * wx) * keep
        yw = (self.Y + warp * wy) * keep

        density = fbm(xw * scale, yw * scale, z, p["octaves"], self.seed + 3, gain)
        texture = (p["texture_base"] + c["texture"]) * vnoise(
            xw * scale * 6, yw * scale * 6, z * 3, self.seed + 4
        )
        contour = c["contour"] * np.sin(density * p["contour_frequency"])
        ripple = c["ripple"] * np.sin(self.radius * 10 - t * 5.0) * np.exp(-self.radius * 1.5)

        contrast = p["contrast"] * (1 + c["contrast"])
        value = 0.5 + 0.5 * np.tanh(contrast * (density + texture + contour + ripple))
        return value.astype(np.float32)

    def render(self, t):
        """Return one frame as uint8 RGB at the renderer's output size for absolute time t (seconds)."""
        frame_id = int(round(t * self.fps))
        field = self.field(t)
        # Colour is applied on the field grid and the RGB result is upsampled, which is cheaper than
        # upsampling the scalar and gathering colours at full output resolution.
        index = np.rint(np.clip(field, 0, 1) * (LUT_SIZE - 1)).astype(np.intp)
        field_rgb = self.lut[index]
        rgb = np.stack([self.up_y @ field_rgb[..., c] @ self.up_x for c in range(3)], axis=-1)

        amount = float(self.preset["grain"]["amount"])
        if amount > 0:
            # The grain offset is a function of the absolute frame number, so a preview and the export agree.
            offset_y = (frame_id * 7919) % GRAIN_TILE
            offset_x = (frame_id * 104729) % GRAIN_TILE
            rows = (offset_y + np.arange(self.height)) % GRAIN_TILE
            cols = (offset_x + np.arange(self.width)) % GRAIN_TILE
            noise = self.grain_tile[rows][:, cols]
            rgb += (amount * 255.0) * noise[..., None]
        return np.clip(rgb + 0.5, 0, 255).astype(np.uint8)
