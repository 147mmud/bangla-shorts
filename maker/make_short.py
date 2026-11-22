#!/usr/bin/env python3
"""
Make ONE vertical YouTube Short (1080x1920) from a Bangla script file.

    python maker/make_short.py scripts/001-sabr.txt

Steps: read script -> Bangla voice-over (OmniVoice, or Edge TTS as backup)
-> background (your images/videos, Pexels stock clips, or a gradient)
-> Bangla subtitles timed to the voice -> output/<name>.mp4 + output/<name>.json
"""
import asyncio
import json
import math
import os
import random
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
import soundfile as sf
import yaml

ROOT = Path(__file__).resolve().parent.parent
W, H, FPS, SR = 1080, 1920, 30, 24000
VOICE_DIR = ROOT / "voice"
IMG_EXT = {".jpg", ".jpeg", ".png", ".webp"}
VID_EXT = {".mp4", ".mov", ".webm", ".mkv"}
AUD_EXT = {".mp3", ".wav", ".m4a", ".ogg"}
# Look presets. A script picks one with "style: cinematic" (default: normal).
# Any value can be overridden in config.yml under a "cinematic:" block.
STYLE_PRESETS = {
    "normal": {},
    "cinematic": {
        "font": "Noto Serif Bengali",
        "font_size": 76,
        "text_color": "#F3E9D2",
        "highlight_color": "#E8C27A",
        "subtitle_position": 70,
        "subtitle_anim": "fade",
        "darken": 0.28,
        "image_seconds": 6,
        "zoom_speed": 0.0006,
        "voice_speed": 0.95,
        "pause_seconds": 0.55,
        "background_colors": ["#1c1408", "#6b4a1e"],
        "letterbox": 150,
        "film_look": True,
    },
}
STYLE_ALIASES = {"সিনেম্যাটিক": "cinematic", "cinema": "cinematic", "সাধারণ": "normal", "নর্মাল": "normal"}

VOICE_SAMPLES = {
    "bn": "আসসালামু আলাইকুম। আজ আমরা এমন একটি কথা শুনব, যা আমাদের হৃদয়কে শান্ত করবে।",
    "en": "Hello friends! Today I have a wonderful little story to share with you.",
}
# Any language OmniVoice knows works. These give each language a good font and a backup voice.
# (code: (font, backup Microsoft voice))
LANGS = {
    "bn": ("Noto Sans Bengali", "bn-BD-PradeepNeural"),
    "en": ("Noto Sans", "en-US-JennyNeural"),
    "hi": ("Noto Sans Devanagari", "hi-IN-MadhurNeural"),
    "ur": ("Noto Naskh Arabic", "ur-PK-AsadNeural"),
    "ar": ("Noto Naskh Arabic", "ar-SA-HamedNeural"),
    "ne": ("Noto Sans Devanagari", "ne-NP-SagarNeural"),
    "ta": ("Noto Sans Tamil", "ta-IN-ValluvarNeural"),
    "te": ("Noto Sans Telugu", "te-IN-MohanNeural"),
    "mr": ("Noto Sans Devanagari", "mr-IN-ManoharNeural"),
    "gu": ("Noto Sans Gujarati", "gu-IN-NiranjanNeural"),
    "pa": ("Noto Sans Gurmukhi", "pa-IN-OjasNeural"),
    "es": ("Noto Sans", "es-ES-AlvaroNeural"),
    "fr": ("Noto Sans", "fr-FR-HenriNeural"),
    "de": ("Noto Sans", "de-DE-ConradNeural"),
    "it": ("Noto Sans", "it-IT-DiegoNeural"),
    "pt": ("Noto Sans", "pt-BR-AntonioNeural"),
    "id": ("Noto Sans", "id-ID-ArdiNeural"),
    "ms": ("Noto Sans", "ms-MY-OsmanNeural"),
    "tr": ("Noto Sans", "tr-TR-AhmetNeural"),
    "ru": ("Noto Sans", "ru-RU-DmitryNeural"),
    "ja": ("Noto Sans CJK JP", "ja-JP-KeitaNeural"),
    "ko": ("Noto Sans CJK KR", "ko-KR-InJoonNeural"),
    "zh": ("Noto Sans CJK SC", "zh-CN-YunxiNeural"),
}
SERIF = {  # cinematic style uses a serif font where one exists
    "Noto Sans Bengali": "Noto Serif Bengali", "Noto Sans": "Noto Serif",
    "Noto Sans Devanagari": "Noto Serif Devanagari", "Noto Sans Tamil": "Noto Serif Tamil",
    "Noto Sans Telugu": "Noto Serif Telugu", "Noto Sans Gujarati": "Noto Serif Gujarati",
    "Noto Sans Gurmukhi": "Noto Serif Gurmukhi", "Noto Sans CJK JP": "Noto Serif CJK JP",
    "Noto Sans CJK KR": "Noto Serif CJK KR", "Noto Sans CJK SC": "Noto Serif CJK SC",
}
NO_SPACE_LANGS = {"ja", "zh"}


