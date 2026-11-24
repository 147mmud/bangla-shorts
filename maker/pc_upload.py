"""Videos made on the PC (OpenMontage agent) arrive in incoming/ as small parts + a .json card.
This joins the parts, publishes the video as a GitHub release and puts it in the channel's
YouTube queue (feeds.publish), exactly like videos made here."""
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))


def log(*a):
    print("[pc-upload]", *a, flush=True)


def main():
    import yaml
    import feeds
    cfg = yaml.safe_load((ROOT / "config.yml").read_text(encoding="utf-8")) or {}
    inc = ROOT / "incoming"
    done = []
    for card in sorted(inc.glob("*.json")):
        info = json.loads(card.read_text(encoding="utf-8"))
        stem = card.stem
        parts = sorted(inc.glob(stem + ".part*"))
        need = int(info.get("parts", len(parts)))
        if len(parts) < need:
            log(f"{stem}: waiting for parts ({len(parts)}/{need})")
            continue
        name = info.get("file") or (stem + ".mp4")
        out = Path("/tmp") / name
        with open(out, "wb") as f:
            for p in parts:
                data = p.read_bytes()
                if data[:8] == b"OMPARTv1":  # small marker added on the PC side
                    data = data[16:]
                f.write(data)
        tag = f"short-pc-{stem}-{datetime.now(timezone.utc):%Y%m%d-%H%M%S}"
        notes = f"{info.get('title', '')}\n\nMade on the PC (OpenMontage + OmniVoice)\n\n{info.get('description', '')}"
        r = subprocess.run(["gh", "release", "create", tag, str(out), "--title", info.get("title", stem)[:100],
                            "--notes", notes], cwd=ROOT, capture_output=True, text=True)
        if r.returncode != 0:
            log(f"{stem}: release failed:", r.stderr[-400:])
            continue
        link = f"https://github.com/{os.environ.get('GITHUB_REPOSITORY', '')}/releases/download/{tag}/{name}"
        feeds.publish({"title": info.get("title", stem), "description": info.get("description", ""),
                       "tags": info.get("tags", []), "channel": info.get("channel", "")}, link, cfg)
        with open(ROOT / "results.md", "a", encoding="utf-8") as f:
            f.write(f"| {datetime.now(timezone.utc):%Y-%m-%d %H:%M} | {stem} (made on PC) | [download]({link}) | - |\n")
        for p in parts + [card]:
            p.unlink()
        done.append(stem)
        log(f"{stem}: published and queued for YouTube")
    log("Done:", done or "nothing complete")


if __name__ == "__main__":
    main()
