#!/usr/bin/env python3
"""
REMAKE: turn other Shorts (YouTube links or uploaded video files) into NEW original scripts.

A job file remake/<id>.txt looks like:
    channel: nafse
    language: bn
    style: cinematic
    ---
    https://www.youtube.com/shorts/xxxx
    file: remake/inbox/<id>-2.mp4

For every source: get what it says (subtitles or Whisper) + look at a few frames,
then GitHub Models writes a fresh script on the same idea (new words, own channel style),
with one AI picture per scene -> scripts/<id>-<n>.txt (made into a video by the normal queue).
"""
import base64
import glob
import json
import re
import subprocess
import tempfile
from pathlib import Path

from make_short import ROOT, LANGS, log, parse_script
import ai_tools

LANG_NAMES = {"bn": "Bangla (Bengali script)", "en": "English", "hi": "Hindi", "ur": "Urdu", "ar": "Arabic",
              "ne": "Nepali", "ta": "Tamil", "te": "Telugu", "mr": "Marathi", "gu": "Gujarati", "pa": "Punjabi",
              "es": "Spanish", "fr": "French", "de": "German", "it": "Italian", "pt": "Portuguese",
              "id": "Indonesian", "ms": "Malay", "tr": "Turkish", "ru": "Russian", "ja": "Japanese",
              "ko": "Korean", "zh": "Chinese (Simplified)"}
WORDS = {"bn": "90-115 words", "en": "110-135 words", "ja": "220-280 characters", "zh": "180-230 characters"}
_asr = None


# ------------------------------------------------------------ reading ----
def whisper(audio_path):
    global _asr
    try:
        if _asr is None:
            import torch
            from transformers import pipeline
            log("Loading Whisper (speech-to-text)…")
            _asr = pipeline("automatic-speech-recognition", model="openai/whisper-large-v3-turbo",
                            device="cpu", torch_dtype=torch.float32)
        out = _asr(str(audio_path), chunk_length_s=30, return_timestamps=False,
                   generate_kwargs={"task": "transcribe"})
        return (out.get("text") or "").strip()
    except Exception as e:  # noqa: BLE001
        log("Whisper failed:", e)
        return ""


def frames_b64(video, n=4):
    out = []
    try:
        dur = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0",
                                    str(video)], capture_output=True, text=True).stdout.strip() or 0)
    except ValueError:
        dur = 0
    if dur <= 0:
        return out
    with tempfile.TemporaryDirectory() as td:
        for i in range(n):
            t = dur * (i + 0.5) / n
            f = Path(td) / f"f{i}.jpg"
            subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{t:.2f}", "-i", str(video),
                            "-frames:v", "1", "-vf", "scale=360:-2", "-q:v", "5", str(f)])
            if f.exists():
                out.append(base64.b64encode(f.read_bytes()).decode())
    return out


def audio_of(video, tmp):
    wav = tmp / "audio.wav"
    r = subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(video), "-vn", "-ac", "1", "-ar", "16000",
                        "-t", "240", str(wav)])
    return wav if r.returncode == 0 and wav.exists() else None


def vtt_text(path):
    lines, seen = [], set()
    for l in Path(path).read_text(encoding="utf-8", errors="ignore").splitlines():
        l = re.sub(r"<[^>]+>", "", l).strip()
        if not l or "-->" in l or l.startswith(("WEBVTT", "Kind:", "Language:", "NOTE")) or l.isdigit():
            continue
        if l not in seen:
            seen.add(l)
            lines.append(l)
    return " ".join(lines)


