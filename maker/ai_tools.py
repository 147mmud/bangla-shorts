"""
Free AI helpers used on GitHub's servers:
  - llm()       : Google Gemini (free key in the GEMINI_API_KEY secret), with a no-key fallback
  - ai_image()  : Pollinations image generation (free; optional POLLINATIONS_KEY secret = faster, no watermark)
"""
import json
import os
import re
import time
import urllib.parse
from pathlib import Path

import requests


def log(*a):
    print("[ai]", *a, flush=True)

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
# Free-tier Flash models; names change over time, so a few are tried in order (override with GEMINI_MODEL).
GEMINI_MODELS = ["gemini-flash-latest", "gemini-3.6-flash", "gemini-3.5-flash", "gemini-3-flash-preview",
                 "gemini-flash-lite-latest", "gemini-3.5-flash-lite"]
_good_model = [None]


def has_gemini():
    return bool(os.environ.get("GEMINI_API_KEY", "").strip())


def _to_gemini_parts(content):
    """OpenAI-style content (str or list of text/image_url) -> Gemini parts."""
    if isinstance(content, str):
        return [{"text": content}]
    parts = []
    for c in content:
        if c.get("type") == "text":
            parts.append({"text": c["text"]})
        elif c.get("type") == "image_url":
            url = c["image_url"]["url"]
            if url.startswith("data:"):
                mime, data = url[5:].split(";base64,", 1)
                parts.append({"inline_data": {"mime_type": mime, "data": data}})
        elif "inline_data" in c or "file_data" in c:
            parts.append(c)
    return parts


def gemini(parts, system=None, want_json=False, temperature=0.8, max_tokens=4096):
    key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not key:
        raise RuntimeError("GEMINI_API_KEY secret is not set")
    body = {"contents": [{"role": "user", "parts": parts}],
            "generationConfig": {"temperature": temperature, "maxOutputTokens": max_tokens}}
    if system:
        body["systemInstruction"] = {"parts": [{"text": system}]}
    if want_json:
        body["generationConfig"]["responseMimeType"] = "application/json"
    env_model = os.environ.get("GEMINI_MODEL", "").strip()
    models = ([env_model] if env_model else []) + ([_good_model[0]] if _good_model[0] else []) + GEMINI_MODELS
    seen, last = set(), None
    for m in models:
        if m in seen:
            continue
        seen.add(m)
        for attempt in range(3):
            try:
                r = requests.post(GEMINI_URL.format(model=m), json=body, timeout=300,
                                  headers={"x-goog-api-key": key, "Content-Type": "application/json"})
            except requests.RequestException as e:
                last = f"{m}: {e}"
                log(last)
                time.sleep(5)
                continue
            if r.status_code == 429:
                last = f"{m}: rate limited"
                log(last + " - waiting 30s")
                time.sleep(30)
                continue
            if r.status_code in (400, 403) and "API key" in r.text:
                raise RuntimeError("Gemini rejected the API key - check the GEMINI_API_KEY secret. " + r.text[:200])
            if r.status_code >= 400:
                last = f"{m}: HTTP {r.status_code} {r.text[:250]}"
                log(last)
                break  # try the next model
            try:
                cand = r.json()["candidates"][0]
                text = "".join(p.get("text", "") for p in cand["content"]["parts"] if not p.get("thought"))
            except (ValueError, KeyError, IndexError) as e:
                last = f"{m}: empty/blocked reply ({e}) {r.text[:250]}"
                log(last)
                break
            _good_model[0] = m
            if not want_json:
                return text
            mm = re.search(r"\{[\s\S]*\}", text)
            try:
                return json.loads(mm.group(0) if mm else text)
            except ValueError as e:
                last = f"{m}: bad JSON ({e})"
                log(last)
                continue
    raise RuntimeError(f"Gemini failed: {last}")


