#!/usr/bin/env python3
"""
Process waiting scripts ONE BY ONE (runs on GitHub Actions):
  scripts/*.txt  ->  video  ->  GitHub Release (download link)  ->  YouTube (optional)
  then the script is moved to scripts/done/ (or scripts/failed/ with the error).
"""
import os
import subprocess
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from make_short import ROOT, NeedsApproval, load_config, log, make_short  # noqa: E402


def git(*args, check=True):
    return subprocess.run(["git", *args], cwd=ROOT, check=check, capture_output=True, text=True)


def save_progress(message):
    paths = [p for p in ("scripts", "voice", "results.md", "remake", "feeds", "flow-jobs", "flow-clips", "meta", "approve") if (ROOT / p).exists()]
    git("add", "-A", *paths, check=False)
    if git("diff", "--cached", "--quiet", check=False).returncode == 0:
        return
    git("commit", "-m", message)
    for _ in range(3):
        if git("push", check=False).returncode == 0:
            return
        git("pull", "--rebase", "--autostash", check=False)
    log("WARNING: could not push progress to GitHub")


# ---- friendly download names: the video's own title, in plain English letters ----
_BN_CONS = dict(zip("কখগঘঙচছজঝঞটঠডঢণতথদধনপফবভমযরলশষসহড়ঢ়য়ৎ",
    "k kh g gh ng ch chh j jh n t th d dh n t th d dh n p f b bh m j r l sh sh s h r rh y t".split()))
_BN_VOW = dict(zip("অআইঈউঊঋএঐওঔ", "o a i i u u ri e oi o ou".split()))
_BN_SIGN = dict(zip("ািীুূৃেৈোৌ", "a i i u u ri e oi o ou".split()))
_BN_MISC = {"ং": "ng", "ঃ": "h", "ঁ": "n", "্": "", "়": ""}
_BN_DIG = dict(zip("০১২৩৪৫৬৭৮৯", "0123456789"))


def bangla_to_latin(text):
    out, chars = [], list(text)
    for i, c in enumerate(chars):
        nxt = chars[i + 1] if i + 1 < len(chars) else ""
        if c in _BN_CONS:
            out.append(_BN_CONS[c])
            # the hidden "o" between two letters (not at the end of a word)
            if nxt in _BN_CONS:
                out.append("o")
        elif c in _BN_SIGN: out.append(_BN_SIGN[c])
        elif c in _BN_VOW: out.append(_BN_VOW[c])
        elif c in _BN_MISC: out.append(_BN_MISC[c])
        elif c in _BN_DIG: out.append(_BN_DIG[c])
        else: out.append(c)
    return "".join(out)


def nice_filename(title, fallback):
    """'গুনাহ গোপন রাখার গুরুত্ব | নফসে মুতমাইন্নাহ #shorts' -> 'Gunah-Gopon-Rakhar-Gurutto.mp4'.
    GitHub only keeps plain English letters in download names, so other scripts are spelled out."""
    import re
    t = re.sub(r"#\S+", " ", title or "").split("|")[0].strip(" .।-")
    if not t:
        return fallback
    latin = ""
    if re.search(r"[^\x00-\x7f]", t):
        try:
            from ai_tools import llm
            latin = llm([{"role": "user", "content":
                "Write this video title in plain English letters (a-z) as it SOUNDS, like people type "
                "Banglish/romanised text - do not translate. Max 8 words. Reply with only the words.\n\n" + t}],
                temperature=0.2).strip().splitlines()[0]
        except Exception as e:  # no AI right now -> simple letter-by-letter spelling
            log("filename AI skipped:", str(e)[:120])
        if not latin or re.search(r"[^\x00-\x7f]", latin):
            latin = bangla_to_latin(t)
            from unicodedata import normalize
            latin = normalize("NFKD", latin).encode("ascii", "ignore").decode()
    else:
        latin = t
    words = re.findall(r"[A-Za-z0-9]+", latin)[:10]
    name = "-".join(w[:1].upper() + w[1:] for w in words)[:80].strip("-")
    return (name or fallback) + ".mp4" if name else fallback


