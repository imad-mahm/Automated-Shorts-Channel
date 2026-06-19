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
# flux-1-schnell rejects any prompt longer than this with HTTP 400
# ("Length of '/prompt' must be <= 2048"). Gemini occasionally writes a longer
# prompt for a single scene, so we cap before sending.
MAX_PROMPT_CHARS = 2048


def _truncate_prompt(prompt: str) -> str:
    """
    Cap a prompt at Cloudflare's 2048-character limit.

    Trims back to the last word boundary so we don't cut mid-word, and strips any
    trailing separators left behind. Returns the prompt unchanged if it already
    fits.
    """
    if len(prompt) <= MAX_PROMPT_CHARS:
        return prompt
    truncated = prompt[:MAX_PROMPT_CHARS]
    cut = truncated.rfind(" ")
    if cut > 0:
        truncated = truncated[:cut]
    return truncated.rstrip(" ,;")


def _cloudflare_error_detail(resp: requests.Response) -> str:
    """
    Extract a human-readable reason from a non-2xx Cloudflare response.

    Cloudflare returns {"errors": [{"message": "...", "code": ...}], ...} on
    failure. Fall back to the raw (truncated) body if it isn't the expected JSON.
    """
    try:
        errors = resp.json().get("errors")
        if errors:
            return "; ".join(e.get("message", str(e)) for e in errors)
    except ValueError:
        pass
    return resp.text[:300]


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

    if len(prompt) > MAX_PROMPT_CHARS:
        logger.warning(
            "Prompt for %s is %d chars; truncating to Cloudflare's %d-char limit",
            dest.name,
            len(prompt),
            MAX_PROMPT_CHARS,
        )
        prompt = _truncate_prompt(prompt)

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

            # A 400 is a deterministic bad-request (e.g. a rejected prompt) — it
            # will never succeed on retry, so surface Cloudflare's own reason and
            # fail fast instead of burning three attempts.
            if resp.status_code == 400:
                raise RuntimeError(
                    f"Cloudflare rejected the prompt for {dest.name} (HTTP 400): "
                    f"{_cloudflare_error_detail(resp)}"
                )

            # A 429 is usually the daily free-neuron allocation being exhausted,
            # which won't recover until it resets at 00:00 UTC — retrying within a
            # run is pointless and the bare status hides the real reason. Surface
            # Cloudflare's message and fail fast in that case. (A rare per-minute
            # burst 429 falls through to raise_for_status() and is retried.)
            if resp.status_code == 429:
                detail = _cloudflare_error_detail(resp)
                if "neuron" in detail.lower() or "daily" in detail.lower():
                    raise RuntimeError(
                        f"Cloudflare Workers AI daily free quota exhausted while "
                        f"generating {dest.name}: {detail} The free allowance resets "
                        "at 00:00 UTC. Rerun after the reset, lower STEPS/scene count "
                        "to spend fewer neurons per run, or upgrade to the Workers "
                        "Paid plan."
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
