"""
Mythology YouTube Shorts — daily pipeline
Runs all steps in sequence, logs progress, cleans up temp files on exit.
"""

import logging
import os
import shutil
import sys
import traceback
from pathlib import Path

import config
from pipeline import (
    captioner,
    image_generator,
    script_generator,
    topic_queue,
    tts,
    uploader,
    video_assembler,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("mythology-shorts")


def _prepare_output_dir() -> Path:
    """
    Create a fresh, empty output directory.

    Inputs:  none (uses config.OUTPUT_DIR).
    Output:  pathlib.Path to the output directory.
    """
    output_dir = Path(config.OUTPUT_DIR)
    if output_dir.exists():
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    return output_dir


def _cleanup_output_dir() -> None:
    """Remove the output directory and all temp media (best effort)."""
    output_dir = Path(config.OUTPUT_DIR)
    try:
        if output_dir.exists():
            shutil.rmtree(output_dir)
            logger.info("Cleaned up %s", output_dir)
    except OSError as exc:  # pragma: no cover - cleanup must never crash the run
        logger.warning("Could not clean up output dir: %s", exc)


def _run_step(name: str, func, *args):
    """
    Execute one pipeline step with consistent logging and error wrapping.

    Inputs:
        name: human-readable step name (for logs).
        func: the callable to run.
        args: positional arguments forwarded to func.
    Output:  whatever func returns.
    Raises:  re-raises any exception after logging the step + traceback.
    """
    logger.info("=== STEP: %s ===", name)
    try:
        return func(*args)
    except Exception:
        logger.error("Step failed: %s", name)
        logger.error(traceback.format_exc())
        raise


def main() -> int:
    """
    Run the full pipeline end to end.

    Output:  process exit code (0 on success, 1 on any failure).
    """
    try:
        _run_step("validate config", config.validate)
    except Exception:
        # Without secrets we can't do anything; fail fast.
        return 1

    _prepare_output_dir()

    # Decide the topic. Precedence:
    #   1. TOPIC env var  — ad-hoc request (e.g. the Actions "Run workflow" input)
    #   2. front of the queue file — managed by the web interface
    #   3. None — pick a random subject (the default daily behaviour)
    # A queued topic is only removed AFTER a successful upload, so a failed run
    # leaves it in place to be retried next time.
    forced_topic = (os.environ.get("TOPIC") or "").strip() or None
    queued_topic = None if forced_topic else topic_queue.peek_next()
    chosen_topic = forced_topic or queued_topic
    if chosen_topic:
        source = "manual request" if forced_topic else "queue"
        logger.info("Requested topic (%s): %s", source, chosen_topic)

    try:
        script_data = _run_step(
            "generate script", script_generator.generate_script, chosen_topic
        )
        logger.info(
            "Topic: %s — %s",
            script_data.get("mythology_type"),
            script_data.get("subject"),
        )

        image_paths = _run_step(
            "generate images", image_generator.generate_images, script_data["scenes"]
        )

        mp3_path, vtt_path = _run_step(
            "generate TTS", tts.generate_tts, script_data["voiceover"]
        )

        raw_video = _run_step(
            "assemble video", video_assembler.assemble_video, image_paths, mp3_path
        )

        final_video = _run_step(
            "burn captions", captioner.burn_captions, raw_video, vtt_path
        )

        thumbnail = image_paths[0] if image_paths else None
        video_url = _run_step(
            "upload to YouTube",
            uploader.upload_video,
            final_video,
            script_data,
            thumbnail,
        )

        # Only now that the upload succeeded do we consume the queued topic.
        if queued_topic:
            topic_queue.remove_first(queued_topic)
            logger.info("Consumed topic from queue: %s", queued_topic)

        logger.info("=" * 60)
        logger.info("SUCCESS")
        logger.info("Mythology : %s", script_data.get("mythology_type"))
        logger.info("Subject   : %s", script_data.get("subject"))
        logger.info("Title     : %s", script_data.get("title"))
        logger.info("URL       : %s", video_url)
        logger.info("=" * 60)
        return 0

    except Exception:
        # Each step already logged its own traceback via _run_step.
        logger.error("Pipeline failed — see traceback above.")
        return 1
    finally:
        _cleanup_output_dir()


if __name__ == "__main__":
    sys.exit(main())
