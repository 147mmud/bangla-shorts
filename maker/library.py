#!/usr/bin/env python3
"""Your own picture library for a channel:  library/<channel>/index.json + the pictures.

For every scene of a new short the best-fitting picture is taken from the library (matched by what
is in the picture vs. what is being said). A scene with no fitting picture gets an AI picture as before.

Every picture is used once before any picture is used a second time:
  library/<channel>/used.json    which picture was used, when, in which video, and in which round
  library/<channel>/status.json  how many pictures are still unused in this round (Shorts Studio and the
                                 2-hourly Claude task read this; at 10 or fewer left you get a notification)
When all pictures are used, a new round starts with the same pictures (least recently used first),
unless you added new ones - new pictures are always used first.
"""
import json
import re
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WARN_AT = 10       # notify when this many unused pictures are left
RECENT_VIDEOS = 3  # never re-use a picture that was in one of the last few videos
# scripts sent before the library started (name time, Bangladesh time) keep the old way: AI pictures only
LIBRARY_FROM = "20261127-171500"


def log(*a):
    print("[library]", *a, flush=True)


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _load_json(p, default):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return default


def load(cid):
    """-> (folder, items) or (None, []) when this channel has no library."""
    d = ROOT / "library" / (cid or "")
    if not cid or not (d / "index.json").is_file():
        return None, []
    items = [it for it in _load_json(d / "index.json", {}).get("items", [])
             if isinstance(it, dict) and it.get("file") and (d / it["file"]).is_file()]
    return (d, items) if items else (None, [])


def _state(d):
    st = _load_json(d / "used.json", {})
    st.setdefault("round", 1)
    st.setdefault("used", {})      # file -> {"round": r, "at": iso, "video": name}
    st.setdefault("history", [])   # [[video, [files]], ...] newest last
    return st


def _is_cta(it):
    """Subscribe / ending pictures (\"cta\": true in index.json): may go in every video, last scene only."""
    return bool(it.get("cta"))


def write_status(d, items, st):
    files = [it["file"] for it in items if not _is_cta(it)]
    left = sum(1 for f in files if st["used"].get(f, {}).get("round", 0) < st["round"])
    status = {"channel": d.name, "total": len(files), "left": left, "round": st["round"],
              "warn_at": WARN_AT, "warn": left <= WARN_AT, "updated": _now()}
    (d / "status.json").write_text(json.dumps(status, ensure_ascii=False, indent=1), encoding="utf-8")
    return status


def _ask_ai(scenes, cands, cfg):
    import ai_tools
    lines_s = "\n".join(f"{i + 1}. said: {s['text'][:300]}" + (f" | scene note: {s['note'][:200]}" if s.get("note") else "")
                        for i, s in enumerate(scenes))
    lines_c = "\n".join(f"{it['file'][:-4]}: {it.get('en') or it.get('bn')}" + (" (used before)" if it.get("_old") else "")
                        + (" [SUBSCRIBE picture: use it only for the LAST scene, and only when that scene asks viewers "
                           "to subscribe/share/like/say Ameen]" if _is_cta(it) else "")
                        for it in cands)
    data = ai_tools.llm([
        {"role": "system", "content": "You choose background pictures for a YouTube Short. Reply with JSON only."},
        {"role": "user", "content":
            f"Channel: {cfg.get('channel_name', '')}. {cfg.get('about', '')}\n"
            "Below are the scenes of one short (what the narrator says, and sometimes a note about the wanted picture), "
            "then a list of available pictures (id: what is in the picture).\n"
            "For each scene choose the ONE picture that fits best what is said and its mood "
            "(e.g. dua -> raised hands; Quran -> Quran; night worship -> lantern/night; patience/hope -> sunrise, path). "
            "Follow the scene note when there is one. Use each picture id at most once. Neighbouring scenes should look "
            "different. Prefer pictures NOT marked (used before) when they fit about as well. "
            "If no picture fits a scene reasonably, use null for it.\n"
            f'Return JSON: {{"pick": [id or null, ...]}} with exactly {len(scenes)} items.\n\n'
            f"SCENES:\n{lines_s}\n\nPICTURES:\n{lines_c}"}],
        want_json=True, temperature=0.2)
    out = []
    for x in (data.get("pick") or [])[:len(scenes)]:
        x = re.sub(r"\D", "", str(x or ""))
        out.append(x.zfill(3) + ".jpg" if x else None)
    return out + [None] * (len(scenes) - len(out))


