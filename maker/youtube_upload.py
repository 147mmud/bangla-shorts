"""Optional: upload a finished short to YouTube (needs 3 GitHub secrets, see GUIDE.md)."""
import os

from make_short import log


def upload(video_path, info, cfg):
    cid, secret, token = (os.environ.get(k, "").strip() for k in ("YT_CLIENT_ID", "YT_CLIENT_SECRET", "YT_REFRESH_TOKEN"))
    if not (cid and secret and token):
        log("YouTube upload skipped: YT_CLIENT_ID / YT_CLIENT_SECRET / YT_REFRESH_TOKEN secrets are missing")
        return None
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaFileUpload

    creds = Credentials(
        None, refresh_token=token, client_id=cid, client_secret=secret,
        token_uri="https://oauth2.googleapis.com/token",
        scopes=["https://www.googleapis.com/auth/youtube.upload"],
    )
    yt = build("youtube", "v3", credentials=creds, cache_discovery=False)
    body = {
        "snippet": {
            "title": info["title"][:100],
            "description": info["description"][:4900],
            "tags": info["tags"][:30],
            "categoryId": str(cfg.get("category_id", "27")),
            "defaultLanguage": "bn",
            "defaultAudioLanguage": "bn",
        },
        "status": {"privacyStatus": cfg.get("privacy", "private"), "selfDeclaredMadeForKids": False},
    }
    req = yt.videos().insert(
        part="snippet,status", body=body,
        media_body=MediaFileUpload(str(video_path), mimetype="video/mp4", chunksize=-1, resumable=True),
    )
    resp = None
    while resp is None:
        _, resp = req.next_chunk()
    log("Uploaded to YouTube: https://youtu.be/" + resp["id"])
    return resp["id"]
