"""Load and validate style presets. Every behaviour the renderer uses is stated in the file."""

import copy
import json
import re
from pathlib import Path

from .analysis import FEATURE_NAMES

SCHEMA_VERSION = 1
CHANNELS = ("expansion", "warp", "contour", "texture", "ripple", "contrast")
FIELD_KEYS = (
    "scale", "warp_scale", "base_warp", "drift_speed", "octaves",
    "softness", "texture_base", "contour_frequency", "contrast",
)
HEX_COLOUR = re.compile(r"^#[0-9a-fA-F]{6}$")


class PresetError(ValueError):
    pass


def load_preset(path, seed=None):
    try:
        data = json.loads(Path(path).read_text())
    except (OSError, json.JSONDecodeError) as error:
        raise PresetError(f"Cannot read preset {str(path)!r}: {error}") from error
    return validate_preset(data, seed=seed)


def validate_preset(data, seed=None):
    errors = []
    if data.get("schema_version") != SCHEMA_VERSION:
        errors.append(f"schema_version must be {SCHEMA_VERSION}")
    for key in ("id", "name"):
        if not isinstance(data.get(key), str) or not data.get(key):
            errors.append(f"'{key}' must be a non-empty string")
    if not isinstance(data.get("seed"), int):
        errors.append("'seed' must be an integer")

    stops = data.get("palette", {}).get("stops", [])
    if not isinstance(stops, list) or len(stops) < 2:
        errors.append("palette.stops needs at least two [position, '#rrggbb'] entries")
    else:
        positions = [stop[0] for stop in stops]
        if positions != sorted(positions) or positions[0] != 0 or positions[-1] != 1:
            errors.append("palette positions must increase from 0 to 1")
        if not all(HEX_COLOUR.match(str(stop[1])) for stop in stops):
            errors.append("palette colours must be '#rrggbb'")

    field = data.get("field", {})
    for key in FIELD_KEYS:
        if not isinstance(field.get(key), (int, float)):
            errors.append(f"field.{key} must be a number")
    if not 0 <= field.get("softness", 0) <= 1:
        errors.append("field.softness must be between 0 and 1")

    if data.get("grain", {}).get("amount", -1) < 0:
        errors.append("grain.amount must be zero or more")

    response = data.get("response", {})
    for name, spec in response.items():
        if name not in CHANNELS:
            errors.append(f"response.{name} is not a known channel ({', '.join(CHANNELS)})")
            continue
        if spec.get("feature") not in FEATURE_NAMES:
            errors.append(f"response.{name}.feature must be one of {', '.join(FEATURE_NAMES)}")
        if spec.get("mode", "follow") not in ("follow", "impulse"):
            errors.append(f"response.{name}.mode must be 'follow' or 'impulse'")
        if spec.get("gain", -1) < 0 or spec.get("max", -1) < 0:
            errors.append(f"response.{name} gain and max must be zero or more")

    if errors:
        raise PresetError("Invalid preset: " + "; ".join(errors))

    result = copy.deepcopy(data)
    if seed is not None:
        result["seed"] = int(seed)
    return result
