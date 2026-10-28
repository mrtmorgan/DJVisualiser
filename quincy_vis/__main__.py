"""Command-line entry point: python -m quincy_vis <command> ..."""

import argparse
import sys
from pathlib import Path

from .export import render_still, render_video
from .analysis import ensure_analysis
from .presets import PresetError
from .queue import run_queue

DEFAULT_CACHE = Path(__file__).resolve().parents[1] / "cache"


def _common(parser):
    parser.add_argument("--audio", required=True, help="Path to the finished mix (any format ffmpeg reads)")
    parser.add_argument("--preset", required=True, help="Path to a preset JSON file")
    parser.add_argument("--width", type=int, default=1920)
    parser.add_argument("--height", type=int, default=1080)
    parser.add_argument("--fps", type=int, default=30)
    parser.add_argument("--seed", type=int, help="Override the preset seed")
    parser.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE)


def build_parser():
    parser = argparse.ArgumentParser(prog="quincy_vis", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    analyse = commands.add_parser("analyse", help="Analyse a mix and cache its features")
    analyse.add_argument("--audio", required=True)
    analyse.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE)

    still = commands.add_parser("still", help="Render one frame to a PNG for inspection")
    _common(still)
    still.add_argument("--time", type=float, required=True, help="Absolute time in seconds")
    still.add_argument("--out", type=Path, required=True)

    render = commands.add_parser("render", help="Render a video with the original mix audio")
    _common(render)
    render.add_argument("--out", type=Path, required=True)
    render.add_argument("--start", type=float, default=0.0, help="Start time in the mix (seconds)")
    render.add_argument("--duration", type=float, help="Length to render; default is the rest of the mix")
    render.add_argument("--workers", type=int, help="Render processes; default is CPU count minus one")
    render.add_argument("--crf", type=int, default=18, help="x264 quality (lower is better, 18 is visually near-lossless)")

    queue = commands.add_parser("queue", help="Render a list of jobs one after another, each to its own output path")
    queue.add_argument("--jobs", type=Path, required=True, help="JSON file listing the jobs (see README)")
    queue.add_argument("--force", action="store_true", help="Re-render jobs whose output already exists")
    queue.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE)
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        if args.command == "analyse":
            ensure_analysis(args.audio, args.cache_dir)
            print("Analysis cached.")
        elif args.command == "still":
            path = render_still(args.audio, args.preset, args.time, args.out, width=args.width,
                                height=args.height, fps=args.fps, seed=args.seed, cache_dir=args.cache_dir)
            print(f"Wrote {path}")
        elif args.command == "queue":
            results = run_queue(args.jobs, force=args.force, cache_dir=args.cache_dir)
            if any(r["status"] in ("failed", "cancelled") for r in results):
                return 1
        else:
            render_video(args.audio, args.preset, args.out, width=args.width, height=args.height,
                         fps=args.fps, start=args.start, duration=args.duration, workers=args.workers,
                         seed=args.seed, crf=args.crf, cache_dir=args.cache_dir)
    except (ValueError, OSError, RuntimeError, PresetError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Cancelled.", file=sys.stderr)
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
