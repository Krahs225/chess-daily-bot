import json
import os
import subprocess
import threading
from pathlib import Path

LEADERBOARD_FILE = "shared_leaderboard.json"
_LOCK = threading.Lock()


def _load_unlocked():
    path = Path(LEADERBOARD_FILE)
    if not path.exists():
        return {}

    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception as exc:
        print(f"[shared leaderboard] load failed: {exc}", flush=True)
        return {}


def _save_unlocked(data):
    with Path(LEADERBOARD_FILE).open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def _git_sync():
    """
    Best-effort GitHub persistence.
    The local file is always updated first.
    """
    try:
        subprocess.run(
            ["git", "config", "user.name", "Shared Chatter Bot"],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            [
                "git",
                "config",
                "user.email",
                "shared-chatter-bot@users.noreply.github.com",
            ],
            check=True,
            capture_output=True,
        )

        branch = os.getenv("GITHUB_REF_NAME", "main")

        pull = subprocess.run(
            ["git", "pull", "--rebase", "origin", branch],
            capture_output=True,
            text=True,
        )
        if pull.returncode != 0:
            print(
                "[shared leaderboard] git pull failed; "
                "keeping local score.",
                flush=True,
            )
            return

        subprocess.run(
            ["git", "add", LEADERBOARD_FILE],
            check=True,
            capture_output=True,
        )

        commit = subprocess.run(
            ["git", "commit", "-m", "Update shared chatter leaderboard"],
            capture_output=True,
            text=True,
        )

        if commit.returncode != 0:
            return

        push = subprocess.run(
            ["git", "push", "origin", f"HEAD:{branch}"],
            capture_output=True,
            text=True,
        )

        if push.returncode != 0:
            print(
                "[shared leaderboard] git push failed:",
                push.stderr[-1000:],
                flush=True,
            )
    except Exception as exc:
        print(f"[shared leaderboard] git sync failed: {exc}", flush=True)


def add_points(user_id, display_name, amount):
    """
    Add points and return the user's new total.
    """
    with _LOCK:
        scores = _load_unlocked()
        key = str(user_id)

        entry = scores.setdefault(
            key,
            {
                "name": display_name,
                "points": 0,
            },
        )

        entry["name"] = display_name
        entry["points"] = round(
            float(entry.get("points", 0)) + float(amount),
            2,
        )

        _save_unlocked(scores)

        # Persist through the current Action checkout.
        _git_sync()

        return float(entry["points"])


def get_score(user_id):
    with _LOCK:
        scores = _load_unlocked()
        return float(
            scores.get(str(user_id), {}).get("points", 0)
        )


def format_points(value):
    value = float(value)
    return str(int(value)) if value.is_integer() else f"{value:.1f}"


def personal_ranking(user_id):
    with _LOCK:
        scores = _load_unlocked()

    ordered = sorted(
        scores.items(),
        key=lambda item: (
            -float(item[1].get("points", 0)),
            str(item[1].get("name", "Unknown")).casefold(),
        ),
    )

    index = next(
        (
            i
            for i, (uid, _) in enumerate(ordered)
            if str(uid) == str(user_id)
        ),
        None,
    )

    if index is None:
        return ""

    start = max(0, index - 1)
    end = min(len(ordered), index + 2)

    lines = ["", "📊 **Your ranking**"]
    for rank_idx in range(start, end):
        uid, entry = ordered[rank_idx]
        name = entry.get("name", "Unknown")
        pts = format_points(entry.get("points", 0))
        word = "point" if float(entry.get("points", 0)) == 1 else "points"

        if str(uid) == str(user_id):
            lines.append(
                f"**#{rank_idx + 1} {name} — "
                f"{pts} {word} ← you**"
            )
        else:
            lines.append(
                f"#{rank_idx + 1} {name} — {pts} {word}"
            )

    return "\n".join(lines)


def full_leaderboard(title="🏆 **Shared Leaderboard**"):
    with _LOCK:
        scores = _load_unlocked()

    if not scores:
        return f"{title}\n\nNo points yet!"

    ordered = sorted(
        scores.items(),
        key=lambda item: (
            -float(item[1].get("points", 0)),
            str(item[1].get("name", "Unknown")).casefold(),
        ),
    )

    lines = [title, ""]
    for rank, (_, entry) in enumerate(ordered, start=1):
        pts_value = float(entry.get("points", 0))
        pts = format_points(pts_value)
        word = "point" if pts_value == 1 else "points"

        if rank == 1:
            prefix = "🥇"
        elif rank == 2:
            prefix = "🥈"
        elif rank == 3:
            prefix = "🥉"
        else:
            prefix = f"**{rank}.**"

        lines.append(
            f"{prefix} {entry.get('name', 'Unknown')} — "
            f"**{pts} {word}**"
        )

    return "\n".join(lines)