def resolve_settings(cfg, meta):
    """config.yml -> channel block -> style preset. Returns (settings, channel_id)."""
    channels = cfg.get("channels") or {}
    cid = re.sub(r"[^a-z0-9_-]", "", str(meta.get("channel", "")).strip().lower())
    ch = channels.get(cid) or {}
    lang = re.sub(r"[^a-z]", "", str(meta.get("language") or ch.get("language") or cfg.get("language") or "bn").lower())[:3] or "bn"
    font, edge = LANGS.get(lang, ("Noto Sans", ""))
    base = {k: v for k, v in cfg.items() if k not in ("channels", "cinematic", "normal")}
    if lang != "bn":  # the top-level config.yml font/voice are for Bangla
        base.pop("font", None)
        base.pop("edge_voice", None)
    out = {"font": font, "edge_voice": edge or "en-US-JennyNeural", **base,
           **{k: v for k, v in ch.items() if k != "name"}}
    if lang != str(ch.get("language", lang)).lower():  # this video overrides the channel language
        out["font"], out["edge_voice"] = font, edge or "en-US-JennyNeural"
    if lang in NO_SPACE_LANGS:
        out.setdefault("subtitle_max_chars", 16)
        out["subtitle_max_chars"] = min(int(out["subtitle_max_chars"]), 16)
    elif lang != "bn" and "subtitle_max_chars" not in ch:
        out["subtitle_max_chars"] = 32
    # colours chosen on the Shorts Studio page (sent with each script)
    hexes = re.findall(r"#?[0-9a-fA-F]{6}", str(meta.get("colors", "")))
    if len(hexes) >= 2:
        out["background_colors"] = ["#" + h.lstrip("#") for h in hexes[:2]]
    hl = re.findall(r"#?[0-9a-fA-F]{6}", str(meta.get("highlight", "")))
    if hl:
        out["highlight_color"] = "#" + hl[0].lstrip("#")
    out["language"] = lang
    out["channel_name"] = meta.get("channel_name") or ch.get("name") or cfg.get("channel_name", "")
    out["_channel"] = cid
    vdir = VOICE_DIR / (cid or "default") / lang
    # keep the Bangla voice made before channels existed
    if lang == "bn" and cid in ("", "nafse") and not (vdir / "channel_voice.wav").exists() \
            and (VOICE_DIR / "channel_voice.wav").exists():
        vdir = VOICE_DIR
    out["_voice_dir"] = str(vdir)

    style = str(meta.get("style") or out.get("default_style", "normal")).strip().lower()
    style = STYLE_ALIASES.get(style, style)
    if style not in STYLE_PRESETS:
        log(f"Unknown style '{style}', using normal")
        style = "normal"
    preset = dict(STYLE_PRESETS[style])
    if "font" in preset:  # cinematic: serif version of this language's font
        preset["font"] = SERIF.get(out["font"], out["font"])
    out = {**out, **preset, **(cfg.get(style) or {}), **(ch.get(style) or {})}
    out["_style"] = style
    return out, cid


def channel_dir(folder, cid):
    """backgrounds/<channel>/ if it exists and has files, else backgrounds/."""
    d = ROOT / folder / cid if cid else None
    if d and d.is_dir() and any(p.is_file() and not p.name.startswith((".", "README")) for p in d.iterdir()):
        return d
    return ROOT / folder


def log(*a):
    print("[shorts]", *a, flush=True)


