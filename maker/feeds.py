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


def _norm(t):
    import re
    t = re.sub(r"#\S+", " ", str(t or "")).lower()
    return re.sub(r"[^\w]+", " ", t).strip()


def check_live():
    """Look at each channel's public YouTube list (RSS) and note which of our videos are really on YouTube.
    Writes feeds/live.json {release tag: {"video": id, "seen": time}} - Shorts Studio shows a mark for these."""
    import re
    import urllib.request
    from datetime import datetime, timezone
    from xml.sax.saxutils import unescape
    import yaml
    cfg = yaml.safe_load((ROOT / "config.yml").read_text(encoding="utf-8")) or {}
    livef = ROOT / "feeds" / "live.json"
    live = json.loads(livef.read_text(encoding="utf-8")) if livef.exists() else {}
    before = dict(live)
    for cid, ch in (cfg.get("channels") or {}).items():
        yid = (ch or {}).get("youtube_channel_id")
        feed = ROOT / "feeds" / f"{cid}.xml"
        if not yid or not feed.exists():
            continue
        try:
            req = urllib.request.Request(f"https://www.youtube.com/feeds/videos.xml?channel_id={yid}",
                                         headers={"User-Agent": "Mozilla/5.0"})
            xml = urllib.request.urlopen(req, timeout=30).read().decode("utf-8", "replace")
        except Exception as e:  # noqa: BLE001
            log(f"{cid}: could not read YouTube list ->", e)
            continue
        on_yt = {}
        for entry in re.findall(r"<entry>(.*?)</entry>", xml, re.S):
            vid = re.search(r"<yt:videoId>([^<]+)</yt:videoId>", entry)
            title = re.search(r"<title>([^<]*)</title>", entry)
            if vid and title:
                on_yt[_norm(unescape(title.group(1)))] = vid.group(1)
        ours = feed.read_text(encoding="utf-8")
        for item in re.findall(r"<item>(.*?)</item>", ours, re.S):
            title = re.search(r"<title>([^<]*)</title>", item)
            link = re.search(r"releases/download/([^/<]+)/", item)
            if not (title and link):
                continue
            key = _norm(unescape(title.group(1)))
            if key in on_yt and link.group(1) not in live:
                live[link.group(1)] = {"video": on_yt[key], "ch": cid,
                                       "seen": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ")}
                log(f"{cid}: on YouTube -> {title.group(1)[:60]}")
    if live != before:
        livef.write_text(json.dumps(live, ensure_ascii=False, indent=1), encoding="utf-8")
    log(f"Live check done ({len(live)} known on YouTube)")


# ---------- clock: release on time, even when GitHub's own timer is late ----------
GRACE_MIN = 150   # a posting time missed by more than this is skipped (no burst of late posts)


def _slots_passed(times, now):
    """Posting times (as datetimes) of yesterday and today that are already past, newest first."""
    from datetime import datetime, timedelta, timezone
    out = []
    for day in (now.date(), (now - timedelta(days=1)).date()):
        for t in times:
            h, m = (int(x) for x in str(t).split(":"))
            at = datetime(day.year, day.month, day.day, h, m, tzinfo=timezone.utc)
            if at <= now:
                out.append(at)
    return sorted(out, reverse=True)


def _next_slot(times, now):
    from datetime import datetime, timedelta, timezone
    best = None
    for day in (now.date(), (now + timedelta(days=1)).date()):
        for t in times:
            h, m = (int(x) for x in str(t).split(":"))
            at = datetime(day.year, day.month, day.day, h, m, tzinfo=timezone.utc)
            if at > now and (best is None or at < best):
                best = at
    return best


def tick():
    """Release one waiting video per channel for its latest posting time, only once per posting time.
    Safe to call as often as you like. Prints seconds until the next posting time (any channel)."""
    import yaml
    from datetime import datetime, timedelta, timezone
    cfg = yaml.safe_load((ROOT / "config.yml").read_text(encoding="utf-8")) or {}
    now = datetime.now(timezone.utc)
    statef = ROOT / "feeds" / "posted_slots.json"
    state = json.loads(statef.read_text(encoding="utf-8")) if statef.exists() else {}
    changed = False
    nxt = None
    for cid, ch in (cfg.get("channels") or {}).items():
        times = (ch or {}).get("post_times_utc") or []
        if not times:
            continue
        n = _next_slot(times, now)
        if n and (nxt is None or n < nxt):
            nxt = n
        passed = _slots_passed(times, now)
        if not passed:
            continue
        slot = passed[0]
        key = slot.strftime("%Y-%m-%d %H:%M")
        if state.get(cid) and state[cid] >= key:
            continue
        if now - slot > timedelta(minutes=GRACE_MIN):
            log(f"{cid}: posting time {key} UTC was missed long ago - skipped, video stays in line")
            state[cid] = key
            changed = True
            continue
        pend = ROOT / "feeds" / "pending" / f"{cid}.json"
        items = json.loads(pend.read_text(encoding="utf-8") or "[]") if pend.exists() else []
        per = int(ch.get("posts_per_time", 1))
        for it in items[:per]:
            add_to_feed(it["info"], it["link"])
            log(f"{cid}: posting time {key} UTC -> {it['info'].get('title', '')[:60]}")
        if items:
            pend.write_text(json.dumps(items[per:], ensure_ascii=False, indent=1), encoding="utf-8")
        else:
            log(f"{cid}: posting time {key} UTC - nothing waiting")
        state[cid] = key
        changed = True
    if changed:
        statef.write_text(json.dumps(state, indent=1), encoding="utf-8")
    wait = int((nxt - now).total_seconds()) if nxt else 3600
    print(f"NEXT_WAIT={wait}", flush=True)
    return wait


if __name__ == "__main__":
    if sys.argv[1:] == ["release"]:
        release_due()
    elif sys.argv[1:] == ["check"]:
        check_live()
    elif sys.argv[1:] == ["tick"]:
        tick()