def _pollinations_text(messages, want_json, temperature):
    """Last-resort free text AI (no key). May be slow or unavailable."""
    flat = []
    for msg in messages:
        c = msg["content"]
        if isinstance(c, list):
            c = "\n".join(x.get("text", "") for x in c if x.get("type") == "text")
        flat.append({"role": msg["role"], "content": c})
    r = requests.post("https://text.pollinations.ai/openai", timeout=240,
                      json={"model": "openai", "messages": flat, "temperature": temperature})
    if r.status_code >= 400 or "json" not in r.headers.get("content-type", ""):
        raise RuntimeError(f"Pollinations text: HTTP {r.status_code} {r.text[:200]}")
    text = r.json()["choices"][0]["message"]["content"]
    if not want_json:
        return text
    mm = re.search(r"\{[\s\S]*\}", text)
    return json.loads(mm.group(0) if mm else text)


def llm(messages, want_json=False, temperature=0.8, **_):
    """Text/vision AI: Gemini (free key) first, then a no-key fallback."""
    errors = []
    if has_gemini():
        system = "\n".join(m["content"] for m in messages if m["role"] == "system" and isinstance(m["content"], str))
        parts = []
        for m in messages:
            if m["role"] != "system":
                parts += _to_gemini_parts(m["content"])
        try:
            return gemini(parts, system=system or None, want_json=want_json, temperature=temperature)
        except Exception as e:  # noqa: BLE001
            errors.append(str(e))
            log("Gemini failed:", e)
    try:
        return _pollinations_text(messages, want_json, temperature)
    except Exception as e:  # noqa: BLE001
        errors.append(str(e))
        log("Free fallback AI failed:", e)
    hint = "" if has_gemini() else " Add a free GEMINI_API_KEY secret (see the guide)."
    raise RuntimeError("No AI could write the script." + hint + " Details: " + " | ".join(errors)[:600])


_last_img = [0.0]


def ai_image(prompt, out: Path, seed=1, width=1080, height=1920, style=""):
    """Generate one vertical image. Returns the path or None."""
    key = os.environ.get("POLLINATIONS_KEY", "").strip()
    full = (prompt + (", " + style if style else "") +
            ", vertical 9:16 composition, highly detailed, no text, no letters, no watermark")[:900]
    q = {"width": width, "height": height, "seed": seed, "nologo": "true", "model": "flux"}
    if key:
        urls = [f"https://gen.pollinations.ai/image/{urllib.parse.quote(full)}?" +
                urllib.parse.urlencode({**q, "key": key})]
    else:
        urls = [f"https://image.pollinations.ai/prompt/{urllib.parse.quote(full)}?" + urllib.parse.urlencode(q)]
    for attempt in range(4):
        gap = 1 if key else 16  # anonymous use: about one image per 15 s
        wait = _last_img[0] + gap - time.time()
        if wait > 0:
            time.sleep(wait)
        try:
            r = requests.get(urls[0], timeout=180)
            _last_img[0] = time.time()
            if r.status_code == 200 and r.headers.get("content-type", "").startswith("image"):
                out.write_bytes(r.content)
                from PIL import Image
                Image.open(out).verify()
                return out
            log(f"image attempt {attempt + 1}: HTTP {r.status_code}")
        except Exception as e:  # noqa: BLE001
            _last_img[0] = time.time()
            log(f"image attempt {attempt + 1}: {e}")
        time.sleep(10 * (attempt + 1))
    return None


ALLOWED_IMG_HOSTS = ("image.pollinations.ai", "gen.pollinations.ai")


def fetch_exact(url, out: Path):
    """Download exactly the picture you chose on the Shorts Studio page (same link = same picture)."""
    host = urllib.parse.urlparse(url).netloc
    if host not in ALLOWED_IMG_HOSTS:
        log("Skipping picture from unknown site:", host)
        return None
    for attempt in range(5):
        wait = _last_img[0] + 16 - time.time()
        if wait > 0:
            time.sleep(wait)
        try:
            r = requests.get(url, timeout=240)
            _last_img[0] = time.time()
            if r.status_code == 200 and r.headers.get("content-type", "").startswith("image"):
                out.write_bytes(r.content)
                from PIL import Image
                Image.open(out).verify()
                return out
            log(f"chosen picture attempt {attempt + 1}: HTTP {r.status_code}")
        except Exception as e:  # noqa: BLE001
            _last_img[0] = time.time()
            log(f"chosen picture attempt {attempt + 1}: {e}")
        time.sleep(10 * (attempt + 1))
    return None


