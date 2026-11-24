"""Jobs that should be made on the PC with OpenMontage (Shorts Studio: "OpenMontage" ticked) wait in
pc-jobs/. The PC agent (/pc-jobs in Claude Code) claims a job by moving it to pc-jobs/working/ and,
when the video is done, sends it to incoming/ and moves the job to pc-jobs/done/.
If the PC is off (nobody claims a job within WAIT_MIN minutes, or a claimed job is stuck for
STUCK_MIN minutes), the cloud makes it instead - with the same voice choice."""
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WAIT_MIN = 40
STUCK_MIN = 240


def log(*a):
    print("[pc-jobs]", *a, flush=True)


def _age_min(path):
    r = subprocess.run(["git", "log", "-1", "--format=%ct", "--", str(path.relative_to(ROOT))],
                       cwd=ROOT, capture_output=True, text=True)
    t = int(r.stdout.strip() or time.time())
    return (time.time() - t) / 60


def takeover():
    base = ROOT / "pc-jobs"
    moved = 0
    if not base.is_dir():
        print("MOVED=0")
        return 0
    cands = [(p, WAIT_MIN) for p in sorted(base.glob("*.txt"))]
    cands += [(p, STUCK_MIN) for p in sorted((base / "working").glob("*.txt"))] if (base / "working").is_dir() else []
    for job, limit in cands:
        age = _age_min(job)
        if age < limit:
            continue
        text = job.read_text(encoding="utf-8")
        remake = "\nkind: remake" in "\n" + text
        dest = ROOT / ("remake" if remake else "scripts") / job.name
        dest.parent.mkdir(exist_ok=True)
        dest.write_text(text, encoding="utf-8")
        job.unlink()
        moved += 1
        log(f"{job.name}: PC did not take it in {int(age)} min -> made in the cloud instead")
    print(f"MOVED={moved}")
    return moved


if __name__ == "__main__":
    if sys.argv[1:] == ["takeover"]:
        takeover()