def run(cmd, cwd=None):
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"Command failed: {' '.join(map(str, cmd))[:300]}\n{r.stderr[-2500:]}")
    return r.stdout


def load_config():
    with open(ROOT / "config.yml", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


# ---------------------------------------------------------------- script ----
def parse_script(path: Path):
    raw = path.read_text(encoding="utf-8-sig").replace("\r\n", "\n").strip()
    meta, body = {}, raw
    lines = raw.split("\n")
    if "---" in [l.strip() for l in lines]:
        i = [l.strip() for l in lines].index("---")
        head = lines[:i]
        if all((not l.strip()) or re.match(r"^\s*[A-Za-z_]+\s*:", l) for l in head):
            for l in head:
                if ":" in l:
                    k, v = l.split(":", 1)
                    meta[k.strip().lower()] = v.strip()
            body = "\n".join(lines[i + 1:]).strip()
    if not body:
        raise ValueError("The script is empty.")
    return meta, body


def split_sentences(text):
    out = []
    for para in re.split(r"\n+", text):
        para = re.sub(r"\s+", " ", para).strip()
        if not para:
            continue
        parts = re.split(r"(?<=[।?!৷.;؟۔])\s+|(?<=[。！？])", para)
        out += [p.strip() for p in parts if p.strip()]
    # glue very short bits to the previous sentence (better rhythm)
    merged = []
    for s in out:
        if merged and len(s) < 4:
            merged[-1] += " " + s
        else:
            merged.append(s)
    return merged


SCENE_RE = re.compile(r"^\s*\[(?:img|image|scene|ছবি|দৃশ্য)\s*:\s*(.+?)\]\s*$", re.I)


def split_scenes(body):
    """Script lines like "[img: a mosque at sunrise]" start a new AI picture.
    Returns (sentences, scene_index_per_sentence, scene_prompts)."""
    prompts, texts, cur = [], [], []
    for line in body.split("\n"):
        m = SCENE_RE.match(line)
        if m:
            if cur or prompts:
                texts.append("\n".join(cur))
            prompts.append(m.group(1).strip())
            cur = []
        else:
            cur.append(line)
    texts.append("\n".join(cur))
    if not prompts:  # no scene markers
        return split_sentences(body), None, []
    if len(texts) > len(prompts):  # text before the first marker joins scene 1
        first = texts.pop(0)
        texts[0] = (first + "\n" + texts[0]).strip()
    sentences, idx = [], []
    for k, t in enumerate(texts):
        ss = split_sentences(t)
        sentences += ss
        idx += [k] * len(ss)
    keep = sorted(set(idx))
    remap = {old: new for new, old in enumerate(keep)}
    return sentences, [remap[i] for i in idx], [prompts[i] for i in keep]


def subtitle_chunks(sentence, max_chars):
    """Split one sentence into short on-screen pieces."""
    if len(sentence) <= max_chars:
        return [sentence]
    pieces = [p.strip() for p in re.split(r"(?<=[,،;:—–-])\s+|(?<=[，、])", sentence) if p.strip()]
    chunks = []
    for p in pieces:
        if " " not in p and len(p) > max_chars:  # Chinese/Japanese: no spaces, cut by length
            chunks += [p[i:i + max_chars] for i in range(0, len(p), max_chars)]
            continue
        words, cur = p.split(" "), ""
        for w in words:
            if cur and len(cur) + 1 + len(w) > max_chars:
                chunks.append(cur)
                cur = w
            else:
                cur = (cur + " " + w).strip()
        if cur:
            if chunks and len(chunks[-1]) + 1 + len(cur) <= max_chars and len(cur) < max_chars // 3:
                chunks[-1] += " " + cur
            else:
                chunks.append(cur)
    return chunks


# ----------------------------------------------------------------- voice ----
class OmniVoiceTTS:
    """Free open-source OmniVoice (k2-fsa). Runs on CPU on GitHub's servers."""

    def __init__(self, cfg, tmp: Path):
        import torch
        from omnivoice import OmniVoice

        torch.set_num_threads(os.cpu_count() or 2)
        gpu = torch.cuda.is_available()
        log("Loading OmniVoice on", "GPU" if gpu else "CPU", "(first run downloads the model, a few minutes)")
        self.model = OmniVoice.from_pretrained(
            "k2-fsa/OmniVoice",
            device_map="cuda:0" if gpu else "cpu",
            dtype=torch.float16 if gpu else torch.float32,
            asr_device="cuda:0" if gpu else "cpu",
        )
        self.torch = torch
        self.steps = int(cfg.get("quality_steps", 32))
        self.speed = float(cfg.get("voice_speed", 1.0))
        self.new_voice_files = []
        self.prompt = self._voice_prompt(cfg)

    def _voice_prompt(self, cfg):
        """Same voice in every video: your own recording, or a saved 'channel voice'."""
        vdir = Path(cfg.get("_voice_dir", VOICE_DIR))
        self.lang = cfg.get("language", "bn")
        sample_text = VOICE_SAMPLES.get(self.lang) or cfg.get("_first_sentence") or VOICE_SAMPLES["en"]
        # your own recording: voice/<channel>/<language>/, then voice/<channel>/, then voice/ (Bangla only)
        look = [vdir, vdir.parent] + ([VOICE_DIR] if self.lang == "bn" else [])
        own = None
        for d in look:
            if d.is_dir():
                own = next((p for p in d.glob("my_voice.*") if p.suffix.lower() in AUD_EXT), None)
                if own:
                    break
        if own:
            txt = own.with_suffix(".txt")
            ref_text = txt.read_text(encoding="utf-8").strip() if txt.exists() else None
            if ref_text is None:
                log("voice/my_voice.txt not found - transcribing your voice sample automatically")
                self.model.load_asr_model()
            log("Cloning your voice from", own.name)
            return self.model.create_voice_clone_prompt(ref_audio=str(own), ref_text=ref_text)

        saved, saved_txt = vdir / "channel_voice.wav", vdir / "channel_voice.txt"
        style = str(cfg.get("voice_style", "male, middle-aged, low pitch")).strip()
        style_file = vdir / "channel_voice_style.txt"
        if saved.exists() and saved_txt.exists() and (
            not style_file.exists() or style_file.read_text(encoding="utf-8").strip() == style
        ):
            log(f"Using saved channel voice ({saved.relative_to(ROOT)})")
            return self.model.create_voice_clone_prompt(
                ref_audio=str(saved), ref_text=saved_txt.read_text(encoding="utf-8").strip()
            )

        log(f"Designing a new channel voice: '{style}'")
        audio = self.model.generate(
            text=sample_text, language=self.lang, instruct=style or None, num_step=self.steps
        )[0]
        vdir.mkdir(parents=True, exist_ok=True)
        sf.write(saved, audio, SR)
        saved_txt.write_text(sample_text, encoding="utf-8")
        style_file.write_text(style, encoding="utf-8")
        self.new_voice_files = [saved, saved_txt, style_file]
        return self.model.create_voice_clone_prompt(ref_audio=str(saved), ref_text=sample_text)

    def say(self, text):
        audio = self.model.generate(
            text=text, language=self.lang, voice_clone_prompt=self.prompt,
            num_step=self.steps, speed=self.speed,
        )[0]
        return np.asarray(audio, dtype=np.float32)


class EdgeTTS:
    """Microsoft Edge online voices (free, very fast). Backup engine."""

    def __init__(self, cfg, tmp: Path):
        import edge_tts  # noqa: F401
        self.voice = cfg.get("edge_voice", "bn-BD-PradeepNeural")
        pct = round((float(cfg.get("voice_speed", 1.0)) - 1) * 100)
        self.rate = f"{pct:+d}%"
        self.tmp, self.n, self.new_voice_files = tmp, 0, []
        log("Using Edge TTS voice", self.voice)

    def say(self, text):
        import edge_tts
        self.n += 1
        mp3, wav = self.tmp / f"edge{self.n}.mp3", self.tmp / f"edge{self.n}.wav"
        asyncio.run(edge_tts.Communicate(text, self.voice, rate=self.rate).save(str(mp3)))
        run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(mp3), "-ac", "1", "-ar", str(SR), str(wav)])
        a, _ = sf.read(wav, dtype="float32")
        return a


