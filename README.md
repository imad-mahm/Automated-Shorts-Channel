# Mythology YouTube Shorts — automated daily pipeline

A fully automated, **zero-cost** pipeline that generates and uploads one
mythology-themed YouTube Short every day. It runs on GitHub Actions' free tier on
a daily cron schedule and needs **no human intervention** once set up.

Each run:

1. Writes a dramatic 40–55s narration + YouTube metadata with **Gemini** (free).
2. Generates 4 cinematic scene images with **Pollinations.ai** (free, no key).
3. Narrates the script with **edge-tts** (free Microsoft neural voices).
4. Assembles a 1080×1920 Ken Burns slideshow with **FFmpeg**, with optional
   royalty-free background music mixed quietly under the voice.
5. Burns animated word-burst captions onto the video.
6. Uploads it as a public Short via the **YouTube Data API v3**.

Everything except your own time is free.

---

## Project layout

```
mythology-shorts/
├── .github/workflows/daily_upload.yml   # daily cron + manual trigger
├── pipeline/
│   ├── script_generator.py              # Gemini → voiceover + metadata + scene prompts
│   ├── image_generator.py               # Pollinations.ai → scene images
│   ├── tts.py                           # edge-tts → MP3 + word-level VTT
│   ├── video_assembler.py               # FFmpeg → Ken Burns slideshow + music
│   ├── captioner.py                     # VTT → ASS → burned-in captions
│   └── uploader.py                      # YouTube Data API v3 upload
├── assets/music/                        # drop royalty-free MP3s here
├── config.py                            # env-var config + validation
├── main.py                              # orchestrator
├── get_youtube_token.py                 # one-time OAuth helper (run locally)
├── requirements.txt
├── .env.example
└── .gitignore
```

---

## Prerequisites

- **Python 3.11+**
- **FFmpeg** installed locally (only needed to test locally; GitHub Actions has it
  pre-installed). Verify with `ffmpeg -version` and `ffprobe -version`.
- A **GitHub account**.
- A **Google account with a YouTube channel** you want to upload to.

---

## Setup

### Step 1 — Get a Gemini API key

1. Go to <https://aistudio.google.com/app/apikey>.
2. Click **Create API key** and copy it. This is your `GEMINI_API_KEY`.

### Step 2 — Set up YouTube OAuth2

1. Open the [Google Cloud Console](https://console.cloud.google.com/) and create a
   **new project**.
2. In **APIs & Services → Library**, enable **YouTube Data API v3**.
3. In **APIs & Services → OAuth consent screen**, configure the consent screen
   (External is fine), and add **your own Google account** as a **Test user**.
4. In **APIs & Services → Credentials → Create credentials → OAuth client ID**,
   choose application type **Desktop app**.
5. **Download** the credentials JSON and save it as `client_secret.json` in the
   project root. Note the **Client ID** and **Client secret** — these are your
   `YOUTUBE_CLIENT_ID` and `YOUTUBE_CLIENT_SECRET`.
6. Get a refresh token by running the one-time helper locally:

   ```bash
   pip install -r requirements.txt
   python get_youtube_token.py
   ```

   A browser opens; approve access with the Google account that owns the channel.
   The script prints your **refresh token** — that is your `YOUTUBE_REFRESH_TOKEN`.
   You only ever run this once.

### Step 3 — Fork/clone the repo and add GitHub secrets

In your GitHub repo, go to **Settings → Secrets and variables → Actions → New
repository secret** and add all four:

| Secret name             | Value                                  |
| ----------------------- | -------------------------------------- |
| `GEMINI_API_KEY`        | from Step 1                            |
| `YOUTUBE_CLIENT_ID`     | from Step 2                            |
| `YOUTUBE_CLIENT_SECRET` | from Step 2                            |
| `YOUTUBE_REFRESH_TOKEN` | from `get_youtube_token.py` in Step 2  |

### Step 4 — Add background music (optional but recommended)

Drop **1–5 royalty-free MP3 files** into `assets/music/`. A random track is mixed
quietly (15% volume) under the narration each run. If the folder is empty, the
pipeline simply runs with voice only.

> Source tip: the **YouTube Audio Library** (in YouTube Studio → Audio Library)
> has free, license-cleared tracks. Make sure anything you use permits this use.

Commit the MP3s — the `.gitignore` is set up to keep `assets/music/*.mp3` tracked
while ignoring all other generated audio.

### Step 5 — Trigger it manually first

1. Go to the **Actions** tab → **Daily mythology short** → **Run workflow**.
2. Watch the logs. On success you'll see the uploaded video URL at the end.

After that it runs automatically every day at **10:00 UTC**. (Edit the `cron`
line in `.github/workflows/daily_upload.yml` to change the time.)

---

## Choosing topics (custom videos & the web queue)

By default each run picks a **random** myth. You can instead steer exactly what
gets made, three ways:

### 1. The web interface (recommended)

A small local app to manage a **topic queue** — the daily run consumes the top
topic each day (and only removes it after a successful upload). Empty queue →
random myth, as before.

```bash
pip install -r webapp/requirements.txt
python webapp/app.py        # then open http://127.0.0.1:5000
```

- **Add / remove / reorder** topics (broad like *"Egyptian underworld"* or
  specific like *"the exact story of Anubis weighing the heart"*).
- **Save & push to GitHub** commits `topics_queue.json` so the scheduled Action
  uses your latest queue. (Requires the folder to be a git repo with a remote.)
- **Generate now** (optional) triggers a run immediately. Set two env vars first:
  - `GITHUB_REPO` — e.g. `yourname/mythology-shorts`
  - `GITHUB_TOKEN` — a fine-grained PAT with **Actions: write** on that repo

The queue lives in `topics_queue.json` (a plain list), so you can also edit it by
hand and commit it.

### 2. One-off from the GitHub Actions UI

**Actions → Daily mythology short → Run workflow** now has a **topic** box. Leave
it blank to use the queue/random, or type a topic for just that run.

### 3. Local one-off

```bash
TOPIC="Norse: Fenrir breaking free at Ragnarök" python main.py
```

Precedence: explicit `TOPIC` (manual) → front of the queue → random.

---

## Local development / testing

```bash
cp .env.example .env        # then fill in your four secrets
pip install -r requirements.txt
python main.py
```

The pipeline reads secrets from `.env` locally (via `python-dotenv`) and from
repository secrets in CI — no code changes needed between the two.

Working files are written to `output/`, which is created fresh at the start of
each run and deleted at the end (success or failure). Nothing in `output/` is
ever committed.

---

## Notes & design decisions

- **No paid APIs.** Gemini, Pollinations.ai, and edge-tts are all free; YouTube
  uploads are free within the Data API's daily quota (one upload/day is well
  within limits).
- **FFmpeg is called directly** via `subprocess` (no `ffmpeg-python` wrapper) for
  precise control and readable error output.
- **Captions** come from edge-tts word boundaries, so they're perfectly aligned
  to the synthesized speech for free. `openai-whisper` is included only as a
  fallback transcription option.
- **Zoom (Ken Burns)** feeds a single still frame per image into FFmpeg's
  `zoompan`; this avoids the stuttering "sawtooth" zoom you get when looping a
  multi-frame input.
- **Failures fail the Action**, which triggers GitHub's automatic notification
  email to the repo owner, so you'll know if a day's run breaks.
