import json
import os
import subprocess
import threading
import time
from pathlib import Path

LEADERBOARD_FILE = "shared_leaderboard.json"
_LOCK = threading.Lock()
MAX_RETRIES = 10


def _run(args):
    return subprocess.run(
        args,
        capture_output=True,
        text=True,
    )


def _branch():
    return os.getenv("GITHUB_REF_NAME", "main")


def _load_local():
    path = Path(LEADERBOARD_FILE)
    if not path.exists():
        return {}

    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception as error:
        print(f"[leaderboard] local read failed: {error}", flush=True)
        return {}


def _write_local(scores):
    path = Path(LEADERBOARD_FILE)
    tmp = path.with_suffix(".tmp")

    with tmp.open("w", encoding="utf-8") as f:
        json.dump(
            scores,
            f,
            indent=2,
            ensure_ascii=False,
            sort_keys=True,
        )
        f.write("\n")

    tmp.replace(path)


def _configure_git():
    _run(["git", "config", "user.name", "Shared Chatter Bot"])
    _run([
        "git",
        "config",
        "user.email",
        "shared-chatter-bot@users.noreply.github.com",
    ])


def _fetch_origin():
    branch = _branch()
    result = _run(["git", "fetch", "origin", branch])

    if result.returncode != 0:
        print(
            "[leaderboard] git fetch failed: "
            + result.stderr[-1000:],
            flush=True,
        )
        return False

    return True


def _remote_scores():
    branch = _branch()

    result = _run([
        "git",
        "show",
        f"origin/{branch}:{LEADERBOARD_FILE}",
    ])

    if result.returncode != 0:
        return {}

    try:
        data = json.loads(result.stdout)
        return data if isinstance(data, dict) else {}
    except Exception as error:
        print(
            f"[leaderboard] remote JSON failed: {error}",
            flush=True,
        )
        return {}


def _reset_to_origin():
    branch = _branch()

    result = _run([
        "git",
        "reset",
        "--hard",
        f"origin/{branch}",
    ])

    if result.returncode != 0:
        print(
            "[leaderboard] git reset failed: "
            + result.stderr[-1000:],
            flush=True,
        )
        return False

    return True


def _commit_push():
    branch = _branch()

    _configure_git()

    add = _run([
        "git",
        "add",
        LEADERBOARD_FILE,
    ])

    if add.returncode != 0:
        return False

    commit = _run([
        "git",
        "commit",
        "-m",
        "Update shared leaderboard",
    ])

    if commit.returncode != 0:
        return False

    push = _run([
        "git",
        "push",
        "origin",
        f"HEAD:{branch}",
    ])

    return push.returncode == 0


def _add_points_transaction(
    user_id,
    display_name,
    amount,
):
    amount = float(amount)

    if amount < 0:
        raise ValueError(
            "Negative point changes are not allowed."
        )

    local_fallback = _load_local()

    for attempt in range(1, MAX_RETRIES + 1):

        if not _fetch_origin():
            time.sleep(min(2, attempt * 0.25))
            continue

        remote = _remote_scores()

        if not _reset_to_origin():
            time.sleep(min(2, attempt * 0.25))
            continue

        # Only use the local copy when the remote leaderboard is truly empty.
        if not remote and local_fallback:
            scores = dict(local_fallback)
        else:
            scores = dict(remote)

        key = str(user_id)
        entry = scores.get(key, {})

        old_points = float(
            entry.get("points", 0)
        )

        new_points = round(
            old_points + amount,
            2,
        )

        scores[key] = {
            "name": display_name,
            "points": new_points,
        }

        _write_local(scores)

        if _commit_push():
            return float(new_points)

        # Another bot pushed between our fetch and push.
        time.sleep(min(2, attempt * 0.25))

    raise RuntimeError(
        "Could not safely update shared leaderboard."
    )


def add_points(user_id, display_name, amount):
    with _LOCK:
        return _add_points_transaction(
            user_id,
            display_name,
            amount,
        )


def _current_scores():
    local = _load_local()

    if not _fetch_origin():
        return local

    remote = _remote_scores()

    _write_local(remote)

    return remote


def get_score(user_id):
    with _LOCK:
        scores = _current_scores()

    return float(
        scores.get(
            str(user_id),
            {},
        ).get(
            "points",
            0,
        )
    )


def format_points(value):
    value = float(value)
    return (
        str(int(value))
        if value.is_integer()
        else f"{value:.1f}"
    )


def _ordered(scores):
    return sorted(
        scores.items(),
        key=lambda item: (
            -float(item[1].get("points", 0)),
            str(
                item[1].get("name", "Unknown")
            ).casefold(),
        ),
    )


def personal_ranking(user_id):
    with _LOCK:
        scores = _current_scores()

    ordered = _ordered(scores)

    position = next(
        (
            i
            for i, (uid, _) in enumerate(ordered)
            if str(uid) == str(user_id)
        ),
        None,
    )

    if position is None:
        return ""

    start = max(0, position - 1)
    end = min(len(ordered), position + 2)

    lines = ["🏆 **Your ranking**"]

    for i in range(start, end):
        uid, entry = ordered[i]
        points = float(entry.get("points", 0))
        word = "point" if points == 1 else "points"
        marker = " ← you" if str(uid) == str(user_id) else ""

        lines.append(
            f"**#{i + 1} "
            f"{entry.get('name', 'Unknown')} — "
            f"{format_points(points)} "
            f"{word}{marker}**"
        )

    return "\n".join(lines)


def full_leaderboard(
    title="🏆 **Shared Leaderboard**",
):
    with _LOCK:
        scores = _current_scores()

    ordered = _ordered(scores)

    if not ordered:
        return f"{title}\n\nNo points yet!"

    lines = [title, ""]

    for rank, (_, entry) in enumerate(
        ordered,
        start=1,
    ):
        points = float(entry.get("points", 0))
        word = "point" if points == 1 else "points"

        if rank == 1:
            prefix = "🥇"
        elif rank == 2:
            prefix = "🥈"
        elif rank == 3:
            prefix = "🥉"
        else:
            prefix = f"**{rank}.**"

        lines.append(
            f"{prefix} "
            f"{entry.get('name', 'Unknown')} — "
            f"**{format_points(points)} {word}**"
        )

    return "\n".join(lines)
