"""
Free AI helpers used on GitHub's servers:
  - llm()       : GitHub Models (free, uses the workflow's own GITHUB_TOKEN, needs "models: read")
  - ai_image()  : Pollinations image generation (free; optional POLLINATIONS_KEY secret = faster, no watermark)
"""
import json
import os
import re
import time
import urllib.parse
from pathlib import Path

import requests

GH_MODELS_URL = "https://models.github.ai/inference/chat/completions"
DEFAULT_MODELS = ["openai/gpt-4.1-mini", "openai/gpt-4o-mini", "openai/gpt-4o"]


def log(*a):
    print("[ai]", *a, flush=True)


def llm(messages, want_json=False, model=None, temperature=0.8, max_tokens=2500):
    """Chat completion through GitHub Models. Tries a few models in turn."""
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if not token:
        raise RuntimeError("No GITHUB_TOKEN for GitHub Models")
    models = [model] if model else DEFAULT_MODELS
    last = None
    for m in models:
        body = {"model": m, "messages": messages, "temperature": temperature, "max_tokens": max_tokens}
        if want_json:
            body["response_format"] = {"type": "json_object"}
        for attempt in range(3):
            try:
                r = requests.post(GH_MODELS_URL, json=body, timeout=180, headers={
                    "Authorization": f"Bearer {token}", "Content-Type": "application/json",
                    "Accept": "application/vnd.github+json"})
                if r.status_code == 429:
                    wait = int(r.headers.get("retry-after", "20") or 20)
                    log(f"{m}: rate limited, waiting {wait}s")
                    time.sleep(min(wait, 90))
                    continue
                if r.status_code >= 400:
                    last = f"{m}: HTTP {r.status_code} {r.text[:300]}"
                    log(last)
                    break
                text = r.json()["choices"][0]["message"]["content"]
                if want_json:
                    mm = re.search(r"\{[\s\S]*\}", text)
                    return json.loads(mm.group(0) if mm else text)
                return text
            except (requests.RequestException, ValueError, KeyError) as e:
                last = f"{m}: {e}"
                log(last)
                time.sleep(5)
    raise RuntimeError(f"GitHub Models failed: {last}")


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
