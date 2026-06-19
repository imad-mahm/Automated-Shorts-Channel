"""
Image generation stage.

Generates one image per scene using Pollinations.ai's free FLUX image endpoint.
Pollinations needs no API key, imposes no daily quota, and serves images at
native portrait resolution (1080x1920) — so there is no account/token to manage
and the output already matches the 9:16 video frame (the assembler still scales/
crops defensively, but no longer has to discard half of a square image).

The endpoint is a simple GET:
    https://image.pollinations.ai/prompt/<url-encoded prompt>
        ?width=1080&height=1920&model=flux&nologo=true&seed=<n>
and returns the raw image bytes (JPEG) directly in the response body.
"""

import logging
import random
import time
import urllib.parse
from pathlib import Path

import requests

import config

logger = logging.getLogger(__name__)

# Pollinations text-to-image endpoint. The prompt goes in the URL path; the
# render options are query parameters.
API_BASE = "https://image.pollinations.ai/prompt/"
MODEL = "flux"

# Native portrait frame, so the assembler doesn't have to crop away half the image.
IMAGE_WIDTH = 1080
IMAGE_HEIGHT = 1920

REQUEST_TIMEOUT = 120  # seconds — Pollinations renders on demand and can queue.
DELAY_BETWEEN_REQUESTS = 3  # seconds, polite spacing under the free anonymous tier.
MAX_RETRIES = 3
# Keep prompts to a sane length so the request URL stays well within proxy limits.
MAX_PROMPT_CHARS = 2000

# JPEG (FFD8FF) and PNG (\x89PNG...) magic numbers — used to confirm we received
# an actual image rather than an HTML/JSON error page served with a 200.
_IMAGE_MAGIC = (b"\xff\xd8\xff", b"\x89PNG\r\n\x1a\n")


def _truncate_prompt(prompt: str) -> str:
    """
    Cap a prompt at MAX_PROMPT_CHARS, trimming back to the last word boundary so
    we don't cut mid-word. Returns the prompt unchanged if it already fits.
    """
    if len(prompt) <= MAX_PROMPT_CHARS:
        return prompt
    truncated = prompt[:MAX_PROMPT_CHARS]
    cut = truncated.rfind(" ")
    if cut > 0:
        truncated = truncated[:cut]
    return truncated.rstrip(" ,;")


def _looks_like_image(content: bytes) -> bool:
    """True if the bytes begin with a known image magic number."""
    return content.startswith(_IMAGE_MAGIC)


def _download_one(prompt: str, dest: Path) -> None:
    """
    Generate a single image with Pollinations and save it to `dest`.

    Inputs:
        prompt:  the image prompt text.
        dest:    pathlib.Path where the image should be written.
    Output:  None.
    Raises:
        RuntimeError after all retry attempts are exhausted for transient errors.
    """
    if len(prompt) > MAX_PROMPT_CHARS:
        logger.warning(
            "Prompt for %s is %d chars; truncating to %d to keep the URL sane",
            dest.name,
            len(prompt),
            MAX_PROMPT_CHARS,
        )
        prompt = _truncate_prompt(prompt)

    url = API_BASE + urllib.parse.quote(prompt, safe="")
    params = {
        "width": IMAGE_WIDTH,
        "height": IMAGE_HEIGHT,
        "model": MODEL,
        "nologo": "true",
        # A fresh seed per call avoids any chance of a cached/identical render.
        "seed": random.randint(1, 1_000_000),
    }
    # The token is optional — Pollinations works anonymously. When present it
    # raises rate limits and guarantees no-logo output on the newer tiers.
    headers = {}
    if config.POLLINATIONS_API_TOKEN:
        headers["Authorization"] = f"Bearer {config.POLLINATIONS_API_TOKEN}"

    last_error: Exception | None = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            logger.debug("Requesting image (attempt %d): %s", attempt, dest.name)
            resp = requests.get(
                url, params=params, headers=headers, timeout=REQUEST_TIMEOUT
            )
            resp.raise_for_status()

            content = resp.content
            if not _looks_like_image(content):
                # Pollinations occasionally returns an HTML/JSON error with a 200.
                raise ValueError(
                    "response was not an image "
                    f"(content-type={resp.headers.get('Content-Type')!r}, "
                    f"{len(content)} bytes): {content[:200]!r}"
                )

            dest.write_bytes(content)
            logger.info("Saved %s (%d bytes)", dest.name, len(content))
            return
        except Exception as exc:  # noqa: BLE001 - retry on any network/IO error
            last_error = exc
            backoff = 2 ** attempt  # exponential: 2s, 4s, 8s
            logger.warning(
                "Image generation failed for %s (attempt %d/%d): %s — retrying in %ds",
                dest.name,
                attempt,
                MAX_RETRIES,
                exc,
                backoff,
            )
            if attempt < MAX_RETRIES:
                time.sleep(backoff)

    raise RuntimeError(f"Failed to generate image {dest.name}: {last_error}")


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

        # Stay polite to the free service between requests (but not after the last).
        if index < len(scenes):
            time.sleep(DELAY_BETWEEN_REQUESTS)

    return paths
