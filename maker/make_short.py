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
VOICE_SAMPLE = "আসসালামু আলাইকুম। আজ আমরা এমন একটি কথা শুনব, যা আমাদের হৃদয়কে শান্ত করবে।"


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
        parts = re.split(r"(?<=[।?!৷.;])\s+", para)
        out += [p.strip() for p in parts if p.strip()]
    # glue very short bits to the previous sentence (better rhythm)
    merged = []
    for s in out:
        if merged and len(s) < 8:
            merged[-1] += " " + s
        else:
            merged.append(s)
    return merged


def subtitle_chunks(sentence, max_chars):
    """Split one sentence into short on-screen pieces."""
    if len(sentence) <= max_chars:
        return [sentence]
    pieces = [p.strip() for p in re.split(r"(?<=[,،;:—–-])\s+", sentence) if p.strip()]
    chunks = []
    for p in pieces:
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
        own = next((p for p in VOICE_DIR.glob("my_voice.*") if p.suffix.lower() in AUD_EXT), None)
        if own:
            txt = VOICE_DIR / "my_voice.txt"
            ref_text = txt.read_text(encoding="utf-8").strip() if txt.exists() else None
            if ref_text is None:
                log("voice/my_voice.txt not found - transcribing your voice sample automatically")
                self.model.load_asr_model()
            log("Cloning your voice from", own.name)
            return self.model.create_voice_clone_prompt(ref_audio=str(own), ref_text=ref_text)

        saved, saved_txt = VOICE_DIR / "channel_voice.wav", VOICE_DIR / "channel_voice.txt"
        style = str(cfg.get("voice_style", "male, middle-aged, low pitch")).strip()
        style_file = VOICE_DIR / "channel_voice_style.txt"
        if saved.exists() and saved_txt.exists() and (
            not style_file.exists() or style_file.read_text(encoding="utf-8").strip() == style
        ):
            log("Using saved channel voice (voice/channel_voice.wav)")
            return self.model.create_voice_clone_prompt(
                ref_audio=str(saved), ref_text=saved_txt.read_text(encoding="utf-8").strip()
            )

        log(f"Designing a new channel voice: '{style}'")
        audio = self.model.generate(
            text=VOICE_SAMPLE, language="bn", instruct=style or None, num_step=self.steps
        )[0]
        VOICE_DIR.mkdir(exist_ok=True)
        sf.write(saved, audio, SR)
        saved_txt.write_text(VOICE_SAMPLE, encoding="utf-8")
        style_file.write_text(style, encoding="utf-8")
        self.new_voice_files = [saved, saved_txt, style_file]
        return self.model.create_voice_clone_prompt(ref_audio=str(saved), ref_text=VOICE_SAMPLE)

    def say(self, text):
        audio = self.model.generate(
            text=text, language="bn", voice_clone_prompt=self.prompt,
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
    t, cues = 0.3, []
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
        t += dur + gap
    track.append(np.zeros(int(SR * 0.6), dtype=np.float32))
    audio = np.concatenate(track)
    peak = float(np.max(np.abs(audio))) or 1.0
    audio = audio * min(1.0, 0.95 / peak)
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
    lines = [
        "[Script Info]", "ScriptType: v4.00+", f"PlayResX: {W}", f"PlayResY: {H}",
        "WrapStyle: 0", "ScaledBorderAndShadow: yes", "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
        "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, "
        "Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        f"Style: Sub,{font},{fs},{col},{col},&H00000000,&H96000000,1,0,0,0,100,100,0,0,1,6,3,2,90,90,{margin_v},1",
        f"Style: Top,{font},{int(fs*0.75)},{hl},{hl},&H00000000,&H96000000,1,0,0,0,100,100,0,0,1,4,2,8,80,80,150,1",
        "", "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    end_all = cues[-1][1] + 0.6 if cues else 5
    if title_text:
        lines.append(f"Dialogue: 0,{ass_time(0)},{ass_time(end_all)},Top,,0,0,0,,{title_text}")
    pop = r"{\fad(80,60)\fscx88\fscy88\t(0,140,\fscx100\fscy100)}"
    for a, b, text in cues:
        text = text.replace("{", "(").replace("}", ")")
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


def make_background(duration, cfg, tmp, query):
    seg = float(cfg.get("image_seconds", 5))
    n = max(1, math.ceil(duration / seg))
    bg_dir = ROOT / "backgrounds"
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
            z = "min(1.0+0.0011*on,1.2)" if zin else "max(1.2-0.0011*on,1.0)"
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


# ------------------------------------------------------------------ main ----
def make_short(script_path: Path, cfg=None, out_dir: Path = None):
    cfg = cfg or load_config()
    out_dir = out_dir or ROOT / "output"
    out_dir.mkdir(exist_ok=True)
    meta, body = parse_script(script_path)
    sentences = split_sentences(body)
    name = script_path.stem
    log(f"=== {name}: {len(sentences)} sentences ===")

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
        write_ass(tmp / "subs.ass", cues, cfg, top)
        bg = make_background(duration, cfg, tmp, meta.get("background", cfg.get("pexels_query", "")))

        mdir = ROOT / "music"
        music = sorted(p for p in mdir.iterdir() if p.suffix.lower() in AUD_EXT) if mdir.is_dir() else []
        vol = float(cfg.get("background_music_volume", 0.1))
        cmd = ["ffmpeg", "-y", "-loglevel", "error", "-i", bg.name, "-i", "voice.wav"]
        dark = float(cfg.get("darken", 0.35))
        k = max(0.0, min(1.0, 1 - dark))
        vf = f"[0:v]format=rgb24,colorchannelmixer=rr={k}:gg={k}:bb={k},ass=subs.ass:fontsdir={ROOT/'fonts'},format=yuv420p[v]"
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
