"""Render queue: a JSON list of jobs, run one after another, each writing to its own output path.

Output paths can be anywhere the machine can write, including external drives such as /Volumes/<name>.
A job whose output already exists is skipped unless forced, so a queue can be re-run after an interruption.
Failures are recorded and the queue moves on to the next job.
"""

import json
import time
from datetime import datetime, timezone
from pathlib import Path

from .export import render_video
from .presets import resolve_genre

DEFAULTS = {"width": 1920, "height": 1080, "fps": 30, "start": 0.0, "duration": None, "seed": None, "crf": 18}


def load_jobs(jobs_path):
    jobs_path = Path(jobs_path).resolve()
    data = json.loads(jobs_path.read_text())
    entries = data["jobs"] if isinstance(data, dict) else data
    jobs = []
    for index, entry in enumerate(entries, start=1):
        job = {**DEFAULTS, **entry}
        for key in ("audio", "out"):
            if not job.get(key):
                raise ValueError(f"Job {index} is missing '{key}'.")
        if bool(job.get("genre")) == bool(job.get("preset")):
            raise ValueError(f"Job {index} needs exactly one of 'genre' or 'preset'.")
        if job.get("genre"):
            job["preset"] = str(resolve_genre(job["genre"]))
        # Relative audio and preset paths are resolved against the jobs file, so the queue can move with its folder.
        job["audio"] = str((jobs_path.parent / job["audio"]).resolve()) if not Path(job["audio"]).is_absolute() else job["audio"]
        if not Path(job["preset"]).is_absolute():
            job["preset"] = str((jobs_path.parent / job["preset"]).resolve())
        job["out"] = str(Path(job["out"]).expanduser())
        job["index"] = index
        jobs.append(job)
    return jobs


def check_output_location(out_path):
    """Refuse to write into a /Volumes/<name> folder when that drive is not connected.

    Without this check, creating the folder would quietly write to the internal disk.
    """
    path = Path(out_path).expanduser().absolute()
    parts = path.parts
    if len(parts) >= 3 and parts[1] == "Volumes":
        volume = Path("/Volumes") / parts[2]
        if not volume.is_dir():
            raise OSError(f"The drive '{parts[2]}' is not connected. Reconnect it and run the queue again.")


def run_queue(jobs_path, *, force=False, cache_dir, log=print):
    jobs_path = Path(jobs_path).resolve()
    jobs = load_jobs(jobs_path)
    report_path = jobs_path.with_name(jobs_path.stem + ".report.json")
    results = []
    log(f"Queue: {len(jobs)} job(s) from {jobs_path.name}")

    try:
        for job in jobs:
            label = f"[{job['index']}/{len(jobs)}] {Path(job['out']).name}"
            record = {"index": job["index"], "out": job["out"], "started": _now()}
            try:
                check_output_location(job["out"])
                out_path = Path(job["out"])
                if out_path.exists() and not force:
                    record["status"] = "skipped (output exists)"
                    log(f"{label}: skipped, output already exists")
                else:
                    log(f"{label}: rendering")
                    summary = render_video(
                        job["audio"], job["preset"], out_path, width=job["width"], height=job["height"],
                        fps=job["fps"], start=job["start"], duration=job["duration"], seed=job["seed"],
                        crf=job["crf"], cache_dir=cache_dir, log=lambda message: log(f"  {message}"),
                    )
                    record["status"] = "done"
                    record["duration"] = summary["duration"]
                    record["bytes"] = summary["bytes"]
            except KeyboardInterrupt:
                record["status"] = "cancelled"
                results.append(record)
                log(f"{label}: cancelled. Partial file left as *.partial.mp4; the rest of the queue did not run.")
                break
            except Exception as error:  # keep going: one bad job should not stop the queue
                record["status"] = "failed"
                record["error"] = str(error)
                log(f"{label}: failed: {error}")
            record["finished"] = _now()
            results.append(record)
    finally:
        report = {"jobs_file": str(jobs_path), "run_at": _now(), "results": results}
        report_path.write_text(json.dumps(report, indent=2) + "\n")

    done = sum(1 for r in results if r["status"] == "done")
    failed = sum(1 for r in results if r["status"] == "failed")
    log(f"Queue finished: {done} rendered, {failed} failed, report at {report_path}")
    return results


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
