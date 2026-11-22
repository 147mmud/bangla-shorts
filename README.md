# Bangla Shorts Maker 🎬 (বাংলা শর্টস মেকার)

Write a Bangla script → GitHub's free cloud computers make a vertical YouTube Short
with **OmniVoice Bangla voice-over + Bangla subtitles**. Your PC can be **OFF**.

স্ক্রিপ্ট দিন → GitHub-এর ফ্রি ক্লাউড কম্পিউটার ভয়েস-ওভার ও সাবটাইটেলসহ শর্টস বানিয়ে দেবে। আপনার পিসি বন্ধ থাকলেও চলবে।

---

## ✍️ How to make a short (every day) / প্রতিদিন যেভাবে শর্টস বানাবেন

Works from your phone (GitHub app or browser) or any computer.

1. Open this repository on **github.com** → open the **`scripts`** folder.
2. Tap **Add file → Create new file**.
3. Name it with a number so they go in order, e.g. **`002-tawbah.txt`**
4. Write the script like this:

   ```
   title: তওবার দরজা এখনো খোলা #shorts
   tags: তওবা, islamic shorts, bangla
   description: আল্লাহর রহমত থেকে নিরাশ হবেন না।
   ---
   এখানে আপনার বাংলা স্ক্রিপ্ট লিখুন।
   প্রতিটি বাক্য দাঁড়ি দিয়ে শেষ করুন।
   ```
   The part above `---` is optional. Only the Bangla text is really needed.
5. Tap **Commit changes**. That's it — the video starts being made automatically.

**Many scripts?** Add as many files as you like. They are made **one by one**, in name order.

## 📥 Where is my video? / ভিডিও কোথায় পাবো?

* Open **`results.md`** in the repository → tap **download** next to your script, **or**
* Open the **Releases** section (right side of the repository page) → each short has its own MP4.

A short usually takes **5–15 minutes** (the very first one takes longer because the voice model downloads once).
Watch progress in the **Actions** tab: 🟡 working, ✅ done, ❌ failed.

If a script fails it moves to `scripts/failed/` with a note explaining why.

## 🎨 Change the look / ডিজাইন বদলাতে

Edit **`config.yml`** on github.com (pencil ✏️ icon). Everything has notes. Common changes:

| What | Setting |
|---|---|
| Male / female / older / younger voice | `voice_style` |
| Faster or slower speaking | `voice_speed` |
| Channel name at the top | `channel_name` |
| Text size / position | `font_size`, `subtitle_position` |
| Background colours | `background_colors` |

**Background photos/videos:** upload your own pictures (mosques, nature, sky…) or short vertical clips into the
**`backgrounds`** folder. They are shuffled with a slow zoom. Empty folder = blue moving gradient.

**Background nasheed/sound:** put an MP3 in the **`music`** folder (volume: `background_music_volume`). Empty = voice only.

## 🗣️ The voice / কণ্ঠ

* The first video **designs a "channel voice"** from `voice_style` and saves it as `voice/channel_voice.wav`.
  Every later video uses that **same voice**. Change `voice_style` (or delete that file) to get a new voice.
* **Use your own voice:** record 5–10 seconds of clean Bangla speech, upload it to the `voice` folder as
  **`my_voice.wav`** (or `.mp3`), and put exactly what you said in **`my_voice.txt`**. OmniVoice will clone it.
* If OmniVoice ever fails, the app automatically uses the Microsoft Bangla voice (`edge_voice`) so you still get a video.
  To always use that faster voice, set `voice_engine: edge`.

## 🔑 Optional extras (secrets)

Add in repository **Settings → Secrets and variables → Actions → New repository secret**.

**Free stock video backgrounds (Pexels):** make a free account at pexels.com/api, copy the key,
add secret `PEXELS_API_KEY`, then set e.g. `pexels_query: "mosque"` in `config.yml`.

**Automatic YouTube upload — read this first ⚠️**
YouTube locks videos uploaded by personal (un-audited) API projects as **private forever** — they can't be made public,
only re-uploaded. So the simple way is: download the MP4 and upload it in the YouTube app (30 seconds).
If you still want it (e.g. after passing YouTube's API audit): create a Google Cloud project → enable
*YouTube Data API v3* → OAuth consent screen (set to *In production*) → create an OAuth client of type *Desktop app* →
download `client_secret.json` → run `maker/get_youtube_token.py` once on your PC → add the 3 secrets it prints
(`YT_CLIENT_ID`, `YT_CLIENT_SECRET`, `YT_REFRESH_TOKEN`) → set `youtube_upload: true`.

## ℹ️ Good to know

* Keep this repository **public** — GitHub Actions is then free and unlimited. (Private repos get 2,000 free minutes per month.)
* The daily run at 9:00 AM Bangladesh time finishes any scripts still waiting.
* Shorts must be under 3 minutes. About 130–160 Bangla words ≈ 1 minute.