class SilentTTS:
    """Test engine only: silence sized like speech (no internet/model needed)."""

    def __init__(self, cfg, tmp):
        self.new_voice_files = []

    def say(self, text):
        return np.zeros(int(SR * max(1.2, len(text) * 0.075)), dtype=np.float32)


def make_tts(cfg, tmp):
    engine = str(cfg.get("voice_engine", "omnivoice")).lower()
    order = {"omnivoice": [OmniVoiceTTS, EdgeTTS], "edge": [EdgeTTS, OmniVoiceTTS], "silent": [SilentTTS]}
    errors = []
    for cls in order.get(engine, order["omnivoice"]):
        try:
            return cls(cfg, tmp)
        except Exception as e:  # try the next engine
            errors.append(f"{cls.__name__}: {e}")
            log("Voice engine failed, trying the next one ->", e)
    raise RuntimeError("No voice engine worked:\n" + "\n".join(errors))


def build_voice(tts, sentences, cfg, max_chars):
    """Speak sentence by sentence -> one track + exact subtitle timings."""
    gap = float(cfg.get("pause_seconds", 0.35))
    track = [np.zeros(int(SR * 0.3), dtype=np.float32)]
    t, cues, spans = 0.3, [], []
    for i, s in enumerate(sentences, 1):
        log(f"Voice {i}/{len(sentences)}: {s[:50]}")
        a = tts.say(s)
        dur = len(a) / SR
        chunks = subtitle_chunks(s, max_chars)
        total = sum(len(c) for c in chunks)
        ct = t
        for c in chunks:
            d = dur * len(c) / total
            cues.append((ct, ct + d, c))
            ct += d
        track += [a, np.zeros(int(SR * gap), dtype=np.float32)]
        spans.append((t, t + dur))
        t += dur + gap
    track.append(np.zeros(int(SR * 0.6), dtype=np.float32))
    audio = np.concatenate(track)
    peak = float(np.max(np.abs(audio))) or 1.0
    audio = audio * min(1.0, 0.95 / peak)
    build_voice.spans = spans
    return audio, cues