def from_url(url, tmp):
    import yt_dlp
    info, text, video = {}, "", None
    base = {"quiet": True, "no_warnings": True, "noplaylist": True, "outtmpl": str(tmp / "src.%(ext)s"),
            "socket_timeout": 30, "retries": 2}
    try:
        base["js_runtimes"] = {"node": {}}
        yt_dlp.YoutubeDL(base)  # older versions ignore/raise on unknown keys
    except Exception:  # noqa: BLE001
        base.pop("js_runtimes", None)
    # 1) small video (gives frames + audio)
    try:
        with yt_dlp.YoutubeDL({**base, "format": "best[height<=480][ext=mp4]/best[height<=480]/worst",
                               "writesubtitles": True, "writeautomaticsub": True,
                               "subtitleslangs": ["bn", "en", "hi", "ur", "ar", ".*-orig"],
                               "subtitlesformat": "vtt", "ignoreerrors": False}) as y:
            info = y.extract_info(url, download=True) or {}
        vids = [p for p in tmp.glob("src.*") if p.suffix.lower() in (".mp4", ".webm", ".mkv", ".mov")]
        video = vids[0] if vids else None
    except Exception as e:  # noqa: BLE001
        log("Video download blocked/failed:", str(e)[:200])
    # 2) subtitles only (works more often than video)
    if not info:
        try:
            with yt_dlp.YoutubeDL({**base, "skip_download": True, "writesubtitles": True,
                                   "writeautomaticsub": True, "subtitleslangs": ["bn", "en", "hi", "ur", "ar", ".*-orig"],
                                   "subtitlesformat": "vtt"}) as y:
                info = y.extract_info(url, download=True) or {}
        except Exception as e:  # noqa: BLE001
            log("Subtitles/metadata blocked/failed:", str(e)[:200])
    subs = sorted(glob.glob(str(tmp / "src*.vtt")))
    if subs:
        text = vtt_text(subs[0])
    if not text and video:
        wav = audio_of(video, tmp)
        text = whisper(wav) if wav else ""
    return {"title": info.get("title", ""), "description": (info.get("description") or "")[:800],
            "transcript": text[:6000], "frames": frames_b64(video) if video else []}


def from_file(path, tmp):
    wav = audio_of(path, tmp)
    return {"title": "", "description": "", "transcript": (whisper(wav) if wav else "")[:6000],
            "frames": frames_b64(path)}


