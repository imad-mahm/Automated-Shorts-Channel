"""
Image generation stage.

Generates one image per scene using Pollinations.ai's free, key-less,
URL-based image API (Flux model). Images are saved as portrait 1080x1920 JPGs
ready to feed into the video assembler.
"""

import logging
import random
import time
from pathlib import Path
from urllib.parse import quote

import requests

import config

logger = logging.getLogger(__name__)

BASE_URL = "https://image.pollinations.ai/prompt/"

REQUEST_TIMEOUT = 60  # seconds, per the spec
DELAY_BETWEEN_REQUESTS = 2  # seconds, to be polite to the free service
MAX_RETRIES = 3


def _download_one(prompt: str, dest: Path) -> None:
    """
    Download a single Pollinations image to `dest`, with retry/backoff.

    Inputs:
        prompt:  the image prompt text.
        dest:    pathlib.Path where the JPG should be written.
    Output:  None.
    Raises:  RuntimeError if all retry attempts fail.
    """
    # URL-encode the prompt for the path segment; quote with empty safe set so
    # slashes inside the prompt don't break the route.
    encoded = quote(prompt, safe="")
    params = {
        "width": 1080,
        "height": 1920,
        "model": "flux",
        "nologo": "true",
        "enhance": "true",
        "seed": random.randint(1, 1_000_000),
    }
    url = BASE_URL + encoded

    last_error: Exception | None = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            logger.debug("Requesting image (attempt %d): %s", attempt, dest.name)
            resp = requests.get(url, params=params, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
            if not resp.content:
                raise ValueError("Empty image response body")
            dest.write_bytes(resp.content)
            logger.info("Saved %s (%d bytes)", dest.name, len(resp.content))
            return
        except Exception as exc:  # noqa: BLE001 - retry on any network/IO error
            last_error = exc
            backoff = 2 ** attempt  # exponential: 2s, 4s, 8s
            logger.warning(
                "Image download failed for %s (attempt %d/%d): %s — retrying in %ds",
                dest.name,
                attempt,
                MAX_RETRIES,
                exc,
                backoff,
            )
            if attempt < MAX_RETRIES:
                time.sleep(backoff)

    raise RuntimeError(f"Failed to download image {dest.name}: {last_error}")


def generate_images(scenes: list[dict]) -> list[Path]:
    """
    Generate one image per scene.

    Inputs:
        scenes:  list of scene dicts (each with an 'image_prompt' key) as
                 produced by script_generator.generate_script().
    Output:
        Ordered list of pathlib.Path objects for the saved images
        (output/scene_01.jpg, output/scene_02.jpg, ...).
    Raises:  RuntimeError if any image cannot be generated.
    """
    output_dir = Path(config.OUTPUT_DIR)
    output_dir.mkdir(parents=True, exist_ok=True)

    paths: list[Path] = []
    for index, scene in enumerate(scenes, start=1):
        prompt = scene["image_prompt"]
        dest = output_dir / f"scene_{index:02d}.jpg"
        logger.info("Generating image %d/%d", index, len(scenes))
        _download_one(prompt, dest)
        paths.append(dest)

        # Be polite to the free service between requests (but not after the last).
        if index < len(scenes):
            time.sleep(DELAY_BETWEEN_REQUESTS)

    return paths
