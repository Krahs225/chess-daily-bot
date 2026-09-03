import hashlib
import io
import json
import os
import subprocess
import tarfile
import tempfile
import threading
import time
from pathlib import Path

LEDGER_BUILD = "shared-ledger-v12-snapshot-2026-09-03"

EVENT_DIR = "shared_leaderboard_events"
LEGACY_FILE = "shared_leaderboard.json"
MIGRATION_TRANSACTION_ID = "__shared-ledger-v12-snapshot-migration__"
MAX_RETRIES = 12

# Exported for bot.py. Daily state commits and leaderboard writes in the same
# process use one lock. Cross-process races are handled by Git fast-forward
# retries, without resetting the running checkout.
REPOSITORY_LOCK = threading.RLock()
_LOCK = REPOSITORY_LOCK

_CACHE_SNAPSHOT = None


def _run(args, *, env=None, input_text=None):
    return subprocess.run(
        args,
        capture_output=True,
        text=True,
        env=env,
        input=input_text,
    )


def _branch():
    return os.getenv("GITHUB_REF_NAME", "main")


def _fetch():
    branch = _branch()
    result = _run([
        "git",
        "fetch",
        "origin",
        f"+refs/heads/{branch}:refs/remotes/origin/{branch}",
    ])
    return result.returncode == 0


def _fetch_retry(attempts=4):
    for attempt in range(1, attempts + 1):
        if _fetch():
            return True
        if attempt < attempts:
            time.sleep(min(1.5, 0.25 * attempt))
    return False


def _origin_ref():
    return f"origin/{_branch()}"


def _origin_file(path):
    result = _run(["git", "show", f"{_origin_ref()}:{path}"])
    if result.returncode != 0:
        return None
    return result.stdout


def _local_json(path):
    try:
        target = Path(path)
        if not target.exists():
            return None
        return json.loads(target.read_text(encoding="utf-8"))
    except Exception:
        return None


def _normalize_snapshot(data):
    if not isinstance(data, dict):
        return None

    snapshot = {}
    for uid, entry in data.items():
        if not isinstance(entry, dict):
            continue
        try:
            points = round(float(entry.get("points", 0)), 3)
        except Exception:
            points = 0.0
        snapshot[str(uid)] = {
            "name": str(entry.get("name", "Unknown")),
            "points": points,
        }
    return snapshot


def _origin_legacy_scores():
    raw = _origin_file(LEGACY_FILE)
    if raw is None:
        return {}
    try:
        data = json.loads(raw)
    except Exception:
        return None
    return _normalize_snapshot(data)


def _local_legacy_scores():
    normalized = _normalize_snapshot(_local_json(LEGACY_FILE))
    return normalized if normalized is not None else {}


def _event_filename(transaction_id):
    digest = hashlib.sha256(str(transaction_id).encode("utf-8")).hexdigest()
    return f"{EVENT_DIR}/{digest}.json"


def _origin_event(transaction_id):
    raw = _origin_file(_event_filename(transaction_id))
    if not raw:
        return None
    try:
        event = json.loads(raw)
        return event if isinstance(event, dict) else None
    except Exception:
        return None


def _local_event(transaction_id):
    data = _local_json(_event_filename(transaction_id))
    return data if isinstance(data, dict) else None


def _origin_events_all():
    """Load old immutable events only for the one-time v12 migration path."""
    result = subprocess.run(
        ["git", "archive", "--format=tar", _origin_ref(), EVENT_DIR],
        capture_output=True,
    )
    if result.returncode != 0:
        return {}

    events = {}
    try:
        with tarfile.open(fileobj=io.BytesIO(result.stdout), mode="r:") as archive:
            for member in archive.getmembers():
                if not member.isfile() or not member.name.startswith(EVENT_DIR + "/"):
                    continue
                extracted = archive.extractfile(member)
                if extracted is None:
                    continue
                try:
                    event = json.loads(extracted.read())
                except Exception:
                    continue
                if not isinstance(event, dict):
                    continue
                tx_id = event.get("transaction_id")
                if tx_id:
                    events[str(tx_id)] = event
    except Exception:
        return {}
    return events