# ------------------------------------------------------------ writing ----
def write_script(src, meta):
    lang = meta.get("language", "bn")
    lname = LANG_NAMES.get(lang, lang)
    style = meta.get("style", "normal")
    about = meta.get("about", "")
    rules = ai_tools._channel_rules({"about": about, "channel_name": meta.get("channel_name", "")})
    words = WORDS.get(lang, "100-130 words")
    note = meta.get("note", "")
    text_part = (
        f"SOURCE VIDEO (for inspiration only)\nTitle: {src['title'] or '-'}\n"
        f"Description: {src['description'] or '-'}\nWhat is said: {src['transcript'] or '(no speech found)'}\n"
    )
    instructions = f"""You remake short videos for the YouTube channel "{meta.get('channel_name', '')}".
Channel: {about or '-'}
Write a NEW, ORIGINAL YouTube Short on the SAME core idea/lesson as the source video{(' (the pictures show what the video looks like)') if src['frames'] else ''}.

Rules:
- Write everything (title, script, description, tags) in {lname}.
- Use completely new wording, a new hook and your own structure. Never copy sentences or more than 4 words in a row from the source (well-known scripture or proverbs may be quoted accurately).
- Do not mention the source video or its creator.
- Spoken length 40-55 seconds: about {words}. Short, clear spoken sentences, each ending with a full stop / dari (।) / ! / ?.
- First sentence = a strong hook. End with a takeaway{', short dua' if 'islam' in about.lower() or 'ইসলাম' in about else ''} or a question to the viewer.
- Only state religious quotes, hadith or facts you are certain are accurate; never invent references or numbers.
- Tone: {'slow, emotional, cinematic storytelling' if style == 'cinematic' else 'clear, warm and engaging'}.
- Split the script into 4-7 scenes. For each scene give one English image prompt (max 35 words) for an AI image generator: one consistent visual style, vertical picture, no text, no logos, no real or famous people. {rules}
{('- Extra instructions from the channel owner: ' + note) if note else ''}

Return JSON only:
{{"title": "under 70 characters", "description": "1-2 sentences", "tags": ["4-8 tags"],
  "scenes": [{{"image_prompt": "...", "lines": ["sentence", "sentence"]}}]}}"""
    content = [{"type": "text", "text": instructions + "\n\n" + text_part}]
    for f in src["frames"][:4]:
        content.append({"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + f, "detail": "low"}})
    msgs = [{"role": "system", "content": "You are a skilled short-video scriptwriter. Reply with valid JSON only."},
            {"role": "user", "content": content if src["frames"] else content[0]["text"]}]
    try:
        data = ai_tools.llm(msgs, want_json=True)
    except Exception:
        if not src["frames"]:
            raise
        log("Vision request failed, retrying with text only")
        msgs[1]["content"] = content[0]["text"]
        data = ai_tools.llm(msgs, want_json=True)
    scenes = [s for s in data.get("scenes", []) if s.get("lines")]
    if not scenes:
        raise RuntimeError("AI did not return a script")
    return data, scenes


def process_job(job: Path):
    """Returns list of (new_script_path or None, error_or_None, source, name)."""
    meta, body = parse_script(job)
    sources = []
    for line in body.splitlines():
        line = re.sub(r"^\s*\d+[.)]\s*", "", line.strip())
        if not line or line.startswith("#"):
            continue
        if line.lower().startswith("file:"):
            sources.append(("file", line.split(":", 1)[1].strip()))
        elif re.match(r"https?://", line):
            sources.append(("url", line.split()[0]))
    if not sources:
        raise ValueError("No links or files found in the remake job.")
    keep = ["channel", "channel_name", "language", "style", "colors", "highlight", "about"]
    results = []
    for n, (kind, ref) in enumerate(sources, 1):
        name = f"{job.stem}-{n}"
        log(f"--- Remake {n}/{len(sources)}: {ref}")
        try:
            with tempfile.TemporaryDirectory() as td:
                tmp = Path(td)
                if kind == "url":
                    src = from_url(ref, tmp)
                else:
                    p = (ROOT / ref).resolve()
                    if not str(p).startswith(str(ROOT)) or not p.exists():
                        raise FileNotFoundError(f"Uploaded file not found: {ref}")
                    src = from_file(p, tmp)
            if not (src["transcript"] or src["title"] or src["frames"]):
                raise RuntimeError("Could not read this video (YouTube may be blocking GitHub's servers). "
                                   "Download it and upload the file on the Remake page instead.")
            log(f"Read source: title={src['title'][:60]!r}, transcript {len(src['transcript'])} chars, "
                f"{len(src['frames'])} frames")
            data, scenes = write_script(src, meta)
            head = [f"{k}: {meta[k]}" for k in keep if meta.get(k)]
            title = str(data.get("title", "")).replace("\n", " ").strip()
            if title:
                head.insert(0, "title: " + (title if "#shorts" in title.lower() else title + " #shorts"))
            tags = data.get("tags") or []
            if tags:
                head.append("tags: " + ", ".join(str(t).replace(",", " ") for t in tags[:8]))
            if data.get("description"):
                head.append("description: " + str(data["description"]).replace("\n", " ")[:300])
            head.append("visuals: ai")
            lines = []
            for sc in scenes:
                prompt = str(sc.get("image_prompt", "")).replace("[", "(").replace("]", ")").replace("\n", " ")
                lines.append(f"[img: {prompt}]")
                lines += [str(x).strip() for x in sc["lines"] if str(x).strip()]
            out = ROOT / "scripts" / f"{name}.txt"
            out.write_text("\n".join(head) + "\n---\n" + "\n".join(lines) + "\n", encoding="utf-8")
            log(f"New script written: scripts/{out.name} ({sum(len(s['lines']) for s in scenes)} lines, "
                f"{len(scenes)} scenes)")
            results.append((out, None, ref, name))
        except Exception as e:  # noqa: BLE001
            log(f"Remake {n} failed: {e}")
            results.append((None, str(e), ref, name))
        finally:
            if kind == "file":
                f = ROOT / ref
                if f.exists() and str(f.resolve()).startswith(str((ROOT / "remake").resolve())):
                    subprocess.run(["git", "rm", "-q", "--cached", "--ignore-unmatch", str(f)], cwd=ROOT)
                    f.unlink(missing_ok=True)
    return results
