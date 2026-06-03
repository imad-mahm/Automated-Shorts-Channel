"""
Script generation stage.

Uses the Google Gemini API (gemini-1.5-flash) to write a complete content package
for one mythology Short: voiceover narration, YouTube metadata and a set of
image-generation prompts for the visual scenes.
"""

import json
import logging
import random
import re
import time

from google import genai

import config

logger = logging.getLogger(__name__)

# gemini-1.5-flash is retired; gemini-2.5-flash is the current free flash model.
# The first entry is preferred; the second is a fallback used on retry so a
# transient 503 (model overloaded) on one doesn't sink the whole run.
MODEL_NAME = "gemini-2.5-flash"
FALLBACK_MODEL = "gemini-flash-latest"

MYTHOLOGY_TYPES = [
    "Greek",
    "Norse",
    "Roman",
    "Egyptian",
    "Mesopotamian",
    "Hindu",
    "Celtic",
    "Japanese",
    "Aztec",
    "Slavic",
]


def _build_prompt(seed: int, retry_hint: str = "", topic: str | None = None) -> str:
    """
    Construct the Gemini instruction prompt.

    Inputs:
        seed:        random integer (1-10000) used to diversify topic selection.
        retry_hint:  optional extra instruction appended on a JSON-repair retry.
        topic:       optional requested subject. When given, the model must build
                     the video around it; when None, it picks a random subject.
    Output:
        The full prompt string.
    """
    if topic:
        topic_block = (
            f"Make the video SPECIFICALLY about: {topic}\n"
            "Choose the most fitting mythology tradition for this subject and set "
            '"mythology_type" accordingly. Use the random seed '
            f"{seed} only to vary the angle, hook and imagery — NOT to change the "
            "subject. If the request is broad, pick a focused, compelling story "
            "within it."
        )
    else:
        suggested = random.choice(MYTHOLOGY_TYPES)
        topic_block = (
            f"Use this random seed to pick a FRESH, less-obvious subject: {seed}.\n"
            f"As a starting bias consider {suggested} mythology, but you may choose "
            "any tradition (Greek, Norse, Roman, Egyptian, Mesopotamian, Hindu, "
            "Celtic, Japanese, Aztec, Slavic).\n"
            "Avoid the most overused topics when the seed is high."
        )

    base = f"""You are a viral short-form video scriptwriter specialising in world mythology.

{topic_block}

Write a complete package for ONE 9:16 vertical YouTube Short.

Return ONLY a single valid JSON object. No markdown code fences, no commentary,
no leading or trailing text. The JSON must have EXACTLY these keys:

{{
  "mythology_type": "<one tradition, e.g. Greek>",
  "subject": "<the specific myth, deity or story>",
  "voiceover": "<a 40-55 second narration, about 130-160 words, no stage directions, no scene labels>",
  "title": "<YouTube title, max 80 characters, punchy, NOT clickbait>",
  "description": "<2-3 sentence YouTube description ending with relevant hashtags>",
  "tags": ["<10 to 15 relevant lowercase tags>"],
  "scenes": [
    {{"timestamp_hint": "0-7s",   "image_prompt": "<detailed anime Flux prompt>"}},
    {{"timestamp_hint": "7-14s",  "image_prompt": "<...>"}},
    {{"timestamp_hint": "14-21s", "image_prompt": "<...>"}},
    {{"timestamp_hint": "21-28s", "image_prompt": "<...>"}},
    {{"timestamp_hint": "28-35s", "image_prompt": "<...>"}},
    {{"timestamp_hint": "35-42s", "image_prompt": "<...>"}},
    {{"timestamp_hint": "42-49s", "image_prompt": "<...>"}},
    {{"timestamp_hint": "49-55s", "image_prompt": "<...>"}}
  ]
}}

Voiceover rules:
- Open with a HOOK: a surprising or dramatic fact in the first sentence.
- Sound like a dramatic documentary narrator, NOT a Wikipedia article.
- Build tension and pay it off.
- End with a soft call to action such as "Follow for more myths".

Image prompt rules (one per scene, EXACTLY 8 scenes total — the "scenes" array
must contain 8 objects):
- Each scene illustrates a different beat of the story, in chronological order,
  so the 8 images form a visual sequence.
- Include a vivid subject description tied to that beat of the story.
- Include this exact art style phrase in every image_prompt: "flat 2D
  hand-drawn anime illustration in the style of a modern high-budget anime
  film, clean cel shading, bold crisp linework, vibrant flat colors, expressive
  2D characters with correct anatomy and detailed faces, dramatic cinematic
  anime lighting, sharp and clean, official anime key visual — purely 2D, hand
  drawn, NOT 3D, not a CGI render, not photorealistic, not a cartoon".
- Convey mood/atmosphere.
- Keep each composition simple and readable to avoid drawing errors: favor one
  or two clear focal characters (or a striking landscape/object), shown at
  medium or wide distance. AVOID large crowds, many small background figures,
  and tight close-ups of hands or intertwined fingers — these produce deformed
  results.
- End each with: "portrait 9:16 aspect ratio, no text, no watermarks".
"""
    if retry_hint:
        base += "\n\n" + retry_hint
    return base