# ------------------------------------------------------------- subtitles ----
def ass_time(t):
    cs = int(round(t * 100))
    return f"{cs // 360000}:{cs // 6000 % 60:02d}:{cs // 100 % 60:02d}.{cs % 100:02d}"


def hex_to_ass(c, alpha="00"):
    c = c.lstrip("#")
    return f"&H{alpha}{c[4:6]}{c[2:4]}{c[0:2]}".upper()


def write_ass(path, cues, cfg, title_text):
    fs = int(cfg.get("font_size", 82))
    col = hex_to_ass(cfg.get("text_color", "#FFFFFF"))
    hl = hex_to_ass(cfg.get("highlight_color", "#FFD75E"))
    pos_y = int(cfg.get("subtitle_position", 62))  # % from top
    margin_v = int(H * (100 - pos_y) / 100)
    font = cfg.get("font", "Noto Sans Bengali")
    enc = -1 if cfg.get("language") in ("ar", "ur", "fa", "he", "ps", "sd") else 1  # -1 = right-to-left aware
    lines = [
        "[Script Info]", "ScriptType: v4.00+", f"PlayResX: {W}", f"PlayResY: {H}",
        "WrapStyle: 0", "ScaledBorderAndShadow: yes", "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
        "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, "
        "Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        f"Style: Sub,{font},{fs},{col},{col},&H00000000,&H96000000,1,0,0,0,100,100,0,0,1,6,3,2,90,90,{margin_v},{enc}",
        f"Style: Top,{font},{int(fs*0.75)},{hl},{hl},&H00000000,&H96000000,1,0,0,0,100,100,0,0,1,4,2,8,80,80,150,{enc}",
        "", "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    end_all = cues[-1][1] + 0.6 if cues else 5
    if title_text:
        lines.append(f"Dialogue: 0,{ass_time(0)},{ass_time(end_all)},Top,,0,0,0,,{title_text}")
    if cfg.get("subtitle_anim") == "fade":
        pop = r"{\fad(250,200)}"
    else:
        pop = r"{\fad(80,60)\fscx88\fscy88\t(0,140,\fscx100\fscy100)}"
    rtl = cfg.get("language") in ("ar", "ur", "fa", "he", "ps", "sd")
    for a, b, text in cues:
        text = text.replace("{", "(").replace("}", ")")
        if rtl:
            text = "\u200f" + text + "\u200f"
        lines.append(f"Dialogue: 1,{ass_time(a)},{ass_time(b)},Sub,,0,0,0,,{pop}{text}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


# ------------------------------------------------------------ background ----
def pexels_clips(query, n, tmp):
    key = os.environ.get("PEXELS_API_KEY", "").strip()
    if not (query and key):
        return []
    import requests
    try:
        r = requests.get(
            "https://api.pexels.com/videos/search",
            params={"query": query, "orientation": "portrait", "per_page": 30, "size": "medium"},
            headers={"Authorization": key}, timeout=30,
        )
        r.raise_for_status()
        vids = r.json().get("videos", [])
        random.shuffle(vids)
        out = []
        for v in vids[:n]:
            files = [f for f in v.get("video_files", []) if f.get("height", 0) >= 1280 and f.get("width", 0) < f.get("height", 0)]
            if not files:
                continue
            f = min(files, key=lambda f: f["height"])
            p = tmp / f"pexels{len(out)}.mp4"
            with requests.get(f["link"], stream=True, timeout=120) as d:
                d.raise_for_status()
                with open(p, "wb") as fh:
                    for chunk in d.iter_content(1 << 20):
                        fh.write(chunk)
            out.append(p)
        log(f"Downloaded {len(out)} Pexels clips for '{query}'")
        return out
    except Exception as e:
        log("Pexels download skipped:", e)
        return []


def make_background(duration, cfg, tmp, query, cid=""):
    seg = float(cfg.get("image_seconds", 5))
    n = max(1, math.ceil(duration / seg))
    bg_dir = channel_dir("backgrounds", cid)
    files = sorted(p for p in bg_dir.iterdir() if p.suffix.lower() in IMG_EXT | VID_EXT) if bg_dir.is_dir() else []
    files += pexels_clips(query, min(n, 8), tmp)
    out = tmp / "bg.mp4"
    enc = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p", "-r", str(FPS), "-an"]
    if not files:
        c0, c1 = (cfg.get("background_colors") or ["#0b1d3a", "#1e4f8a"])[:2]
        log("No background images - using a moving gradient")
        run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i",
             f"gradients=s={W}x{H}:c0=0x{c0.lstrip('#')}:c1=0x{c1.lstrip('#')}:x0=0:y0=0:x1={W}:y1={H}:speed=0.012:r={FPS}",
             "-t", f"{duration:.2f}", *enc, str(out)])
        return out
    random.shuffle(files)
    clips, frames = [], int(seg * FPS)
    for i in range(n):
        src = files[i % len(files)]
        clip = tmp / f"seg{i:03d}.mp4"
        cover = f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H}"
        if src.suffix.lower() in IMG_EXT:
            zin = i % 2 == 0  # alternate zoom in / zoom out
            zs = float(cfg.get("zoom_speed", 0.0011))
            z = f"min(1.0+{zs}*on,1.2)" if zin else f"max(1.2-{zs}*on,1.0)"
            vf = (f"scale={W*2}:{H*2}:force_original_aspect_ratio=increase,crop={W*2}:{H*2},"
                  f"zoompan=z='{z}':d={frames}:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s={W}x{H}:fps={FPS}")
            run(["ffmpeg", "-y", "-loglevel", "error", "-loop", "1", "-i", str(src), "-vf", vf,
                 "-frames:v", str(frames), *enc, str(clip)])
        else:
            run(["ffmpeg", "-y", "-loglevel", "error", "-stream_loop", "-1", "-i", str(src),
                 "-vf", f"{cover},fps={FPS}", "-t", f"{seg:.2f}", *enc, str(clip)])
        clips.append(clip)
    (tmp / "list.txt").write_text("".join(f"file '{c.name}'\n" for c in clips))
    run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", "list.txt",
         "-c", "copy", "bg.mp4"], cwd=tmp)
    return out


