#!/usr/bin/env python3
"""
Process waiting scripts ONE BY ONE (runs on GitHub Actions):
  scripts/*.txt  ->  video  ->  GitHub Release (download link)  ->  YouTube (optional)
  then the script is moved to scripts/done/ (or scripts/failed/ with the error).
"""
import json
import os
import subprocess
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from make_short import ROOT, load_config, log, make_short  # noqa: E402


def git(*args, check=True):
    return subprocess.run(["git", *args], cwd=ROOT, check=check, capture_output=True, text=True)


def save_progress(message):
    git("add", "-A", "scripts", "voice", "results.md", "remake")
    if git("diff", "--cached", "--quiet", check=False).returncode == 0:
        return
    git("commit", "-m", message)
    for _ in range(3):
        if git("push", check=False).returncode == 0:
            return
        git("pull", "--rebase", "--autostash", check=False)
    log("WARNING: could not push progress to GitHub")


def publish_release(video: Path, info: dict):
    if not os.environ.get("GH_TOKEN"):
        return ""
    tag = f"short-{video.stem}-{datetime.now(timezone.utc):%Y%m%d-%H%M%S}"
    notes = f"{info['title']}\n\nLength: {info['seconds']}s\n\n{info['description']}"
    r = subprocess.run(
        ["gh", "release", "create", tag, str(video), "--title", info["title"][:100], "--notes", notes],
        cwd=ROOT, capture_output=True, text=True,
    )
    if r.returncode != 0:
        log("Release upload failed:", r.stderr[-500:])
        return ""
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    return f"https://github.com/{repo}/releases/download/{tag}/{video.name}"


def add_result(line):
    p = ROOT / "results.md"
    if not p.exists():
        p.write_text("# Results / ফলাফল\n\n| When (UTC) | Script | Video | YouTube |\n|---|---|---|---|\n", encoding="utf-8")
    with open(p, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def run_remakes():
    """remake/*.txt -> new scripts in scripts/ (made into videos right after)."""
    jobs = sorted((ROOT / "remake").glob("*.txt")) if (ROOT / "remake").is_dir() else []
    if not jobs:
        return 0
    import remake
    fails = 0
    for job in jobs:
        when = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
        log(f"=== Remake job {job.name} ===")
        done_dir = ROOT / "remake" / "done"
        done_dir.mkdir(parents=True, exist_ok=True)
        try:
            for out, err, ref, stem in remake.process_job(job):
                if err:
                    fails += 1
                    fdir = ROOT / "scripts" / "failed"
                    fdir.mkdir(parents=True, exist_ok=True)
                    (fdir / f"{stem}.error.txt").write_text(f"Source: {ref}\n\n{err}\n", encoding="utf-8")
                    add_result(f"| {when} | {stem}.txt (remake of {ref[:60]}) | FAILED - see scripts/failed/{stem}.error.txt | - |")
            job.rename(done_dir / job.name)
        except Exception as e:  # noqa: BLE001
            fails += 1
            traceback.print_exc()
            job.rename(done_dir / job.name)
            (done_dir / f"{job.stem}.error.txt").write_text(f"{e}\n\n{traceback.format_exc()}", encoding="utf-8")
            add_result(f"| {when} | {job.name} (remake) | FAILED - see remake/done/{job.stem}.error.txt | - |")
        save_progress(f"Remake job: {job.name} [skip ci]")
    return fails


def main():
    cfg = load_config()
    remake_fails = run_remakes()
    waiting = sorted(p for p in (ROOT / "scripts").glob("*.txt"))
    if not waiting:
        log("No scripts waiting. Put a .txt file in the scripts folder.")
        return 1 if remake_fails else 0
    limit = int(cfg.get("max_per_run", 5))
    log(f"{len(waiting)} script(s) waiting; making up to {limit} now")
    (ROOT / "scripts" / "done").mkdir(exist_ok=True)
    failures = 0

    for script in waiting[:limit]:
        when = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
        try:
            video, info, new_voice = make_short(script, cfg)
            link = publish_release(video, info)
            yt = ""
            if cfg.get("youtube_upload"):
                from youtube_upload import upload
                vid = upload(video, info, cfg)
                yt = f"https://youtu.be/{vid}" if vid else "upload failed"
            script.rename(ROOT / "scripts" / "done" / script.name)
            add_result(f"| {when} | {script.name} | {'[download](' + link + ')' if link else 'see Actions artifact'} | {yt or '-'} |")
            save_progress(f"Made short: {script.name} [skip ci]")
        except Exception as e:
            failures += 1
            traceback.print_exc()
            failed = ROOT / "scripts" / "failed"
            failed.mkdir(exist_ok=True)
            script.rename(failed / script.name)
            (failed / f"{script.stem}.error.txt").write_text(f"{e}\n\n{traceback.format_exc()}", encoding="utf-8")
            add_result(f"| {when} | {script.name} | FAILED - see scripts/failed/{script.stem}.error.txt | - |")
            save_progress(f"Failed: {script.name} [skip ci]")

    left = len(waiting) - min(limit, len(waiting))
    if left:
        log(f"{left} script(s) still waiting - they will be made in the next run.")
    return 1 if (failures or remake_fails) else 0


if __name__ == "__main__":
    sys.exit(main())