def _local_events_all():
    events = {}
    root = Path(EVENT_DIR)
    if not root.exists():
        return events
    for path in root.glob("*.json"):
        try:
            event = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(event, dict):
            continue
        tx_id = event.get("transaction_id")
        if tx_id:
            events[str(tx_id)] = event
    return events
    def _historical_snapshot(events, legacy):
    """
    Reproduce the pre-v12 ledger exactly once.

    Before v12, once event files existed, shared_leaderboard.json was ignored
    and every event amount was summed. If no events existed, the JSON snapshot
    was used. This preserves everybody's live score at migration time.
    """
    if not events:
        return {
            str(uid): {
                "name": str(entry.get("name", "Unknown")),
                "points": round(float(entry.get("points", 0)), 3),
            }
            for uid, entry in (legacy or {}).items()
            if isinstance(entry, dict)
        }

    totals = {}
    for event in events.values():
        if not isinstance(event, dict):
            continue
        if event.get("operation") == "migration":
            continue

        uid = str(event.get("user_id", "")).strip()
        if not uid:
            continue
        try:
            amount = float(event.get("amount", 0))
        except Exception:
            continue

        entry = totals.setdefault(uid, {
            "name": "Unknown",
            "points": 0.0,
            "_last_created": -1,
        })
        entry["points"] = round(float(entry["points"]) + amount, 3)

        try:
            created = int(event.get("created_at", 0))
        except Exception:
            created = 0
        if created >= entry["_last_created"]:
            entry["_last_created"] = created
            entry["name"] = str(
                event.get("display_name", event.get("name", "Unknown"))
            )

    for entry in totals.values():
        entry.pop("_last_created", None)
    return totals


def _origin_state():
    """Return (snapshot, migrated). Requires a successful fetch beforehand."""
    migrated = _origin_event(MIGRATION_TRANSACTION_ID) is not None

    if migrated:
        snapshot = _origin_legacy_scores()
        if snapshot is None:
            raise RuntimeError(
                "Canonical shared_leaderboard.json is missing or invalid after v12 migration."
            )
        return snapshot, True

    legacy = _origin_legacy_scores()
    if legacy is None:
        legacy = {}
    events = _origin_events_all()
    return _historical_snapshot(events, legacy), False


def _local_state():
    migrated = _local_event(MIGRATION_TRANSACTION_ID) is not None
    if migrated:
        snapshot = _local_legacy_scores()
        return snapshot, True
    return _historical_snapshot(_local_events_all(), _local_legacy_scores()), False


def _snapshot_json(snapshot):
    clean = {}
    for uid, entry in snapshot.items():
        clean[str(uid)] = {
            "name": str(entry.get("name", "Unknown")),
            "points": round(float(entry.get("points", 0)), 3),
        }
    return json.dumps(clean, indent=2, ensure_ascii=False, sort_keys=True) + "\n"


def _event_json(payload):
    return json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True) + "\n"


def _audit_event(
    transaction_id,
    user_id,
    display_name,
    *,
    operation,
    source,
    before_points,
    after_points,
    amount,
):
    return {
        "transaction_id": str(transaction_id),
        "operation": str(operation),
        "user_id": str(user_id),
        "display_name": str(display_name),
        "amount": round(float(amount), 3),
        "before_points": round(float(before_points), 3),
        "after_points": round(float(after_points), 3),
        "source": str(source),
        "ledger_build": LEDGER_BUILD,
        "created_at": int(time.time()),
    }


def _migration_event():
    return {
        "transaction_id": MIGRATION_TRANSACTION_ID,
        "operation": "migration",
        "source": "v12-snapshot-migration",
        "ledger_build": LEDGER_BUILD,
        "created_at": int(time.time()),
    }


def _git_blob(content):
    result = _run(["git", "hash-object", "-w", "--stdin"], input_text=content)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "git hash-object failed")
    return result.stdout.strip()