def publish_release(video: Path, info: dict):
    if not os.environ.get("GH_TOKEN"):
        return ""
    import shutil
    import tempfile
    tag = f"short-{video.stem}-{datetime.now(timezone.utc):%Y%m%d-%H%M%S}"
    notes = f"{info['title']}\n\nLength: {info['seconds']}s\n\n{info['description']}"
    name = nice_filename(info.get("title", ""), video.name)
    up = Path(tempfile.mkdtemp()) / name
    shutil.copy2(video, up)
    log("Download name:", name)
    r = subprocess.run(
        ["gh", "release", "create", tag, str(up), "--title", info["title"][:100], "--notes", notes],
        cwd=ROOT, capture_output=True, text=True,
    )
    shutil.rmtree(up.parent, ignore_errors=True)
    if r.returncode != 0:
        log("Release upload failed:", r.stderr[-500:])
        return ""
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    return f"https://github.com/{repo}/releases/download/{tag}/{name}"


def cleanup_pictures(script):
    """Your uploaded remake pictures are only needed until the video is made."""
    import re
    done = ROOT / "scripts" / "done" / script.name
    try:
        text = done.read_text(encoding="utf-8")
    except OSError:
        return
    rels = re.findall(r"\[img:\s*file:\s*(remake/inbox/[^\]]+?)\s*\]", text)
    m = re.search(r"^\s*my_images\s*:(.*)$", text.split("\n---", 1)[0], re.M)
    if m:  # your own pictures for a new short
        rels += [x.split(":", 1)[-1].strip() if x.strip().split(":", 1)[0].lower() in ("same", "like") else x.strip()
                 for x in m.group(1).split("|") if x.strip()]
    for rel in rels:
        if not rel.startswith("remake/inbox/"):
            continue
        f = ROOT / rel
        if f.is_file():
            subprocess.run(["git", "rm", "-q", "--ignore-unmatch", rel], cwd=ROOT)
            f.unlink(missing_ok=True)


def cleanup_flow(script):
    """A Flow (3D) short is made: its clips and its waiting job are no longer needed."""
    import re
    done = ROOT / "scripts" / "done" / script.name
    try:
        head = done.read_text(encoding="utf-8").split("\n---", 1)[0]
    except OSError:
        return
    m = re.search(r"^\s*flow_clips\s*:\s*(flow-clips/[\w.\-]+)\s*$", head, re.M)
    if not m:
        return
    for rel in (m.group(1), f"flow-jobs/{script.name}", f"flow-jobs/working/{script.name}"):
        if (ROOT / rel).exists():
            subprocess.run(["git", "rm", "-r", "-q", "--ignore-unmatch", rel], cwd=ROOT)
            subprocess.run(["rm", "-rf", str(ROOT / rel)])


def cleanup_approved(script):
    """The approved pictures are inside the finished video now - their folder is no longer needed."""
    import re
    done = ROOT / "scripts" / "done" / script.name
    try:
        head = done.read_text(encoding="utf-8").split("\n---", 1)[0]
    except OSError:
        return
    m = re.search(r"^\s*approved_images\s*:\s*(approve/[\w.\-]+)\s*$", head, re.M)
    if m and (ROOT / m.group(1)).exists():
        subprocess.run(["git", "rm", "-r", "-q", "--ignore-unmatch", m.group(1)], cwd=ROOT)
        subprocess.run(["rm", "-rf", str(ROOT / m.group(1))])


def save_post_info(info, link):
    """Title / description / keywords of every video, so Shorts Studio can show them for uploading by hand."""
    import json
    import re
    m = re.search(r"/releases/download/([^/]+)/", link or "")
    if not m:
        return
    d = ROOT / "meta"
    d.mkdir(exist_ok=True)
    data = {k: info.get(k) for k in ("title", "description", "tags", "channel", "seconds") if k in info}
    data["link"] = link
    (d / f"{m.group(1)}.json").write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


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
            save_post_info(info, link)
            try:
                import feeds
                feeds.publish(info, link, cfg)
            except Exception as e:  # noqa: BLE001 - the video is still made
                log("Could not add to the YouTube feed:", e)
            yt = ""
            if cfg.get("youtube_upload"):
                from youtube_upload import upload
                vid = upload(video, info, cfg)
                yt = f"https://youtu.be/{vid}" if vid else "upload failed"
            script.rename(ROOT / "scripts" / "done" / script.name)
            cleanup_pictures(script)
            cleanup_flow(script)
            cleanup_approved(script)
            add_result(f"| {when} | {script.name} | {'[download](' + link + ')' if link else 'see Actions artifact'} | {yt or '-'} |")
            save_progress(f"Made short: {script.name} [skip ci]")
        except NeedsApproval:
            # pictures are made; the script waits next to them until you press "অনুমোদন দিন" in Shorts Studio
            script.rename(ROOT / "approve" / script.stem / "script.txt")
            save_progress(f"Pictures waiting for approval: {script.name} [skip ci]")
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
