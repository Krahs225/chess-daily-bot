import json
import os
import subprocess
import threading
import time

LEADERBOARD_FILE = "shared_leaderboard.json"

# Only protects concurrent calls inside ONE bot process.
_LOCAL_LOCK = threading.Lock()

PUSH_RETRIES = 8


def _run_git(args, check=False):
    return subprocess.run(
        args,
        capture_output=True,
        text=True,
        check=check,
    )


def _branch():
    return os.getenv(
        "GITHUB_REF_NAME",
        "main",
    )


def _read_json_file():
    path = Path(
        LEADERBOARD_FILE
    )

    if not path.exists():
        return {}

    try:
        with path.open(
            "r",
            encoding="utf-8",
        ) as file:
            data = json.load(file)

        return data if isinstance(data, dict) else {}

    except Exception as error:
        print(
            f"[shared leaderboard] local read error: {error}",
            flush=True,
        )
        return {}


def _write_json_file(scores):
    path = Path(
        LEADERBOARD_FILE
    )

    temp = path.with_suffix(
        ".json.tmp"
    )

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

    temp.replace(
        path
    )


def _read_remote_scores():
    """
    Read the current leaderboard directly from origin without
    changing the working tree.
    """
    branch = _branch()

    result = _run_git(
        [
            "git",
            "fetch",
            "origin",
            branch,
        ]
    )

    if result.returncode != 0:
        print(
            "[shared leaderboard] git fetch failed:",
            result.stderr[-1000:],
            flush=True,
        )
        return None

    result = _run_git(
        [
            "git",
            "show",
            f"origin/{branch}:{LEADERBOARD_FILE}",
        ]
    )

    if result.returncode != 0:
        # File may not exist on origin yet.
        return {}

    try:
        data = json.loads(
            result.stdout
        )
        return data if isinstance(data, dict) else {}
    except Exception as error:
        print(
            f"[shared leaderboard] remote JSON error: {error}",
            flush=True,
        )
        return {}


def _set_git_identity():
    _run_git(
        [
            "git",
            "config",
            "user.name",
            "Shared Chatter Bot",
        ],
    )

    _run_git(
        [
            "git",
            "config",
            "user.email",
            "shared-chatter-bot@users.noreply.github.com",
        ],
    )


def _commit_push():
    """
    Push the current local leaderboard.

    Returns:
      True  = push succeeded or nothing needed pushing
      False = push failed
    """
    branch = _branch()

    for _ in range(
        PUSH_RETRIES
    ):
        _set_git_identity()

        _run_git(
            [
                "git",
                "add",
                LEADERBOARD_FILE,
            ]
        )

        commit = _run_git(
            [
                "git",
                "commit",
                "-m",
                "Update shared leaderboard",
            ]
        )

        if commit.returncode != 0:
            # Usually: nothing to commit.
            return True

        push = _run_git(
            [
                "git",
                "push",
                "origin",
                f"HEAD:{branch}",
            ]
        )

        if push.returncode == 0:
            return True

        # Another Action pushed first.
        # Rebase our commit on top of the new remote.
        fetch = _run_git(
            [
                "git",
                "fetch",
                "origin",
                branch,
            ]
        )

        if fetch.returncode != 0:
            time.sleep(0.5)
            continue

        rebase = _run_git(
            [
                "git",
                "rebase",
                f"origin/{branch}",
            ]
        )

        if rebase.returncode == 0:
            continue

        # The leaderboard is JSON and should not normally conflict
        # when the transaction logic below is used. If an old conflict
        # does happen, abort safely and let the transaction retry from
        # the latest remote data.
        _run_git(
            [
                "git",
                "rebase",
                "--abort",
            ]
        )

        time.sleep(0.5)

    return False


def _merge_increment(
    remote_scores,
    user_id,
    display_name,
    amount,
):
    """
    Apply ONE point transaction to the latest remote snapshot.
    Points never decrease.
    """
    scores = dict(
        remote_scores or {}
    )

    key = str(
        user_id
    )

    old_entry = scores.get(
        key,
        {},
    )

    old_points = float(
        old_entry.get(
            "points",
            0,
        )
    )

    increment = float(
        amount
    )

    if increment < 0:
        raise ValueError(
            "Negative point changes are not allowed."
        )

    new_points = round(
        old_points + increment,
        2,
    )

    scores[key] = {
        "name": display_name,
        "points": new_points,
    }

    return scores, new_points


def add_points(
    user_id,
    display_name,
    amount,
):
    """
    Atomically add points to the shared leaderboard across separate
    GitHub Actions.

    If another bot pushes between our read and push, the transaction
    retries from the newest remote score and applies the increment
    again. This prevents a +1 from being lost or an older score from
    overwriting a newer score.
    """
    with _LOCAL_LOCK:

        last_error = None

        for _ in range(
            PUSH_RETRIES
        ):
            remote_scores = _read_remote_scores()

            if remote_scores is None:
                # Temporary fetch failure: fall back to the local
                # snapshot, but still retry the push.
                remote_scores = _read_json_file()

            new_scores, new_total = _merge_increment(
                remote_scores,
                user_id,
                display_name,
                amount,
            )

            _write_json_file(
                new_scores
            )

            if _commit_push():
                return float(
                    new_total
                )

            # The push was rejected because another Action changed
            # origin. Reset the working tree to the newest origin and
            # retry the SAME increment against the newest score.
            branch = _branch()

            _run_git(
                [
                    "fetch",
                    "origin",
                    branch,
                ]
            )

            reset = _run_git(
                [
                    "git",
                    "reset",
                    "--hard",
                    f"origin/{branch}",
                ]
            )

            if reset.returncode != 0:
                last_error = reset.stderr
                time.sleep(0.5)

        # Local persistence is still better than losing the user's
        # point if GitHub is temporarily unavailable.
        raise RuntimeError(
            "Could not safely save shared leaderboard after "
            f"{PUSH_RETRIES} retries: {last_error or 'unknown error'}"
        )


def _current_scores():
    """
    Prefer origin's current leaderboard. Fall back to local.
    """
    remote = _read_remote_scores()

    if remote is not None:
        _write_json_file(
            remote
        )
        return remote

    return _read_json_file()


def get_score(
    user_id,
):
    with _LOCAL_LOCK:
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
    with _LOCAL_LOCK:
        scores = _current_scores()

    ordered = _ordered_scores(
        scores
    )

    position = None

    for index, (
        uid,
        _entry,
    ) in enumerate(
        ordered
    ):
        if str(uid) == str(
            user_id
        ):
            position = index
            break

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

        if str(uid) == str(
            user_id
        ):
            lines.append(
                f"**#{index + 1} "
                f"{name} — "
                f"{format_points(points)} "
                f"{word} ← you**"
            )
        else:
            lines.append(
                f"#{index + 1} "
                f"{name} — "
                f"{format_points(points)} "
                f"{word}"
            )

    return "\n".join(
        lines
    )


def full_leaderboard(
    title="🏆 **Shared Leaderboard**",
):
    with _LOCAL_LOCK:
        scores = _current_scores()

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
        _uid,
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
