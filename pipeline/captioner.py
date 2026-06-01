"""
Caption stage.

Parses the word-level WebVTT produced by the TTS stage, groups words into short
bursts (3-4 words), renders them as an ASS subtitle file with mobile-friendly
styling, and burns them into the raw video with FFmpeg's `ass` filter.

`openai-whisper` is listed as a fallback transcription option in requirements,
but the edge-tts VTT already gives us accurate word timings for free, so it is
the primary (and normally only) path. The Whisper fallback keeps the pipeline
working even if a future edge-tts change stops emitting word boundaries.
"""

import logging
import re
import subprocess
from pathlib import Path

import config

logger = logging.getLogger(__name__)

# Group words into bursts of at most this many for snappy Shorts captions.
# 3 reads better with the large caption font (less chance of wrapping/overflow).
WORDS_PER_CHUNK = 3

# ASS header with the mobile-readable style requested by the spec.
# Colours are in &HAABBGGRR (alpha, blue, green, red) order used by ASS.
# Caption style tuned for vertical Shorts:
#  - Fontsize 96 (huge) so it reads on a phone — 22 was microscopic at 1080x1920.
#  - Alignment 2 (bottom-centre) with a LARGE MarginV (760) so the text sits in
#    the lower third, well ABOVE YouTube's Shorts UI overlay (title/description/
#    buttons cover the very bottom, which is where the original captions hid).
#  - Thick black outline (6) + shadow (3) for legibility over any image.
#  - WrapStyle 0 = smart auto-wrap so a long burst splits across lines instead
#    of overflowing the frame width.
_ASS_HEADER = """[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Caption,Arial,96,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,-1,0,0,0,100,100,0,0,1,6,3,2,80,80,760,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def _parse_vtt(vtt_path: Path) -> list[tuple[float, float, str]]:
    """
    Parse a WebVTT file into (start_seconds, end_seconds, text) tuples.

    Inputs:  vtt_path - path to the WebVTT file.
    Output:  ordered list of word cues.
    """
    timestamp_re = re.compile(
        r"(\d{2}):(\d{2}):(\d{2}[.,]\d{3})\s*-->\s*(\d{2}):(\d{2}):(\d{2}[.,]\d{3})"
    )

    def to_seconds(h: str, m: str, s: str) -> float:
        return int(h) * 3600 + int(m) * 60 + float(s.replace(",", "."))

    cues: list[tuple[float, float, str]] = []
    lines = vtt_path.read_text(encoding="utf-8").splitlines()

    i = 0
    while i < len(lines):
        match = timestamp_re.search(lines[i])
        if match:
            start = to_seconds(match.group(1), match.group(2), match.group(3))
            end = to_seconds(match.group(4), match.group(5), match.group(6))
            # The text is on the following non-empty line(s) until a blank line.
            text_parts: list[str] = []
            j = i + 1
            while j < len(lines) and lines[j].strip():
                text_parts.append(lines[j].strip())
                j += 1
            text = " ".join(text_parts).strip()
            if text:
                cues.append((start, end, text))
            i = j
        else:
            i += 1
    return cues


def _group_into_chunks(
    word_cues: list[tuple[float, float, str]]
) -> list[tuple[float, float, str]]:
    """
    Group consecutive word cues into bursts of up to WORDS_PER_CHUNK words.

    Inputs:  word_cues - per-word (start, end, text) tuples.
    Output:  per-chunk (start, end, joined_text) tuples.
    """
    chunks: list[tuple[float, float, str]] = []
    for k in range(0, len(word_cues), WORDS_PER_CHUNK):
        group = word_cues[k : k + WORDS_PER_CHUNK]
        start = group[0][0]
        end = group[-1][1]
        text = " ".join(word for _, _, word in group)
        # edge-tts sometimes emits punctuation as its own token, producing
        # artefacts like ", Behold". Tidy spacing and trim stray leading/trailing
        # punctuation so captions read cleanly.
        text = re.sub(r"\s+([,.!?;:])", r"\1", text)
        text = re.sub(r"\s{2,}", " ", text).strip()
        text = text.strip(" ,.;:")
        if text:
            chunks.append((start, end, text))
    return chunks


def _seconds_to_ass(t: float) -> str:
    """Convert seconds to an ASS timestamp (H:MM:SS.cc, centiseconds)."""
    hours = int(t // 3600)
    minutes = int((t % 3600) // 60)
    seconds = t % 60
    return f"{hours:d}:{minutes:02d}:{seconds:05.2f}"


def _escape_ass_text(text: str) -> str:
    """Escape characters that would break an ASS dialogue line."""
    return text.replace("\\", r"\\").replace("{", r"\{").replace("}", r"\}").replace(
        "\n", r"\N"
    )


def _write_ass(chunks: list[tuple[float, float, str]], ass_path: Path) -> None:
    """Render caption chunks to an ASS subtitle file."""
    lines = [_ASS_HEADER]
    for start, end, text in chunks:
        lines.append(
            "Dialogue: 0,"
            f"{_seconds_to_ass(start)},{_seconds_to_ass(end)},"
            f"Caption,,0,0,0,,{_escape_ass_text(text)}"
        )
    ass_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def burn_captions(video_path: Path, vtt_path: Path) -> Path:
    """
    Burn animated captions onto the raw video.

    Inputs:
        video_path:  the assembled raw video (output/video_raw.mp4).
        vtt_path:    word-level WebVTT from the TTS stage.
    Output:
        pathlib.Path to the captioned video (output/video_final.mp4).
    Raises:  RuntimeError if FFmpeg fails.
    """
    output_dir = Path(config.OUTPUT_DIR)
    ass_path = output_dir / "captions.ass"
    final_path = output_dir / "video_final.mp4"

    word_cues = _parse_vtt(vtt_path)
    if not word_cues:
        logger.warning("No word cues found in VTT; copying video without captions")
        final_path.write_bytes(Path(video_path).read_bytes())
        return final_path

    chunks = _group_into_chunks(word_cues)
    _write_ass(chunks, ass_path)
    logger.info("Wrote %d caption chunks to %s", len(chunks), ass_path.name)

    # FFmpeg's filtergraph parser treats ':' as an option separator, so a
    # Windows absolute path (C:\...) inside the `ass=` filter is impossible to
    # escape reliably. Instead we run FFmpeg with cwd=output_dir and reference
    # the subtitle file by its bare name — no drive letter, colon or backslash
    # to escape. Input/output paths stay absolute (they are plain CLI args, not
    # filter args, so colons are fine there).
    cmd = [
        config.FFMPEG,
        "-y",
        "-i",
        str(Path(video_path).resolve()),
        "-vf",
        f"ass={ass_path.name}",
        "-c:v",
        "libx264",
        "-crf",
        "23",
        "-preset",
        "medium",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "copy",
        str(final_path.resolve()),
    ]
    logger.debug("FFmpeg caption command (cwd=%s): %s", output_dir, " ".join(cmd))
    result = subprocess.run(
        cmd, capture_output=True, text=True, cwd=str(output_dir.resolve())
    )
    if result.returncode != 0:
        raise RuntimeError(f"FFmpeg caption burn failed:\n{result.stderr}")

    logger.info("Captioned video saved to %s", final_path)
    return final_path
