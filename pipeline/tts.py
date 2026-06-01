"""
Text-to-speech stage.

Converts the voiceover script to an MP3 narration and a word-level WebVTT
subtitle file using Microsoft Edge's free neural voices via the `edge-tts`
library. The VTT feeds directly into the captioner stage.

We build the WebVTT file ourselves from raw WordBoundary events rather than
relying on edge_tts.SubMaker, because SubMaker's public API has changed several
times across edge-tts releases whereas the WordBoundary chunk fields
("offset", "duration", "text", all in 100-nanosecond units) have stayed stable.
"""

import asyncio
import logging
from pathlib import Path

import edge_tts

import config

logger = logging.getLogger(__name__)

# Deep, authoritative British voice suits dramatic mythology narration.
PRIMARY_VOICE = "en-GB-RyanNeural"
FALLBACK_VOICE = "en-US-GuyNeural"


def _ticks_to_timestamp(ticks: int) -> str:
    """
    Convert a 100-nanosecond tick count to a WebVTT timestamp (HH:MM:SS.mmm).

    Inputs:  ticks - time in 100ns units (as edge-tts reports offsets).
    Output:  WebVTT timestamp string.
    """
    total_seconds = ticks / 10_000_000
    hours = int(total_seconds // 3600)
    minutes = int((total_seconds % 3600) // 60)
    seconds = total_seconds % 60
    return f"{hours:02d}:{minutes:02d}:{seconds:06.3f}"


async def _synthesize(text: str, voice: str, mp3_path: Path, vtt_path: Path) -> None:
    """
    Run one edge-tts synthesis pass, writing both MP3 audio and VTT subtitles.

    Inputs:
        text:      the voiceover narration.
        voice:     edge-tts voice short name.
        mp3_path:  destination for the audio.
        vtt_path:  destination for the word-level WebVTT subtitles.
    Output:  None.
    Raises:  propagates any edge-tts error to the caller for fallback handling.
    """
    # edge-tts >=7 defaults to SentenceBoundary, which gives no per-word timing.
    # boundary="WordBoundary" restores word-level offsets/durations that the
    # captioner needs for 3-4 word caption bursts.
    communicate = edge_tts.Communicate(text, voice, boundary="WordBoundary")
    cues: list[str] = ["WEBVTT", ""]

    with mp3_path.open("wb") as audio_file:
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                audio_file.write(chunk["data"])
            elif chunk["type"] == "WordBoundary":
                start = _ticks_to_timestamp(chunk["offset"])
                end = _ticks_to_timestamp(chunk["offset"] + chunk["duration"])
                cues.append(f"{start} --> {end}")
                cues.append(chunk["text"])
                cues.append("")

    vtt_path.write_text("\n".join(cues), encoding="utf-8")


def generate_tts(voiceover: str) -> tuple[Path, Path]:
    """
    Synthesize narration audio and word-level subtitles.

    Inputs:
        voiceover:  the narration string from the script generator.
    Output:
        (mp3_path, vtt_path) as pathlib.Path objects:
        output/voiceover.mp3 and output/voiceover.vtt.
    Raises:  RuntimeError if both the primary and fallback voices fail.
    """
    output_dir = Path(config.OUTPUT_DIR)
    output_dir.mkdir(parents=True, exist_ok=True)

    mp3_path = output_dir / "voiceover.mp3"
    vtt_path = output_dir / "voiceover.vtt"

    for voice in (PRIMARY_VOICE, FALLBACK_VOICE):
        try:
            logger.info("Synthesizing voiceover with %s", voice)
            # edge-tts is async internally; we drive it from sync code here so the
            # top-level orchestration stays simple and sequential.
            asyncio.run(_synthesize(voiceover, voice, mp3_path, vtt_path))
            if not mp3_path.exists() or mp3_path.stat().st_size == 0:
                raise RuntimeError("Produced empty audio file")
            logger.info("Voiceover saved to %s", mp3_path)
            return mp3_path, vtt_path
        except Exception as exc:  # noqa: BLE001 - fall back to the next voice
            logger.warning("TTS failed with voice %s: %s", voice, exc)

    raise RuntimeError("Text-to-speech failed with both primary and fallback voices")
