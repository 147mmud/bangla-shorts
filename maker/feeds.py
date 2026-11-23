"""Small RSS lists (feeds/<channel>.xml) that Zapier watches to upload videos to YouTube.
Channels with `post_times_utc` in config.yml wait in feeds/pending/<channel>.json and are
released one at a time at those times (see .github/workflows/post-on-time.yml)."""
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def log(*a):
    print("[feeds]", *a, flush=True)


def add_to_feed(info, link):
    """feeds/<channel>.xml - a small RSS list of finished videos. Zapier watches it and uploads each new
    video to that channel's YouTube as PRIVATE, with title, description, hashtags and tags."""
    if not link:
        return
    from email.utils import formatdate
    from xml.sax.saxutils import escape
    import re
    cid = re.sub(r"[^a-z0-9_-]", "", str(info.get("channel") or "default").lower()) or "default"
    feed = ROOT / "feeds" / f"{cid}.xml"
    feed.parent.mkdir(exist_ok=True)
    old = feed.read_text(encoding="utf-8") if feed.exists() else ""
    items = re.findall(r"<item>.*?</item>", old, re.S)
    tags = [t.strip() for t in info.get("tags", []) if t.strip()]
    item = ("<item>"
            f"<title>{escape(info['title'][:100])}</title>"
            f"<link>{escape(link)}</link>"
            f'<guid isPermaLink="false">{escape(link)}</guid>'
            f"<description>{escape(info.get('description', '')[:4900])}</description>"
            + "".join(f"<category>{escape(t)}</category>" for t in tags[:30]) +
            f'<enclosure url="{escape(link)}" type="video/mp4" length="0"/>'
            f"<pubDate>{formatdate(usegmt=True)}</pubDate>"
            "</item>")
    items = [item] + items[:29]
    name = escape(info.get("channel_name") or cid)
    feed.write_text('<?xml version="1.0" encoding="UTF-8"?>\n<rss version="2.0"><channel>'
                    f"<title>{name} - new shorts</title><link>https://github.com/{os.environ.get('GITHUB_REPOSITORY', '')}</link>"
                    f"<description>New videos for {name}</description>\n" + "\n".join(items) +
                    "\n</channel></rss>\n", encoding="utf-8")
    log(f"Added to feeds/{cid}.xml (Zapier sends it to YouTube)")


def _cid(info):
    import re
    return re.sub(r"[^a-z0-9_-]", "", str(info.get("channel") or "default").lower()) or "default"


def publish(info, link, cfg):
    """Right away, or - for channels with post_times_utc - wait for the next good posting time."""
    if not link:
        return
    ch = (cfg.get("channels") or {}).get(info.get("channel")) or {}
    info = {**info, "channel_name": ch.get("name", "")}
    if ch.get("post_times_utc"):
        pend = ROOT / "feeds" / "pending" / f"{_cid(info)}.json"
        pend.parent.mkdir(parents=True, exist_ok=True)
        items = json.loads(pend.read_text(encoding="utf-8")) if pend.exists() else []
        items.append({"info": {k: info[k] for k in ("title", "description", "tags", "channel", "channel_name") if k in info},
                      "link": link})
        pend.write_text(json.dumps(items, ensure_ascii=False, indent=1), encoding="utf-8")
        log(f"Waiting for the next posting time ({', '.join(ch['post_times_utc'])} UTC): {info.get('title', '')[:60]}")
    else:
        add_to_feed(info, link)


def release_due():
    """Called at each posting time: move the oldest waiting video of each channel into its feed."""
    import yaml
    cfg = yaml.safe_load((ROOT / "config.yml").read_text(encoding="utf-8")) or {}
    moved = []
    for pend in sorted((ROOT / "feeds" / "pending").glob("*.json")) if (ROOT / "feeds" / "pending").is_dir() else []:
        items = json.loads(pend.read_text(encoding="utf-8") or "[]")
        if not items:
            continue
        per = int(((cfg.get("channels") or {}).get(pend.stem) or {}).get("posts_per_time", 1))
        for it in items[:per]:
            add_to_feed(it["info"], it["link"])
            moved.append(it["info"].get("title", ""))
        pend.write_text(json.dumps(items[per:], ensure_ascii=False, indent=1), encoding="utf-8")
    log("Released:", moved or "nothing waiting")
    return moved


if __name__ == "__main__":
    if sys.argv[1:] == ["release"]:
        release_due()
