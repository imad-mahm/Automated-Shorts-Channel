"""
Video assembly stage.

Builds the raw vertical Short with FFmpeg (driven directly via subprocess, no
wrapper library): a Ken Burns slideshow of the scene images, timed to the
voiceover length, with optional background music mixed quietly underneath.
"""

import json
import logging
import random
import subprocess
from pathlib import Path

import config

logger = logging.getLogger(__name__)

WIDTH = 1080
HEIGHT = 1920
FPS = 30


def _probe_duration(audio_path: Path) -> float:
    """
    Return the duration in seconds of an audio file using ffprobe.

    Inputs:  audio_path - path to the voiceover MP3.
    Output:  duration in seconds (float).
    Raises:  RuntimeError if ffprobe fails or returns no duration.
    """
    cmd = [
        config.FFPROBE,
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "json",
        str(audio_path.resolve()),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffprobe failed:\n{result.stderr}")
    try:
        duration = float(json.loads(result.stdout)["format"]["duration"])
    except (KeyError, ValueError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Could not read audio duration: {exc}") from exc
    logger.info("Voiceover duration: %.2fs", duration)
    return duration


def _pick_music() -> Path | None:
    """
    Pick a random MP3 from the music directory, or None if there are none.

    Inputs:  none (reads config.MUSIC_DIR).
    Output:  pathlib.Path to a music file, or None.
    """
    music_dir = Path(config.MUSIC_DIR)
    if not music_dir.exists():
        return None
    tracks = sorted(music_dir.glob("*.mp3"))
    if not tracks:
        return None
    choice = random.choice(tracks)
    logger.info("Background music: %s", choice.name)
    return choice


def assemble_video(image_paths: list[Path], voiceover_path: Path) -> Path:
    """
    Assemble the raw video from images and the voiceover.

    Inputs:
        image_paths:    ordered list of scene image paths.
        voiceover_path: path to the narration MP3.
    Output:
        pathlib.Path to output/video_raw.mp4.
    Raises:  RuntimeError if FFmpeg fails or no images are provided.
    """
    if not image_paths:
        raise RuntimeError("No images provided to video assembler")

    output_dir = Path(config.OUTPUT_DIR)
    output_dir.mkdir(parents=True, exist_ok=True)
    raw_path = output_dir / "video_raw.mp4"

    total_duration = _probe_duration(voiceover_path)
    num_scenes = len(image_paths)
    per_scene = total_duration / num_scenes
    frames_per_scene = max(1, round(per_scene * FPS))

    cmd: list[str] = [config.FFMPEG, "-y"]

    # Each image is supplied WITHOUT -loop, so FFmpeg reads exactly one frame.
    # zoompan then expands that single frame into `frames_per_scene` output
    # frames. Feeding a single frame is the reliable way to drive zoompan: with
    # a looped multi-frame input the zoom expression restarts every input frame
    # and produces a stuttering "sawtooth" zoom.
    for img in image_paths:
        cmd += ["-i", str(Path(img).resolve())]

    voice_index = num_scenes
    cmd += ["-i", str(Path(voiceover_path).resolve())]

    music_path = _pick_music()
    music_index = None
    if music_path is not None:
        # -stream_loop -1 loops the track to cover the whole video if it is short.
        music_index = num_scenes + 1
        cmd += ["-stream_loop", "-1", "-i", str(music_path.resolve())]

    # ----------------------------------------------------------------------- #
    # Build the filter graph.
    # ----------------------------------------------------------------------- #
    filters: list[str] = []
    for i in range(num_scenes):
        filters.append(
            f"[{i}:v]"
            f"scale={WIDTH}:{HEIGHT}:force_original_aspect_ratio=increase,"
            f"crop={WIDTH}:{HEIGHT},"
            f"zoompan=z='min(zoom+0.0008,1.5)':"
            f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
            f"d={frames_per_scene}:s={WIDTH}x{HEIGHT}:fps={FPS},"
            f"setsar=1[v{i}]"
        )

    concat_inputs = "".join(f"[v{i}]" for i in range(num_scenes))
    filters.append(f"{concat_inputs}concat=n={num_scenes}:v=1:a=0[outv]")

    if music_index is not None:
        # Mix voiceover (weight 1) with music (weight 0.15) so narration stays
        # dominant. normalize=0 stops amix from auto-attenuating the louder voice.
        filters.append(
            f"[{voice_index}:a][{music_index}:a]"
            f"amix=inputs=2:duration=first:weights=1 0.15:normalize=0[outa]"
        )
        audio_map = "[outa]"
    else:
        logger.info("No background music found; using voiceover only")
        audio_map = f"{voice_index}:a"

    cmd += [
        "-filter_complex",
        ";".join(filters),
        "-map",
        "[outv]",
        "-map",
        audio_map,
        "-r",
        str(FPS),
        "-c:v",
        "libx264",
        "-crf",
        "23",
        "-preset",
        "medium",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        # Hard-cap the output to the voiceover length so looped music/zoompan
        # tails are trimmed cleanly.
        "-t",
        f"{total_duration:.3f}",
        str(raw_path.resolve()),
    ]

    logger.debug("FFmpeg assembly command: %s", " ".join(cmd))
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"FFmpeg assembly failed:\n{result.stderr}")

    logger.info("Raw video saved to %s", raw_path)
    return raw_path