def scene_prompts(sentences, cfg, n_scenes):
    """Ask the AI for one English image prompt per group of sentences."""
    groups = _group(sentences, n_scenes)
    about = cfg.get("about", "")
    numbered = "\n".join(f"{i + 1}. " + " ".join(g) for i, g in enumerate(groups))
    data = llm([
        {"role": "system", "content": "You write prompts for an AI image generator. Reply with JSON only."},
        {"role": "user", "content":
            f"Channel: {cfg.get('channel_name', '')}. {about}\n"
            f"Video style: {cfg.get('_style', 'normal')}.\n"
            "For each numbered narration part below, write ONE vivid English image prompt (max 40 words) "
            "that illustrates it as a beautiful vertical background picture. Keep one consistent visual style "
            "across all prompts. No text in the image, no logos, no real or famous people. "
            + _channel_rules(cfg) +
            f'\nReturn JSON: {{"style": "short shared style words", "prompts": ["...", ...]}} with exactly {len(groups)} prompts.\n\n'
            + numbered},
    ], want_json=True, temperature=0.7)
    prompts = [str(p) for p in data.get("prompts", [])][:len(groups)]
    while len(prompts) < len(groups):
        prompts.append(prompts[-1] if prompts else "soft cinematic light, calm landscape")
    return groups, prompts, str(data.get("style", ""))


def describe_image(path, cfg=None):
    """Look at YOUR picture and write an image prompt for a NEW picture just like it."""
    import base64
    from io import BytesIO
    from PIL import Image
    im = Image.open(path).convert("RGB")
    im.thumbnail((1024, 1024))
    buf = BytesIO()
    im.save(buf, "JPEG", quality=85)
    data = base64.b64encode(buf.getvalue()).decode()
    text = ("Describe this picture as ONE English prompt (max 60 words) for an AI image generator, so it can make "
            "a NEW picture that looks very similar: same subject, scene, composition, colours, light and mood. "
            "Do not mention any text, letters, logos or watermarks. " + _channel_rules(cfg or {}) +
            "Reply with the prompt only.")
    parts = [{"inline_data": {"mime_type": "image/jpeg", "data": data}}, {"text": text}]
    out = gemini(parts, temperature=0.4, max_tokens=400)
    out = (out if isinstance(out, str) else str(out)).strip().strip('"')
    if not out:
        raise RuntimeError("empty picture description")
    return out


def seo_tags(title, text, cfg):
    """Tags + hashtags chosen for viewers in high-paying countries (US, UK, Canada, Australia...)."""
    lang = cfg.get("language", "en")
    name = cfg.get("channel_name", "")
    about = cfg.get("about", "")
    kids = any(w in (about + " " + name).lower() for w in ("kid", "child", "cartoon", "bedtime"))
    audience = ("parents and children in the United States, United Kingdom, Canada and Australia" if kids else
                "viewers in the United States, United Kingdom, Canada, Australia and Western Europe")
    if lang != "en":
        audience += f" (for a {lang}-language video: target {lang}-speaking diaspora living in those countries, " \
                    "mix English search phrases with a few native-language ones)"
    data = llm([
        {"role": "system", "content": "You are a YouTube SEO expert. Reply with JSON only."},
        {"role": "user", "content":
            f"Channel: {name}. {about}\nVideo title: {title}\nScript: {text[:1500]}\n\n"
            f"Write YouTube tags and hashtags for this Short so it is found by {audience}. "
            "Use the exact phrases people in those countries type into YouTube search (long-tail, natural English, "
            "US spelling), most important first. No misleading or unrelated tags, no other creators' or brand names"
            + (", keep everything child-safe" if kids else "") + ".\n"
            'Return JSON: {"tags": [12-18 tags, all together under 450 characters], '
            '"hashtags": [3-5 hashtags without spaces, first one #shorts]}'},
    ], want_json=True, temperature=0.5)
    tags = [str(t).strip().strip("#") for t in data.get("tags", []) if str(t).strip()]
    out, total = [], 0
    for t in tags:
        if total + len(t) + 2 > 480:
            break
        out.append(t); total += len(t) + 2
    hashtags = []
    for h in data.get("hashtags", []):
        h = "#" + re.sub(r"[^\w]", "", str(h).lstrip("#"))
        if len(h) > 1 and h.lower() not in [x.lower() for x in hashtags]:
            hashtags.append(h)
    return out, hashtags[:5]


