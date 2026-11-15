# Urdu Real Stories AI — YouTube Automation Agent

A separate GitHub project for Urdu-language documentary-style videos about:
- Islamic stories, using Quran references and carefully identified authentic sources
- Historical true stories, with cited references
- Motivational real-life biographies, based on documented facts

## Pipeline

1. Select a topic from `topics.json`.
2. Retrieve the listed reference page when available.
3. Ask Gemini to draft a natural Pakistani Urdu narration (Urdu script), scene plan, title, description and tags.
4. Generate Pakistani Urdu narration with `ur-PK-AsadNeural` (Edge TTS).
5. Search Wikimedia Commons for relevant images and record available license/attribution metadata.
6. Build a 720p video, thumbnail and metadata.
7. Optionally upload through the YouTube Data API.
8. Save the finished assets as a GitHub Actions artifact.

## Set up GitHub Actions secrets

In the new repository, open **Settings → Secrets and variables → Actions → New repository secret**.

Required:
- `GEMINI_API_KEY`: Google AI Studio API key. Free-tier limits and availability are controlled by Google and can change.
- `YOUTUBE_TOKEN_JSON`: OAuth token JSON for the YouTube channel owner, with the `youtube.upload` scope.

Never put API keys, OAuth client secrets or token files into the repository.

## YouTube OAuth token

Create an OAuth client in Google Cloud Console for YouTube Data API v3, download the OAuth client JSON, and run locally:

```bash
pip install -r requirements.txt
python setup_youtube_oauth.py client_secret.json
```

The helper opens Google's consent page. Save the generated `youtube_token.json` securely. Copy its full JSON content into the GitHub Actions secret named `YOUTUBE_TOKEN_JSON`. The token includes a refresh token; treat it like a password and do not share it.

Google may require OAuth consent-screen configuration and verification depending on the app's publishing status. YouTube API quota and channel restrictions apply.

## Run

1. Open **Actions → Build Urdu Story Video → Run workflow**.
2. Choose `auto`, `islamic`, `history` or `motivation`.
3. First run: leave **Upload to YouTube** unchecked.
4. Download the workflow artifact and review the narration, image licenses, thumbnail and facts.
5. Only enable upload after OAuth is configured and you are comfortable with the output.

The daily scheduled run is disabled by default for upload. To enable daily public publishing after testing, change `UPLOAD_ON_SCHEDULE` to `true` in the workflow and ensure both secrets exist. The default schedule is 15:30 UTC (19:30 Oman time).

## Important notes

- The first generated video should be reviewed by a human. AI can make factual mistakes.
- Islamic stories must not contain fabricated hadith, invented dialogue, or unsupported details. Prophets and sacred figures are not visually depicted.
- Wikimedia Commons search results have individual license requirements. The agent records attribution details in `output/asset_credits.json`; check each asset's license before commercial use.
- When Commons has no suitable image, the video uses a designed scene card instead of inventing an archival photograph.
- Edge TTS is not a guaranteed permanent free API; service terms and availability can change.
- GitHub Actions minutes, Gemini quotas, YouTube API quotas, OAuth permissions and platform policies apply.
- Automated publishing does not guarantee views, monetization, accuracy or YouTube approval.

## Add topics

Edit `topics.json`. Every story must include a reliable source URL and a note describing what claims are safe to make. Do not add unverified viral stories.

## Project is independent

This repository is intended to be independent of any other YouTube agent repository.
