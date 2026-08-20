import json
import os
import subprocess
import threading
import time
from pathlib import Path

LOCK_FILE = "puzzle_mode_lock.json"
_BRANCH = os.getenv("GITHUB_REF_NAME", "main")
_LOCAL_LOCK = threading.Lock()
LOCK_STALE_SECONDS = 10 * 60


def _run(args):
    return subprocess.run(
        args,
        capture_output=True,
        text=True,
    )


def _read_local():
    path = Path(LOCK_FILE)
    if not path.exists():
        return None

    try:
        data = json.loads(
            path.read_text(
                encoding="utf-8"
            )
        )
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _read_remote():
    _run(
        [
            "git",
            "fetch",
            "origin",
            _BRANCH,
        ]
    )

    result = _run(
        [
            "git",
            "show",
            f"origin/{_BRANCH}:{LOCK_FILE}",
        ]
    )

    if result.returncode != 0:
        return None

    try:
        data = json.loads(
            result.stdout
        )
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def get_lock():
    """
    Read the latest lock from origin.

    A Survival lock older than 10 minutes is considered stale.
    This prevents a dead GitHub Action from permanently blocking
    Daily/Random after a crash.
    """
    remote = _read_remote()

    if remote is None:
        remote = _read_local()

    if not remote:
        return None

    try:
        last_activity = float(
            remote.get(
                "last_activity_epoch",
                0,
            )
        )
    except Exception:
        return None

    if (
        time.time()
        - last_activity
        > LOCK_STALE_SECONDS
    ):
        return None

    if remote.get(
        "mode"
    ) != "survival":
        return None

    return remote


def is_survival_active():
    return get_lock() is not None


def active_team():
    lock = get_lock()
    if not lock:
        return None
    return lock.get(
        "team",
    )


def write_lock(
    team,
    survival_id,
    last_activity_epoch=None,
):
    if last_activity_epoch is None:
        last_activity_epoch = time.time()

    payload = {
        "mode": "survival",
        "team": team,
        "survival_id": survival_id,
        "last_activity_epoch": float(
            last_activity_epoch
        ),
        "updated_at_epoch": time.time(),
    }

    path = Path(LOCK_FILE)

    with _LOCAL_LOCK:

        for _ in range(8):

            try:
                path.write_text(
                    json.dumps(
                        payload,
                        indent=2,
                        ensure_ascii=False,
                    )
                    + "\n",
                    encoding="utf-8",
                )

                _run(
                    [
                        "git",
                        "config",
                        "user.name",
                        "Survival Mode Bot",
                    ]
                )

                _run(
                    [
                        "git",
                        "config",
                        "user.email",
                        "survival-mode-bot@users.noreply.github.com",
                    ]
                )

                _run(
                    [
                        "git",
                        "add",
                        LOCK_FILE,
                    ]
                )

                commit = _run(
                    [
                        "git",
                        "commit",
                        "-m",
                        "Update puzzle mode lock",
                    ]
                )

                if commit.returncode != 0:
                    return payload

                push = _run(
                    [
                        "git",
                        "push",
                        "origin",
                        f"HEAD:{_BRANCH}",
                    ]
                )

                if push.returncode == 0:
                    return payload

                _run(
                    [
                        "git",
                        "fetch",
                        "origin",
                        _BRANCH,
                    ]
                )

                _run(
                    [
                        "git",
                        "reset",
                        "--hard",
                        f"origin/{_BRANCH}",
                    ]
                )

            except Exception:
                pass

            time.sleep(0.5)

    raise RuntimeError(
        "Could not save puzzle mode lock."
    )


def clear_lock():
    path = Path(LOCK_FILE)

    with _LOCAL_LOCK:

        for _ in range(8):

            try:
                # Always reset first so a stale lock update from another
                # workflow does not cause us to delete unrelated work.
                _run(
                    [
                        "git",
                        "fetch",
                        "origin",
                        _BRANCH,
                    ]
                )

                _run(
                    [
                        "git",
                        "reset",
                        "--hard",
                        f"origin/{_BRANCH}",
                    ]
                )

                if path.exists():
                    path.unlink()

                _run(
                    [
                        "git",
                        "config",
                        "user.name",
                        "Survival Mode Bot",
                    ]
                )

                _run(
                    [
                        "git",
                        "config",
                        "user.email",
                        "survival-mode-bot@users.noreply.github.com",
                    ]
                )

                _run(
                    [
                        "git",
                        "add",
                        "-u",
                        LOCK_FILE,
                    ]
                )

                commit = _run(
                    [
                        "git",
                        "commit",
                        "-m",
                        "Clear puzzle mode lock",
                    ]
                )

                if commit.returncode != 0:
                    return True

                push = _run(
                    [
                        "git",
                        "push",
                        "origin",
                        f"HEAD:{_BRANCH}",
                    ]
                )

                if push.returncode == 0:
                    return True

            except Exception:
                pass

            time.sleep(0.5)

    return False
