"""
Run ONCE on your own PC to get YT_REFRESH_TOKEN (only needed for auto-upload).

  1. pip install google-auth-oauthlib
  2. Put client_secret.json (from Google Cloud) in the same folder as this file
  3. python get_youtube_token.py   -> a browser opens -> pick your channel -> Allow
  4. Copy the 3 values it prints into GitHub > Settings > Secrets and variables > Actions
"""
import json
from pathlib import Path

from google_auth_oauthlib.flow import InstalledAppFlow

secret_file = Path(__file__).with_name("client_secret.json")
flow = InstalledAppFlow.from_client_secrets_file(
    str(secret_file), scopes=["https://www.googleapis.com/auth/youtube.upload"]
)
creds = flow.run_local_server(port=0, prompt="consent", access_type="offline")
data = json.loads(secret_file.read_text())
client = data.get("installed") or data.get("web")
print("\nAdd these 3 secrets on GitHub:\n")
print("YT_CLIENT_ID     =", client["client_id"])
print("YT_CLIENT_SECRET =", client["client_secret"])
print("YT_REFRESH_TOKEN =", creds.refresh_token)
