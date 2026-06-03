"""
Image generation stage.

Generates one image per scene using Cloudflare Workers AI's FLUX.1-schnell
model. The model is free (well within Cloudflare's daily free allowance),
Apache-2.0 licensed (safe for commercial/monetized output), and called over a
simple authenticated REST endpoint.

FLUX.1-schnell returns a 1024x1024 image as base64-encoded JPEG inside a JSON
envelope. We decode and save it as-is; the video assembler later scales and
center-crops every image to portrait 1080x1920, so the source dimensions here
don't need to match.
"""

import base64
import binascii
import logging
import time
from pathlib import Path

import requests

import config

logger = logging.getLogger(__name__)

# Cloudflare Workers AI REST endpoint for the FLUX.1-schnell text-to-image model.
MODEL = "@cf/black-forest-labs/flux-1-schnell"
API_URL = (
    "https://api.cloudflare.com/client/v4/accounts/"
    "{account_id}/ai/run/" + MODEL
)

REQUEST_TIMEOUT = 60  # seconds
DELAY_BETWEEN_REQUESTS = 2  # seconds, to stay polite under the free tier
MAX_RETRIES = 3
# schnell supports 1-8 diffusion steps; 8 is the max quality the model allows.
STEPS = 8


def _download_one(prompt: str, dest: Path) -> None:
    """
    Generate a single image with Cloudflare FLUX.1-schnell and save it to `dest`.

    Inputs:
        prompt:  the image prompt text.
        dest:    pathlib.Path where the JPG should be written.
    Output:  None.
    Raises:
        RuntimeError on a fatal auth/config error (no retry), or after all retry
        attempts are exhausted for transient errors.
    """
    url = API_URL.format(account_id=config.CLOUDFLARE_ACCOUNT_ID)
    headers = {
        "Authorization": f"Bearer {config.CLOUDFLARE_API_TOKEN}",
        "Content-Type": "application/json",
    }
    payload = {"prompt": prompt, "steps": STEPS}

    last_error: Exception | None = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            logger.debug("Requesting image (attempt %d): %s", attempt, dest.name)
            resp = requests.post(
                url, headers=headers, json=payload, timeout=REQUEST_TIMEOUT
            )

            # Auth/permission failures are config problems, not transient — fail
            # fast with an actionable message instead of burning retries.
            if resp.status_code in (401, 403):
                raise RuntimeError(
                    "Cloudflare rejected the request "
                    f"(HTTP {resp.status_code}). Check that CLOUDFLARE_ACCOUNT_ID "
                    "and CLOUDFLARE_API_TOKEN are correct and the token has the "
                    "'Workers AI' permission."
                )

            resp.raise_for_status()
            image_bytes = _extract_image_bytes(resp)
            dest.write_bytes(image_bytes)
            logger.info("Saved %s (%d bytes)", dest.name, len(image_bytes))
            return
        except RuntimeError:
            # Fatal (auth/config) errors are raised as RuntimeError above — don't
            # retry them, let them propagate immediately.
            raise
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


def _extract_image_bytes(resp: requests.Response) -> bytes:
    """
    Pull the decoded image bytes out of a Cloudflare Workers AI JSON response.

    FLUX.1-schnell responds with:
        {"result": {"image": "<base64 jpeg>"}, "success": true, ...}

    Inputs:  resp - the requests.Response from the Workers AI endpoint.
    Output:  raw JPEG bytes.
    Raises:  ValueError if the envelope is malformed or the image is empty.
    """
    try:
        body = resp.json()
    except ValueError as exc:
        raise ValueError(f"Cloudflare response was not JSON: {exc}") from exc

    if not body.get("success", False):
        raise ValueError(f"Cloudflare reported failure: {body.get('errors')}")

    b64 = (body.get("result") or {}).get("image")
    if not b64:
        raise ValueError("Cloudflare response contained no image data")

    try:
        image_bytes = base64.b64decode(b64)
    except (binascii.Error, ValueError) as exc:
        raise ValueError(f"Could not base64-decode image: {exc}") from exc

    if not image_bytes:
        raise ValueError("Decoded image was empty")
    return image_bytes


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
