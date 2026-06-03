"""
Topic queue web interface.

A small local Flask app for managing the mythology-shorts topic queue
(topics_queue.json). You can add, remove and reorder topics, push the queue to
GitHub so the daily Action picks it up, and optionally trigger a run now.

Run it from anywhere:

    pip install -r webapp/requirements.txt
    python webapp/app.py

then open http://127.0.0.1:5000 in your browser.

Optional environment variables (for the buttons that talk to GitHub):
    GITHUB_REPO   e.g. "yourname/mythology-shorts"  (enables "Generate now")
    GITHUB_TOKEN  a fine-grained PAT with "actions: write" on that repo
"""

import os
import subprocess
import sys
from pathlib import Path

import requests
from flask import Flask, redirect, render_template, request, url_for

# The repo root is the parent of this webapp/ directory. We make every path
# resolve against it (and run git there) regardless of where the app is started.
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
os.chdir(REPO_ROOT)

from pipeline import topic_queue  # noqa: E402 - import after sys.path/chdir setup

app = Flask(__name__)

WORKFLOW_FILE = "daily_upload.yml"


def _git(*args: str) -> tuple[bool, str]:
    """
    Run a git command in the repo root.

    Output:  (success, combined stdout+stderr text).
    """
    result = subprocess.run(
        ["git", *args], cwd=str(REPO_ROOT), capture_output=True, text=True
    )
    return result.returncode == 0, (result.stdout + result.stderr).strip()


@app.route("/")
def index():
    """Render the queue with add/remove/reorder controls."""
    return render_template(
        "index.html",
        topics=topic_queue.load_queue(),
        message=request.args.get("msg", ""),
        github_repo=os.environ.get("GITHUB_REPO", ""),
        can_trigger=bool(os.environ.get("GITHUB_REPO") and os.environ.get("GITHUB_TOKEN")),
    )


@app.route("/add", methods=["POST"])
def add():
    """Add a topic to the back of the queue."""
    topic_queue.add_topic(request.form.get("topic", ""))
    return redirect(url_for("index"))


@app.route("/remove", methods=["POST"])
def remove():
    """Remove the topic at the given index."""
    topic_queue.remove_index(int(request.form["index"]))
    return redirect(url_for("index"))


@app.route("/move", methods=["POST"])
def move():
    """Move a topic up (delta -1) or down (delta +1)."""
    topic_queue.move(int(request.form["index"]), int(request.form["delta"]))
    return redirect(url_for("index"))


@app.route("/push", methods=["POST"])
def push():
    """Commit topics_queue.json and push it so GitHub Actions sees the queue."""
    is_repo, _ = _git("rev-parse", "--is-inside-work-tree")
    if not is_repo:
        return redirect(url_for("index", msg="Not a git repo — run 'git init' and add a remote first."))

    _git("add", "topics_queue.json")
    committed, out = _git("commit", "-m", "chore: update topic queue")
    if not committed and "nothing to commit" in out:
        return redirect(url_for("index", msg="Nothing to push — queue already committed."))

    repo = (os.environ.get("GITHUB_REPO") or "").strip()
    token = (os.environ.get("GITHUB_TOKEN") or "").strip()
    if repo and token:
        # Authenticate with the env token transiently in the push URL so it is
        # never written to .git/config. Push the current HEAD to main.
        push_url = f"https://x-access-token:{token}@github.com/{repo}.git"
        pushed, push_out = _git("push", push_url, "HEAD:main")
        push_out = push_out.replace(token, "***")  # never surface the token
    else:
        # Fall back to git's own credential handling if env vars aren't set.
        pushed, push_out = _git("push")
    msg = "Pushed queue to GitHub." if pushed else f"Push failed: {push_out[:200]}"
    return redirect(url_for("index", msg=msg))


@app.route("/trigger", methods=["POST"])
def trigger():
    """
    Trigger the daily workflow now via the GitHub API (workflow_dispatch),
    optionally for a specific one-off topic typed into the trigger box.
    """
    repo = os.environ.get("GITHUB_REPO")
    token = os.environ.get("GITHUB_TOKEN")
    if not (repo and token):
        return redirect(url_for("index", msg="Set GITHUB_REPO and GITHUB_TOKEN to enable 'Generate now'."))

    one_off = (request.form.get("topic") or "").strip()
    payload = {"ref": "main"}
    if one_off:
        payload["inputs"] = {"topic": one_off}

    resp = requests.post(
        f"https://api.github.com/repos/{repo}/actions/workflows/{WORKFLOW_FILE}/dispatches",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
        },
        json=payload,
        timeout=30,
    )
    if resp.status_code == 204:
        msg = "Triggered a run" + (f" for: {one_off}" if one_off else " (random/queue).")
    else:
        msg = f"Trigger failed ({resp.status_code}): {resp.text[:200]}"
    return redirect(url_for("index", msg=msg))


if __name__ == "__main__":
    # Local-only admin tool; debug reloader off to avoid double chdir.
    app.run(host="127.0.0.1", port=5000, debug=False)