def make_ai_background(duration, sentences, scene_idx, prompts, cfg, tmp, cid, query):
    """One AI picture per scene, each shown (with slow zoom) while its sentences are spoken."""
    import ai_tools
    spans = getattr(build_voice, "spans", [])
    style = ""
    if not prompts:
        n = max(3, min(8, round(duration / 6)))
        try:
            groups, prompts, style = ai_tools.scene_prompts(sentences, cfg, n)
        except Exception as e:  # noqa: BLE001
            log("Could not write picture ideas, using normal background ->", e)
            return make_background(duration, cfg, tmp, query, cid)
        scene_idx, k = [], 0
        for gi, g in enumerate(groups):
            scene_idx += [gi] * len(g)
        scene_idx = (scene_idx + [len(groups) - 1] * len(sentences))[:len(sentences)]
    if cfg.get("_style") == "cinematic":
        style = (style + ", cinematic film still, dramatic warm light, shallow depth of field").strip(", ")
    elif cfg.get("ai_image_style"):
        style = (style + ", " + cfg["ai_image_style"]).strip(", ")

    # start time of each scene = start of its first sentence
    starts = {}
    for i, k in enumerate(scene_idx):
        if k not in starts and i < len(spans):
            starts[k] = spans[i][0]
    order = sorted(starts)
    bounds = []
    for j, k in enumerate(order):
        a = 0.0 if j == 0 else starts[k]
        b = starts[order[j + 1]] if j + 1 < len(order) else duration
        bounds.append((k, a, b))

    seed = abs(hash(sentences[0])) % 100000 if sentences else 1
    images = {}
    for k, a, b in bounds:
        log(f"AI picture {k + 1}/{len(bounds)}: {prompts[k][:70]}")
        img = ai_tools.ai_image(prompts[k], tmp / f"ai{k:02d}.jpg", seed=seed + k, style=style)
        if img:
            images[k] = img
    if not images:
        log("No AI pictures could be made, using normal background")
        return make_background(duration, cfg, tmp, query, cid)

    enc = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p", "-r", str(FPS), "-an"]
    clips, last_ok = [], next(iter(images.values()))
    for j, (k, a, b) in enumerate(bounds):
        src = images.get(k, last_ok)
        last_ok = src
        frames = max(2, int(round((b - a) * FPS)))
        zs = min(0.0015, 0.18 / frames)
        z = f"min(1.0+{zs:.6f}*on,1.2)" if j % 2 == 0 else f"max(1.2-{zs:.6f}*on,1.0)"
        vf = (f"scale={W*2}:{H*2}:force_original_aspect_ratio=increase,crop={W*2}:{H*2},"
              f"zoompan=z='{z}':d={frames}:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s={W}x{H}:fps={FPS}")
        clip = tmp / f"aiseg{j:03d}.mp4"
        run(["ffmpeg", "-y", "-loglevel", "error", "-loop", "1", "-i", str(src), "-vf", vf,
             "-frames:v", str(frames), *enc, str(clip)])
        clips.append(clip)
    (tmp / "ailist.txt").write_text("".join(f"file '{c.name}'\n" for c in clips))
    run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", "ailist.txt",
         "-c", "copy", "bg.mp4"], cwd=tmp)
    return tmp / "bg.mp4"