def _commit_snapshot(base_commit, files, message):
    """
    Create a commit with a temporary Git index.

    This never runs git reset/checkout and never touches bot.py, the RP pool,
    Survival state, or any other file in the running Actions checkout.
    """
    with tempfile.TemporaryDirectory(prefix="shared-ledger-index-") as temp_dir:
        index_file = os.path.join(temp_dir, "index")
        env = os.environ.copy()
        env["GIT_INDEX_FILE"] = index_file

        read_tree = _run(["git", "read-tree", base_commit], env=env)
        if read_tree.returncode != 0:
            raise RuntimeError(read_tree.stderr.strip() or "git read-tree failed")

        for path, content in files.items():
            blob = _git_blob(content)
            update = _run(
                [
                    "git",
                    "update-index",
                    "--add",
                    "--cacheinfo",
                    "100644",
                    blob,
                    path,
                ],
                env=env,
            )
            if update.returncode != 0:
                raise RuntimeError(update.stderr.strip() or "git update-index failed")

        tree = _run(["git", "write-tree"], env=env)
        if tree.returncode != 0:
            raise RuntimeError(tree.stderr.strip() or "git write-tree failed")
        tree_id = tree.stdout.strip()
                commit_env = os.environ.copy()
        commit_env.update({
            "GIT_AUTHOR_NAME": "Shared Chatter Ledger",
            "GIT_AUTHOR_EMAIL": "shared-chatter-ledger@users.noreply.github.com",
            "GIT_COMMITTER_NAME": "Shared Chatter Ledger",
            "GIT_COMMITTER_EMAIL": "shared-chatter-ledger@users.noreply.github.com",
        })
        commit = _run(
            ["git", "commit-tree", tree_id, "-p", base_commit, "-m", message],
            env=commit_env,
        )
        if commit.returncode != 0:
            raise RuntimeError(commit.stderr.strip() or "git commit-tree failed")
        return commit.stdout.strip()


def _push_files(files, message):
    base = _run(["git", "rev-parse", _origin_ref()])
    if base.returncode != 0:
        return False

    try:
        commit_id = _commit_snapshot(base.stdout.strip(), files, message)
    except Exception:
        return False

    push = _run([
        "git",
        "push",
        "origin",
        f"{commit_id}:refs/heads/{_branch()}",
    ])
    return push.returncode == 0


def _verified_origin_snapshot(required_transaction_id=None):
    global _CACHE_SNAPSHOT

    if not _fetch_retry():
        return None, False

    try:
        snapshot, _migrated = _origin_state()
    except Exception:
        return None, False

    if required_transaction_id is not None:
        if _origin_event(required_transaction_id) is None:
            return snapshot, False

    _CACHE_SNAPSHOT = {
        uid: dict(entry) for uid, entry in snapshot.items()
    }
    return snapshot, True


def _current_snapshot():
    global _CACHE_SNAPSHOT

    with _LOCK:
        snapshot, verified = _verified_origin_snapshot()
        if verified and snapshot is not None:
            return snapshot

        if _CACHE_SNAPSHOT is not None:
            return {uid: dict(entry) for uid, entry in _CACHE_SNAPSHOT.items()}

        snapshot, _migrated = _local_state()
        return snapshot


def add_points(user_id, display_name, amount, transaction_id, source="chatter"):
    global _CACHE_SNAPSHOT

    if not transaction_id:
        raise ValueError("A unique transaction_id is required.")

    amount = float(amount)
    if amount < 0:
        raise ValueError("Negative point changes are not allowed.")

    uid = str(user_id)

    with _LOCK:
        for attempt in range(1, MAX_RETRIES + 1):
            if not _fetch_retry():
                time.sleep(min(2.0, 0.2 * attempt))
                continue

            existing = _origin_event(transaction_id)
            try:
                snapshot, migrated = _origin_state()
            except Exception:
                time.sleep(min(2.0, 0.2 * attempt))
                continue

            if existing is not None:
                _CACHE_SNAPSHOT = {k: dict(v) for k, v in snapshot.items()}
                return float(snapshot.get(uid, {}).get("points", 0))

            before = float(snapshot.get(uid, {}).get("points", 0))
            after = round(before + amount, 3)
            snapshot[uid] = {
                "name": str(display_name),
                "points": after,
            }

            payload = _audit_event(
                transaction_id,
                uid,
                display_name,
                operation="add",
                source=source,
                before_points=before,
                after_points=after,
                amount=amount,
            )

            files = {
                LEGACY_FILE: _snapshot_json(snapshot),
                _event_filename(transaction_id): _event_json(payload),
            }
            if not migrated:
                files[_event_filename(MIGRATION_TRANSACTION_ID)] = _event_json(
                    _migration_event()
                )

            if _push_files(files, "Update shared leaderboard snapshot"):
                verified_snapshot, verified = _verified_origin_snapshot(transaction_id)
                if verified and verified_snapshot is not None:
                    return float(verified_snapshot.get(uid, {}).get("points", after))

            time.sleep(min(2.0, 0.25 * attempt))

    raise RuntimeError(
        "Could not safely record shared leaderboard transaction "
        f"{transaction_id}."
    )