def pick(cid, scene_texts, scene_notes, cfg, video, skip=()):
    """scene_texts[k] = narration of scene k, scene_notes[k] = picture note (may be '').
    skip = scene numbers that already have a picture (yours).
    -> list (one per scene) of Path or None.  Records the use in used.json / status.json."""
    d, items = load(cid)
    n = len(scene_texts)
    if not d or not n or cfg.get("use_library") is False:
        return [None] * n
    m = re.search(r"(\d{8})-(\d{6})", str(video or ""))
    if m and f"{m.group(1)}-{m.group(2)}" < LIBRARY_FROM:
        log("Script was sent before the picture library started - AI pictures as before")
        return [None] * n
    st = _state(d)
    ctas = [it for it in items if _is_cta(it)]
    items = [it for it in items if not _is_cta(it)]
    recent = {f for _, fs in st["history"][-RECENT_VIDEOS:] for f in fs}
    fresh = [it for it in items if st["used"].get(it["file"], {}).get("round", 0) < st["round"] and it["file"] not in recent]
    want = [k for k in range(n) if k not in skip]
    cands = list(fresh)
    if len(fresh) < max(len(want) * 3, 12):  # round almost over: add the least recently used old ones
        old = sorted((it for it in items if it not in fresh and it["file"] not in recent),
                     key=lambda it: st["used"].get(it["file"], {}).get("at", ""))
        cands += [{**it, "_old": True} for it in old[:max(len(want) * 3, 12) - len(fresh)]]
    if want and want[-1] == n - 1:
        cands += ctas  # subscribe pictures: always offered (last scene only), never "used up"
    if not want or not cands:
        return [None] * n
    scenes = [{"text": scene_texts[k], "note": (scene_notes[k] if k < len(scene_notes) else "") or ""} for k in want]
    try:
        chosen = _ask_ai(scenes, cands, cfg)
    except Exception as e:  # noqa: BLE001
        log("Could not match library pictures, AI pictures will be made ->", str(e)[:150])
        return [None] * n
    allowed = {it["file"] for it in cands}
    # a last few unused pictures that never fit any script would keep the round open for ever:
    # after being passed over 5 times near the end of a round they count as used
    if len(fresh) <= WARN_AT:
        st.setdefault("passed", {})
        for it in fresh:
            if it["file"] not in chosen:
                st["passed"][it["file"]] = st["passed"].get(it["file"], 0) + 1
                if st["passed"][it["file"]] >= 5:
                    st["used"][it["file"]] = {"round": st["round"], "at": _now(), "video": "(skipped - did not fit)"}
                    st["passed"].pop(it["file"], None)
    out, taken = [None] * n, set()
    cta_files = {it["file"] for it in ctas}
    for k, f in zip(want, chosen):
        if f in cta_files and k != n - 1:
            continue  # a subscribe picture only goes at the end
        if f and f in allowed and f not in taken:
            out[k] = d / f
            taken.add(f)
    cta_used = taken & cta_files
    taken -= cta_files
    if cta_used:
        log("Subscribe picture for the last scene: " + ", ".join(sorted(cta_used)))
    if not taken:
        if not cta_used:
            log("No library picture fits this script")
        (d / "used.json").write_text(json.dumps(st, ensure_ascii=False, indent=1), encoding="utf-8")
        write_status(d, items, st)
        return out
    s = _mark(d, items, st, taken, video)
    log(f"{len(taken)} picture(s) from your library: " + ", ".join(sorted(taken)) +
        f" | {s['left']} of {s['total']} still unused in this round")
    return out


def _mark(d, items, st, taken, video):
    """Remember that these pictures were used; start a new round when every picture has been used."""
    for f in taken:
        r = st["round"] if st["used"].get(f, {}).get("round", 0) < st["round"] else st["round"] + 1
        st["used"][f] = {"round": r, "at": _now(), "video": video}
    if all(st["used"].get(it["file"], {}).get("round", 0) >= st["round"] for it in items):
        st["round"] += 1
        st["passed"] = {}
        log(f"All {len(items)} library pictures have been used - round {st['round']} starts")
    st["history"] = (st["history"] + [[video, sorted(taken)]])[-20:]
    (d / "used.json").write_text(json.dumps(st, ensure_ascii=False, indent=1), encoding="utf-8")
    return write_status(d, items, st)


def record(cid, paths, video):
    """Pictures you chose yourself from the library (on the Shorts Studio page) count as used too."""
    d, items = load(cid)
    if not d:
        return
    names = {it["file"] for it in items if not _is_cta(it)}
    items = [it for it in items if not _is_cta(it)]
    taken = sorted({Path(p).name for p in paths if Path(p).parent.resolve() == d.resolve() and Path(p).name in names})
    if taken:
        s = _mark(d, items, _state(d), taken, video)
        log(f"{len(taken)} library picture(s) you chose: " + ", ".join(taken) + f" | {s['left']} of {s['total']} still unused")


def scene_texts_from(sentences, scene_idx, n):
    groups = [[] for _ in range(n)]
    for i, s in enumerate(sentences):
        k = scene_idx[i] if scene_idx and i < len(scene_idx) else min(n - 1, int(i * n / max(1, len(sentences))))
        groups[min(max(k, 0), n - 1)].append(s)
    return [" ".join(g) for g in groups]


if __name__ == "__main__":  # python maker/library.py nafse  -> show the status
    import sys
    d, items = load(sys.argv[1] if len(sys.argv) > 1 else "nafse")
    print(write_status(d, items, _state(d)) if d else "no library")