def look_sheet(prompts, cfg):
    """One fixed description of the recurring characters + art style, added to EVERY picture,
    so the same bear/child/place looks the same in every scene."""
    joined = "\n".join(f"{i + 1}. {p}" for i, p in enumerate(prompts) if p and not p.lower().startswith("file:"))
    if not joined:
        return ""
    data = llm([
        {"role": "system", "content": "You are an art director. Reply with JSON only."},
        {"role": "user", "content":
            f"Channel: {cfg.get('channel_name', '')}. {cfg.get('about', '')}\n"
            "These image prompts are scenes of ONE short video:\n" + joined + "\n\n"
            "Write a fixed visual bible so every picture looks like the same film: describe each recurring "
            "character exactly (species/age, colours, clothes, distinctive features) and the art style, palette "
            "and lighting. Max 60 words. " + _channel_rules(cfg) +
            '\nReturn JSON: {"bible": "..."}'},
    ], want_json=True, temperature=0.3)
    return str(data.get("bible", "")).strip()


QUALITY = {
    "kids": "Pixar-style 3D animated film still, vibrant cheerful colours, soft global illumination, "
            "expressive big eyes, clean detailed textures, bright and well lit, masterpiece",
    "cinematic": "cinematic film still, anamorphic lens, dramatic volumetric light, rich colour grading, "
                 "shallow depth of field, sharp focus, 8k detail, masterpiece",
    "normal": "high quality illustration, sharp focus, rich detail, beautiful lighting, masterpiece",
}
SHOTS = ["", ", close-up shot", ", wide establishing shot", ", low angle shot", ", over-the-shoulder view",
         ", medium shot from the side"]


def quality_words(cfg):
    about = (str(cfg.get("about", "")) + " " + str(cfg.get("channel_name", ""))).lower()
    if any(w in about for w in ("kid", "child", "cartoon", "toon", "bedtime")):
        return QUALITY["kids"]
    return QUALITY["cinematic"] if cfg.get("_style") == "cinematic" else QUALITY["normal"]


def _channel_rules(cfg):
    txt = (str(cfg.get("about", "")) + " " + str(cfg.get("channel_name", ""))).lower()
    if any(w in txt for w in ("islam", "ইসলাম", "quran", "কুরআন", "dua", "দোয়া", "নামাজ", "salah")):
        return ("This is an Islamic channel: show NO humans, NO faces and NO animals; use mosques, "
                "calligraphy-free architecture, nature, sky, light, desert, water, lanterns, books. ")
    if any(w in txt for w in ("kid", "child", "cartoon", "bedtime")):
        return "This is a children's channel: cute, colourful, friendly 3D cartoon style, nothing scary. "
    return ""


def _group(sentences, n):
    n = max(1, min(n, len(sentences)))
    size = len(sentences) / n
    groups, i = [], 0.0
    for k in range(n):
        a, b = round(i), round(i + size) if k < n - 1 else len(sentences)
        groups.append(sentences[a:b] or sentences[-1:])
        i += size
    return groups
