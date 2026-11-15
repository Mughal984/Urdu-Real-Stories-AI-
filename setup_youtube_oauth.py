import json, sys
from pathlib import Path
from google_auth_oauthlib.flow import InstalledAppFlow

if len(sys.argv) != 2:
    raise SystemExit("Usage: python setup_youtube_oauth.py client_secret.json")
client_file = Path(sys.argv[1])
if not client_file.exists():
    raise SystemExit("OAuth client JSON file not found.")
flow = InstalledAppFlow.from_client_secrets_file(
    str(client_file),
    scopes=["https://www.googleapis.com/auth/youtube.upload"]
)
creds = flow.run_local_server(port=0, prompt="consent", access_type="offline")
out = Path("youtube_token.json")
out.write_text(creds.to_json(), encoding="utf-8")
print(f"Saved {out.resolve()}. Keep it private; add its full JSON content as GitHub secret YOUTUBE_TOKEN_JSON.")