def admin_set_points(
    display_name,
    target_points,
    transaction_id,
    source="admin-edit",
    target_user_id=None,
):
    global _CACHE_SNAPSHOT

    target_points = round(float(target_points), 3)
    if target_points < 0:
        raise ValueError("Leaderboard points cannot be negative.")
    if not transaction_id:
        raise ValueError("A unique transaction_id is required.")

    wanted = str(display_name).casefold().strip()

    with _LOCK:
        for attempt in range(1, MAX_RETRIES + 1):
            if not _fetch_retry():
                time.sleep(min(2.0, 0.2 * attempt))
                continue

            existing = _origin_event(transaction_id)
            try:
                snapshot, migrated = _origin_state()
            except Exception:
                time.sleep(min(2.0, 0.2 * attempt))
                continue

            if target_user_id is not None:
                uid = str(target_user_id)
                existing_entry = snapshot.get(uid, {})
                canonical_name = str(
                    display_name or existing_entry.get("name", "Unknown")
                )
            else:
                matches = []
                                for candidate_uid, entry in snapshot.items():
                    name = str(entry.get("name", "Unknown")).casefold().strip()
                    if name == wanted:
                        matches.append((str(candidate_uid), entry))

                if not matches:
                    raise ValueError(
                        f"No shared-leaderboard player named '{display_name}' was found."
                    )
                if len(matches) > 1:
                    raise ValueError(
                        f"More than one shared-leaderboard player matches "
                        f"'{display_name}'. Use the exact display name."
                    )

                uid, existing_entry = matches[0]
                canonical_name = str(
                    existing_entry.get("name", display_name)
                )

            if existing is not None:
                _CACHE_SNAPSHOT = {
                    k: dict(v) for k, v in snapshot.items()
                }
                return float(
                    snapshot.get(uid, {}).get("points", 0)
                )

            before = float(
                snapshot.get(uid, {}).get("points", 0)
            )

            after = target_points

            snapshot[uid] = {
                "name": canonical_name,
                "points": after,
            }

            payload = _audit_event(
                transaction_id,
                uid,
                canonical_name,
                operation="set",
                source=source,
                before_points=before,
                after_points=after,
                amount=round(after - before, 3),
            )

            files = {
                LEGACY_FILE:
                    _snapshot_json(snapshot),

                _event_filename(transaction_id):
                    _event_json(payload),
            }

            if not migrated:
                files[
                    _event_filename(
                        MIGRATION_TRANSACTION_ID
                    )
                ] = _event_json(
                    _migration_event()
                )

            if _push_files(
                files,
                "Set shared leaderboard score",
            ):
                verified_snapshot, verified = (
                    _verified_origin_snapshot(
                        transaction_id
                    )
                )

                if (
                    verified
                    and verified_snapshot is not None
                ):
                    return float(
                        verified_snapshot.get(
                            uid,
                            {},
                        ).get(
                            "points",
                            after,
                        )
                    )

            time.sleep(
                min(
                    2.0,
                    0.25 * attempt,
                )
            )

    raise RuntimeError(
        "Could not safely set the shared leaderboard score."
    )


def get_score(user_id):
    snapshot = _current_snapshot()

    return float(
        snapshot.get(
            str(user_id),
            {},
        ).get(
            "points",
            0,
        )
    )


def format_points(value):
    value = float(value)

    if value.is_integer():
        return str(int(value))

    text = (
        f"{value:.3f}"
        .rstrip("0")
        .rstrip(".")
    )

    return text


def _ordered(snapshot):
    return sorted(
        snapshot.items(),
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


def personal_ranking(user_id):
    snapshot = _current_snapshot()

    ordered = _ordered(
        snapshot
    )

    position = next(
        (
            i
            for i, (uid, _)
            in enumerate(ordered)
            if str(uid) == str(user_id)
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

    for i in range(
        start,
        end,
    ):
        uid, entry = ordered[i]

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

        marker = (
            " ← you"
            if str(uid) == str(user_id)
            else ""
        )

        lines.append(
            f"**#{i + 1} "
            f"{entry.get('name', 'Unknown')} — "
            f"{format_points(points)} "
            f"{word}{marker}**"
        )

    return "\n".join(
        lines
    )


def full_leaderboard(
    title="🏆 **Shared Leaderboard**"
):
    snapshot = _current_snapshot()

    ordered = _ordered(
        snapshot
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
