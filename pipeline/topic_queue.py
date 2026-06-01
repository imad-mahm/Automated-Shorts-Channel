"""
Topic queue.

A tiny JSON-backed FIFO of requested video topics, shared between the web
interface (which adds/removes/reorders topics) and the pipeline (which consumes
the front of the queue each run). The file is plain JSON so it is easy to edit
by hand, diff in git, and commit back from GitHub Actions.

On-disk format is a JSON array of strings, e.g.:
    ["Norse: Fenrir at Ragnarok", "Greek: the real story of Medusa"]
A legacy/object form {"queue": [...]} is also accepted on read.
"""

import json
import logging
from pathlib import Path

import config

logger = logging.getLogger(__name__)


def _path() -> Path:
    """Return the queue file path (relative to the current working directory)."""
    return Path(config.QUEUE_FILE)


def load_queue() -> list[str]:
    """
    Load the topic queue.

    Output:  list of non-empty topic strings (empty list if file missing/invalid).
    """
    path = _path()
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        logger.warning("Queue file %s is not valid JSON; treating as empty", path)
        return []
    if isinstance(data, dict):
        data = data.get("queue", [])
    if not isinstance(data, list):
        return []
    return [str(t).strip() for t in data if str(t).strip()]


def save_queue(topics: list[str]) -> None:
    """
    Write the topic queue to disk.

    Inputs:  topics - ordered list of topic strings.
    Output:  None.
    """
    cleaned = [str(t).strip() for t in topics if str(t).strip()]
    _path().write_text(
        json.dumps(cleaned, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )


def peek_next() -> str | None:
    """Return the front topic without removing it, or None if the queue is empty."""
    queue = load_queue()
    return queue[0] if queue else None


def add_topic(topic: str) -> list[str]:
    """
    Append a topic to the back of the queue.

    Inputs:  topic - the topic string (blank values are ignored).
    Output:  the updated queue.
    """
    topic = topic.strip()
    queue = load_queue()
    if topic:
        queue.append(topic)
        save_queue(queue)
    return queue


def remove_first(topic: str) -> list[str]:
    """
    Remove the first occurrence of `topic` from the queue.

    Used after a successful upload to consume the topic that was generated.

    Inputs:  topic - the topic string to remove.
    Output:  the updated queue.
    """
    queue = load_queue()
    for i, existing in enumerate(queue):
        if existing == topic:
            queue.pop(i)
            break
    save_queue(queue)
    return queue


def remove_index(index: int) -> list[str]:
    """Remove the topic at the given position (no-op if out of range)."""
    queue = load_queue()
    if 0 <= index < len(queue):
        queue.pop(index)
        save_queue(queue)
    return queue


def move(index: int, delta: int) -> list[str]:
    """
    Move the topic at `index` by `delta` positions (e.g. -1 up, +1 down).

    Inputs:  index - current position; delta - signed offset.
    Output:  the updated queue.
    """
    queue = load_queue()
    new_index = index + delta
    if 0 <= index < len(queue) and 0 <= new_index < len(queue):
        queue[index], queue[new_index] = queue[new_index], queue[index]
        save_queue(queue)
    return queue