# ------------------------------------------------------------------ main ----
def make_short(script_path: Path, cfg=None, out_dir: Path = None):
    cfg = cfg or load_config()
    out_dir = out_dir or ROOT / "output"
    out_dir.mkdir(exist_ok=True)
    meta, body = parse_script(script_path)
    sentences, scene_idx, scene_list = split_scenes(body)
    name = script_path.stem
    cfg, cid = resolve_settings(cfg, meta)
    cfg["_first_sentence"] = sentences[0] if sentences else ""
    style = cfg["_style"]
    log(f"=== {name}: {len(sentences)} sentences | channel: {cid or 'default'} "
        f"({cfg['channel_name']}) | language: {cfg['language']} | style: {style} ===")

    tmp = Path(tempfile.mkdtemp(prefix="short_"))
    try:
        tts = make_tts({**cfg, **({"voice_style": meta["voice"]} if meta.get("voice") else {})}, tmp)
        max_chars = int(cfg.get("subtitle_max_chars", 40))
        try:
            audio, cues = build_voice(tts, sentences, cfg, max_chars)
        except Exception as e:
            if not isinstance(tts, OmniVoiceTTS):
                raise
            log("OmniVoice failed while speaking, switching to Edge TTS ->", e)
            tts = EdgeTTS(cfg, tmp)
            audio, cues = build_voice(tts, sentences, cfg, max_chars)
        duration = len(audio) / SR
        sf.write(tmp / "voice.wav", audio, SR)
        log(f"Voice length: {duration:.1f}s")
        if duration > 180:
            log("WARNING: longer than 3 minutes - YouTube will not treat this as a Short")

        top = meta.get("top_text", cfg.get("channel_name", "")) or ""
        top = top.replace("{", "(").replace("}", ")")
        write_ass(tmp / "subs.ass", cues, cfg, top)
        query = meta.get("background", cfg.get("pexels_query", ""))
        visuals = str(meta.get("visuals") or cfg.get("visuals", "")).lower()
        if visuals in ("ai", "ai_images", "ai-images") or scene_list:
            cfg["about"] = meta.get("about", cfg.get("about", ""))
            bg = make_ai_background(duration, sentences, scene_idx, scene_list, cfg, tmp, cid, query)
        else:
            bg = make_background(duration, cfg, tmp, query, cid)

        mdir = channel_dir("music", cid)
        music = sorted(p for p in mdir.iterdir() if p.suffix.lower() in AUD_EXT) if mdir.is_dir() else []
        vol = float(cfg.get("background_music_volume", 0.1))
        cmd = ["ffmpeg", "-y", "-loglevel", "error", "-i", bg.name, "-i", "voice.wav"]
        dark = float(cfg.get("darken", 0.35))
        k = max(0.0, min(1.0, 1 - dark))
        look = ""
        if cfg.get("film_look"):
            look = ("eq=contrast=1.08:saturation=0.82,colorbalance=rs=0.06:gs=0.01:bs=-0.06:rm=0.03:bm=-0.03,"
                    "vignette=angle=PI/4.2,noise=alls=7:allf=t,")
        bars = int(cfg.get("letterbox", 0) or 0)
        if bars:
            look += (f"drawbox=x=0:y=0:w=iw:h={bars}:color=black:t=fill,"
                     f"drawbox=x=0:y=ih-{bars}:w=iw:h={bars}:color=black:t=fill,")
        fade = (f",fade=t=in:st=0:d=0.8,fade=t=out:st={max(0, duration-1.0):.2f}:d=1.0"
                if cfg.get("film_look") else "")
        vf = (f"[0:v]format=rgb24,colorchannelmixer=rr={k}:gg={k}:bb={k},{look}"
              f"ass=subs.ass:fontsdir={ROOT/'fonts'}{fade},format=yuv420p[v]")
        if music and vol > 0:
            cmd += ["-stream_loop", "-1", "-i", str(random.choice(music))]
            af = (f"[2:a]volume={vol},afade=t=out:st={max(0, duration-1.5):.2f}:d=1.5[m];"
                  f"[1:a][m]amix=inputs=2:duration=first:normalize=0[a]")
        else:
            af = "[1:a]anull[a]"
        final = out_dir / f"{name}.mp4"
        cmd += ["-filter_complex", f"{vf};{af}", "-map", "[v]", "-map", "[a]", "-t", f"{duration:.2f}",
                "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p", "-r", str(FPS),
                "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-movflags", "+faststart", str(final.resolve())]
        log("Rendering final video...")
        run(cmd, cwd=tmp)

        first = sentences[0]
        title = meta.get("title") or (first[:80] + " #shorts")
        tags = [t.strip() for t in (meta.get("tags", "") or "").split(",") if t.strip()]
        info = {
            "title": title[:100],
            "description": (meta.get("description", "") + "\n\n" + cfg.get("default_description", "")).strip(),
            "tags": tags or list(cfg.get("default_tags", [])),
            "seconds": round(duration, 1),
            "script": script_path.name,
            "style": style,
            "channel": cid,
        }
        (out_dir / f"{name}.json").write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")
        log(f"DONE -> {final}  ({duration:.1f}s)")
        return final, info, getattr(tts, "new_voice_files", [])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit("usage: python maker/make_short.py scripts/your-script.txt")
    make_short(Path(sys.argv[1]))
