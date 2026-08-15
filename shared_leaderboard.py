import json
import os
import subprocess
import threading
import time
from pathlib import Path

LEADERBOARD_FILE = "shared_leaderboard.json"
_LOCK = threading.Lock()
RETRIES = 10


def _run(args):
    return subprocess.run(
        args,
        capture_output=True,
        text=True,
    )


def _branch():
    return os.getenv(
        "GITHUB_REF_NAME",
        "main",
    )


def _load_local():
    path = Path(LEADERBOARD_FILE)

    if not path.exists():
        return {}

    try:
        with path.open(
            "r",
            encoding="utf-8",
        ) as file:
            data = json.load(file)

        return data if isinstance(data, dict) else {}

    except Exception:
        return {}


def _write_local(scores):
    path = Path(LEADERBOARD_FILE)
    temp = path.with_suffix(".tmp")

    with temp.open(
        "w",
        encoding="utf-8",
    ) as file:
        json.dump(
            scores,
            file,
            indent=2,
            ensure_ascii=False,
            sort_keys=True,
        )
        file.write("\n")

    temp.replace(path)


def _reset_to_origin(branch):
    fetch = _run(
        [
            "git",
            "fetch",
            "origin",
            branch,
        ]
    )

    if fetch.returncode != 0:
        return False

    reset = _run(
        [
            "git",
            "reset",
            "--hard",
            f"origin/{branch}",
        ]
    )

    return reset.returncode == 0


def _remote_scores(branch):
    result = _run(
        [
            "git",
            "show",
            f"origin/{branch}:{LEADERBOARD_FILE}",
        ]
    )

    if result.returncode != 0:
        return {}

    try:
        data = json.loads(
            result.stdout
        )

        return data if isinstance(data, dict) else {}

    except Exception:
        return {}


def _configure_git():
    _run(
        [
            "git",
            "config",
            "user.name",
            "Shared Chatter Bot",
        ]
    )

    _run(
        [
            "git",
            "config",
            "user.email",
            "shared-chatter-bot@users.noreply.github.com",
        ]
    )


def _try_push(branch):
    _run(
        [
            "git",
            "add",
            LEADERBOARD_FILE,
        ]
    )

    commit = _run(
        [
            "git",
            "commit",
            "-m",
            "Update shared leaderboard",
        ]
    )

    # Even if commit says "nothing to commit", still try pushing.
    push = _run(
        [
            "git",
            "push",
            "origin",
            f"HEAD:{branch}",
        ]
    )

    return push.returncode == 0


def _transaction(
    user_id,
    display_name,
    amount,
):
    if float(amount) < 0:
        raise ValueError(
            "Negative leaderboard changes are not allowed."
        )

    branch = _branch()

    for attempt in range(
        RETRIES
    ):

        # Start every attempt from the newest remote commit.
        if not _reset_to_origin(
            branch
        ):
            time.sleep(1)
            continue

        scores = _remote_scores(
            branch
        )

        key = str(
            user_id
        )

        entry = scores.get(
            key,
            {},
        )

        old_points = float(
            entry.get(
                "points",
                0,
            )
        )

        new_total = round(
            old_points
            + float(amount),
            2,
        )

        scores[key] = {
            "name": display_name,
            "points": new_total,
        }

        _write_local(
            scores
        )

        _configure_git()

        if _try_push(
            branch
        ):
            return float(
                new_total
            )

        # Another Action changed origin between our fetch and push.
        # Wait briefly, then start again from the new remote state.
        time.sleep(
            min(
                2,
                0.25 * (attempt + 1)
            )
        )

    # Keep the local file correct rather than silently losing the point.
    raise RuntimeError(
        "Could not safely save the shared leaderboard."
    )


def add_points(
    user_id,
    display_name,
    amount,
):
    with _LOCK:
        return _transaction(
            user_id,
            display_name,
            amount,
        )


def get_score(
    user_id,
):
    with _LOCK:

        scores = _load_local()

        return float(
            scores.get(
                str(user_id),
                {},
            ).get(
                "points",
                0,
            )
        )


def format_points(
    value,
):
    value = float(
        value
    )

    if value.is_integer():
        return str(
            int(value)
        )

    return f"{value:.1f}"


def _ordered_scores(
    scores,
):
    return sorted(
        scores.items(),
        key=lambda item: (
            -float(
                item[1].get(
                    "points",
                    0,
                )
            ),
            str(
                item[1].get(
                    "name",
                    "Unknown",
                )
            ).casefold(),
        ),
    )


def personal_ranking(
    user_id,
):
    with _LOCK:
        scores = _load_local()

    ordered = _ordered_scores(
        scores
    )

    position = next(
        (
            index
            for index, (uid, _) in enumerate(
                ordered
            )
            if str(uid) == str(
                user_id
            )
        ),
        None,
    )

    if position is None:
        return ""

    start = max(
        0,
        position - 1,
    )

    end = min(
        len(ordered),
        position + 2,
    )

    lines = [
        "🏆 **Your ranking**"
    ]

    for index in range(
        start,
        end,
    ):
        uid, entry = ordered[index]

        points = float(
            entry.get(
                "points",
                0,
            )
        )

        word = (
            "point"
            if points == 1
            else "points"
        )

        name = entry.get(
            "name",
            "Unknown",
        )

        marker = (
            " ← you"
            if str(uid) == str(
                user_id
            )
            else ""
        )

        lines.append(
            f"**#{index + 1} {name} — "
            f"{format_points(points)} "
            f"{word}{marker}**"
        )

    return "\n".join(
        lines
    )


def full_leaderboard(
    title="🏆 **Shared Leaderboard**",
):
    with _LOCK:
        scores = _load_local()

    ordered = _ordered_scores(
        scores
    )

    if not ordered:
        return (
            f"{title}\n\n"
            "No points yet!"
        )

    lines = [
        title,
        "",
    ]

    for rank, (
        _,
        entry,
    ) in enumerate(
        ordered,
        start=1,
    ):
        points = float(
            entry.get(
                "points",
                0,
            )
        )

        word = (
            "point"
            if points == 1
            else "points"
        )

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
            f"**{format_points(points)} "
            f"{word}**"
        )

    return "\n".join(
        lines
    )
