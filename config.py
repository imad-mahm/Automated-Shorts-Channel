"""
Central configuration for the mythology shorts pipeline.

Every secret and tunable path lives here. Secrets are read from environment
variables only (never hardcoded). In local development a `.env` file is loaded
via python-dotenv; in GitHub Actions the same variables are injected as
repository secrets.
"""

import os
import shutil
from pathlib import Path

# Load a local .env file if present. In CI the variables come from the
# environment directly, so a missing .env is not an error.
try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # pragma: no cover - dotenv is always in requirements
    # If python-dotenv isn't installed we simply rely on the real environment.
    pass


# --------------------------------------------------------------------------- #
# Secrets (environment variables only)
# --------------------------------------------------------------------------- #
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
YOUTUBE_CLIENT_ID = os.environ.get("YOUTUBE_CLIENT_ID")
YOUTUBE_CLIENT_SECRET = os.environ.get("YOUTUBE_CLIENT_SECRET")
YOUTUBE_REFRESH_TOKEN = os.environ.get("YOUTUBE_REFRESH_TOKEN")

# Pollinations.ai — free FLUX image generation (no key required). The optional
# token raises rate limits / guarantees no-logo output on the newer tiers; the
# pipeline works without it, so it is NOT a required variable.
POLLINATIONS_API_TOKEN = os.environ.get("POLLINATIONS_API_TOKEN")


# --------------------------------------------------------------------------- #
# Paths
# --------------------------------------------------------------------------- #
# Kept as plain strings (as required by the spec) but every consumer wraps them
# in pathlib.Path for actual filesystem work.
OUTPUT_DIR = "output/"
ASSETS_DIR = "assets/"
MUSIC_DIR = "assets/music/"

# JSON file holding the FIFO of requested topics managed by the web interface.
QUEUE_FILE = "topics_queue.json"


# --------------------------------------------------------------------------- #
# FFmpeg / ffprobe resolution
# --------------------------------------------------------------------------- #
def _resolve_ffmpeg_tool(name: str) -> str:
    """
    Locate an FFmpeg-suite executable (ffmpeg/ffprobe).

    Resolution order:
      1. PATH (covers GitHub Actions, where ffmpeg is pre-installed).
      2. Common Windows winget install location, since a freshly winget-installed
         FFmpeg isn't visible to already-open terminals (stale PATH).
    Falls back to the bare name so the error surfaces clearly if truly missing.

    Inputs:  name - "ffmpeg" or "ffprobe".
    Output:  an absolute path string, or the bare name if not found.
    """
    on_path = shutil.which(name)
    if on_path:
        return on_path

    if os.name == "nt":
        local = os.environ.get("LOCALAPPDATA", "")
        if local:
            matches = sorted(
                Path(local).glob(
                    f"Microsoft/WinGet/Packages/Gyan.FFmpeg*/**/bin/{name}.exe"
                )
            )
            if matches:
                return str(matches[-1])  # newest version last after sort

    return name


FFMPEG = _resolve_ffmpeg_tool("ffmpeg")
FFPROBE = _resolve_ffmpeg_tool("ffprobe")


# Required env vars that must be present for the pipeline to run end to end.
_REQUIRED_VARS = (
    "GEMINI_API_KEY",
    "YOUTUBE_CLIENT_ID",
    "YOUTUBE_CLIENT_SECRET",
    "YOUTUBE_REFRESH_TOKEN",
)


def validate() -> None:
    """
    Ensure every required environment variable is set.

    Inputs:  none (reads module-level constants populated from the environment).
    Output:  None on success.
    Raises:  RuntimeError listing exactly which variables are missing.
    """
    missing = [name for name in _REQUIRED_VARS if not globals().get(name)]
    if missing:
        raise RuntimeError(
            "Missing required environment variables: "
            + ", ".join(missing)
            + ". Set them in a local .env file or as GitHub Actions secrets."
        )