def _extract_json(text: str) -> dict:
    """
    Parse a JSON object out of a model response, tolerating stray fences/text.

    Inputs:  text - raw model output.
    Output:  parsed dict.
    Raises:  json.JSONDecodeError / ValueError if no valid JSON can be found.
    """
    cleaned = text.strip()

    fence = re.match(r"^```(?:json)?\s*(.*?)\s*```$", cleaned, re.DOTALL)
    if fence:
        cleaned = fence.group(1).strip()

    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start != -1 and end != -1 and end > start:
            return json.loads(cleaned[start : end + 1])
        raise


def _validate_shape(data: dict) -> None:
    """Raise ValueError if the parsed dict is missing required keys/scenes."""
    required = {
        "mythology_type",
        "subject",
        "voiceover",
        "title",
        "description",
        "tags",
        "scenes",
    }
    missing = required - data.keys()
    if missing:
        raise ValueError(f"Script JSON missing keys: {missing}")
    if not isinstance(data["scenes"], list) or len(data["scenes"]) < 1:
        raise ValueError("Script JSON 'scenes' must be a non-empty list")
    for scene in data["scenes"]:
        if "image_prompt" not in scene:
            raise ValueError("Each scene must contain an 'image_prompt'")


def generate_script(topic: str | None = None) -> dict:
    """
    Generate the full content package for one mythology Short.

    Inputs:
        topic:  optional requested subject. When provided the video is built
                around it; when None a random subject is chosen.
    Output:  dict with keys mythology_type, subject, voiceover, title,
             description, tags (list) and scenes (list of {timestamp_hint,
             image_prompt}).
    Raises:  RuntimeError if generation/parsing fails after a retry.
    """
    client = genai.Client(api_key=config.GEMINI_API_KEY)

    seed = random.randint(1, 10000)
    if topic:
        logger.info("Generating script for requested topic: %s (seed %d)", topic, seed)
    else:
        logger.info("Generating script with random topic (seed %d)", seed)

    base_prompt = _build_prompt(seed, topic=topic)
    repair_prompt = _build_prompt(
        seed,
        retry_hint=(
            "Your previous response was not valid JSON. Respond again with "
            "ONLY the JSON object described above — no fences, no prose."
        ),
        topic=topic,
    )

    # Alternate between the preferred and fallback models so a per-model overload
    # spike doesn't block every attempt. Transient errors (503 overloaded, 429
    # rate limit) are retried with exponential backoff; the prompt switches to a
    # JSON-repair variant only after a parsing failure.
    max_attempts = 6
    last_error: Exception | None = None
    use_repair_prompt = False

    for attempt in range(1, max_attempts + 1):
        model_name = MODEL_NAME if attempt % 2 == 1 else FALLBACK_MODEL
        prompt = repair_prompt if use_repair_prompt else base_prompt
        try:
            response = client.models.generate_content(
                model=model_name, contents=prompt
            )
            data = _extract_json(response.text)
            _validate_shape(data)
            logger.info(
                "Script generated: %s — %s",
                data.get("mythology_type"),
                data.get("subject"),
            )
            return data
        except Exception as exc:  # noqa: BLE001 - classify then decide
            last_error = exc
            message = str(exc)
            transient = any(
                token in message
                for token in ("503", "UNAVAILABLE", "429", "RESOURCE_EXHAUSTED", "overloaded")
            )
            # A non-transient failure is almost always bad/garbled JSON, so flip
            # to the repair prompt for the next try.
            use_repair_prompt = not transient
            if attempt < max_attempts:
                backoff = min(2 ** attempt, 30)  # 2,4,8,16,30,30s
                logger.warning(
                    "Script attempt %d (%s) failed: %s — retrying in %ds",
                    attempt,
                    model_name,
                    message[:160],
                    backoff,
                )
                time.sleep(backoff)
            else:
                logger.warning("Script attempt %d (%s) failed: %s", attempt, model_name, message[:160])

    raise RuntimeError(f"Failed to generate a valid script: {last_error}")