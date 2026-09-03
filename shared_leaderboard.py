import base64
import hashlib
import io
import json
import os
import subprocess
import tarfile
import threading
import time
from pathlib import Path

EVENT_DIR = "shared_leaderboard_events"
LEGACY_FILE = "shared_leaderboard.json"

# Exported so bot.py can serialize its own git commits with leaderboard git
# operations in the same runner/process. RLock keeps nested shared calls safe.
REPOSITORY_LOCK = threading.RLock()
_LOCK = REPOSITORY_LOCK
MAX_RETRIES = 12


def _run(args):
    return subprocess.run(args, capture_output=True, text=True)


def _branch():
    return os.getenv("GITHUB_REF_NAME", "main")


def _configure_git():
    _run(["git", "config", "user.name", "Shared Chatter Ledger"])
    _run([
        "git", "config", "user.email",
        "shared-chatter-ledger@users.noreply.github.com",
    ])


def _fetch():
    result = _run(["git", "fetch", "origin", _branch()])
    return result.returncode == 0


def _fetch_retry(attempts=4):
    for attempt in range(1, attempts + 1):
        if _fetch():
            return True
        if attempt < attempts:
            time.sleep(min(1.5, 0.25 * attempt))
    return False


def _origin_file(path):
    result = _run(["git", "show", f"origin/{_branch()}:{path}"])
    if result.returncode != 0:
        return None
    return result.stdout


def _origin_legacy_scores():
    raw = _origin_file(LEGACY_FILE)
    if not raw:
        return {}
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _local_legacy_scores():
    try:
        path = Path(LEGACY_FILE)
        if not path.exists():
            return {}
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _origin_events():
    """Read all immutable event files from origin in one git-archive call."""
    result = subprocess.run(
        ["git", "archive", "--format=tar", f"origin/{_branch()}", EVENT_DIR],
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
                    tx_id = event.get("transaction_id")
                    if tx_id:
                        events[str(tx_id)] = event
                except Exception:
                    continue
    except Exception:
        return {}
    return events


def _local_events():
    """Best-effort local fallback if GitHub fetch is temporarily unavailable."""
    events = {}
    root = Path(EVENT_DIR)
    if not root.exists():
        return events
    for path in root.glob("*.json"):
        try:
            event = json.loads(path.read_text(encoding="utf-8"))
            tx_id = event.get("transaction_id")
            if tx_id:
                events[str(tx_id)] = event
        except Exception:
            continue
    return events


def _encode_name(name):
    raw = str(name or "Unknown").encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _event_filename(transaction_id):
    digest = hashlib.sha256(str(transaction_id).encode("utf-8")).hexdigest()
    return f"{EVENT_DIR}/{digest}.json"


def _event_payload(transaction_id, user_id, display_name, amount, source):
    return {
        "transaction_id": str(transaction_id),
        "user_id": str(user_id),
        "display_name": str(display_name),
        "amount": round(float(amount), 3),
        "source": source,
        "created_at": int(time.time()),
    }


def _write_event(path, payload):
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_suffix(".tmp")
    with temp.open("w", encoding="utf-8") as file:
        json.dump(payload, file, indent=2, ensure_ascii=False)
        file.write("\n")
    temp.replace(target)


def _reset_to_origin():
    result = _run(["git", "reset", "--hard", f"origin/{_branch()}"])
    return result.returncode == 0


def _commit_push(paths):
    _configure_git()
    add = _run(["git", "add", *paths])
    if add.returncode != 0:
        return False

    commit = _run(["git", "commit", "-m", "Add shared leaderboard event"])
    if commit.returncode != 0:
        return False

    push = _run(["git", "push", "origin", f"HEAD:{_branch()}"])
    return push.returncode == 0


def _snapshot(events, legacy):
    if not events:
        totals = {}
        for uid, entry in legacy.items():
            try:
                points = float(entry.get("points", 0))
            except Exception:
                points = 0.0
            if points:
                totals[str(uid)] = {
                    "points": points,
                    "name": entry.get("name", "Unknown"),
                    "last_created": 0,
                }
        return totals

    totals = {}
    for event in events.values():
        uid = str(event.get("user_id", ""))
        if not uid:
            continue
        try:
            amount = float(event.get("amount", 0))
        except Exception:
            continue

        entry = totals.setdefault(uid, {
            "points": 0.0,
            "name": "Unknown",
            "last_created": -1,
        })
        entry["points"] = round(entry["points"] + amount, 3)

        try:
            created = int(event.get("created_at", 0))
        except Exception:
            created = 0
        if created >= entry["last_created"]:
            entry["last_created"] = created
            entry["name"] = event.get("display_name", "Unknown")
    return totals


def _ensure_legacy_baselines(events, legacy):
    if events:
        return []
    paths = []
    for uid, entry in legacy.items():
        try:
            points = float(entry.get("points", 0))
        except Exception:
            points = 0.0
        if points <= 0:
            continue
        tx_id = f"baseline:{uid}:{points:.3f}"
        path = _event_filename(tx_id)
        if Path(path).exists():
            continue
        payload = _event_payload(
            tx_id, uid, entry.get("name", "Unknown"), points, "legacy-baseline"
        )
        _write_event(path, payload)
        paths.append(path)
    return paths


def _verified_origin_snapshot(required_transaction_id=None):
    """Fetch committed state and optionally verify a transaction is really on origin."""
    if not _fetch_retry():
        return None, False
    events = _origin_events()
    legacy = _origin_legacy_scores()
    if required_transaction_id is not None:
        if str(required_transaction_id) not in events:
            return _snapshot(events, legacy), False
    return _snapshot(events, legacy), True


def add_points(user_id, display_name, amount, transaction_id, source="chatter"):
    if not transaction_id:
        raise ValueError("A unique transaction_id is required.")

    amount = float(amount)
    if amount < 0:
        raise ValueError("Negative point changes are not allowed.")

    path = _event_filename(transaction_id)

    with _LOCK:
        for attempt in range(1, MAX_RETRIES + 1):
            if not _fetch_retry():
                time.sleep(min(2, attempt * 0.2))
                continue

            events = _origin_events()
            legacy = _origin_legacy_scores()

            # Exact transaction id is the idempotency key.
            if str(transaction_id) in events:
                snapshot = _snapshot(events, legacy)
                return float(snapshot.get(str(user_id), {}).get("points", 0))

            if not _reset_to_origin():
                time.sleep(min(2, attempt * 0.2))
                continue

            baseline_paths = _ensure_legacy_baselines(events, legacy) if not events else []
            payload = _event_payload(
                transaction_id, user_id, display_name, amount, source
            )
            _write_event(path, payload)

            if _commit_push(baseline_paths + [path]):
                # Do not trust only the local commit. Confirm GitHub can read the
                # exact transaction back before reporting success to Discord.
                snapshot, verified = _verified_origin_snapshot(transaction_id)
                if verified and snapshot is not None:
                    return float(snapshot.get(str(user_id), {}).get("points", 0))

            time.sleep(min(2, attempt * 0.25))

        raise RuntimeError(
            "Could not safely record leaderboard transaction "
            f"{transaction_id}."
        )


def _current_snapshot():
    with _LOCK:
        snapshot, verified = _verified_origin_snapshot()
        if verified and snapshot is not None:
            return snapshot

        # IMPORTANT: never fall back to origin legacy-only data here. Once
        # immutable events exist, that can make a real score appear as 0.
        local_events = _local_events()
        local_legacy = _local_legacy_scores()
        return _snapshot(local_events, local_legacy)


def admin_set_points(display_name, target_points, transaction_id, source="admin-edit"):
    target_points = float(target_points)
    if target_points < 0:
        raise ValueError("Leaderboard points cannot be negative.")
    if not transaction_id:
        raise ValueError("A unique transaction_id is required.")

    wanted = str(display_name).casefold().strip()

    with _LOCK:
        for attempt in range(1, MAX_RETRIES + 1):
            if not _fetch_retry():
                time.sleep(min(2, attempt * 0.2))
                continue

            events = _origin_events()
            legacy = _origin_legacy_scores()
            snapshot = _snapshot(events, legacy)

            matches = []
            for uid, entry in snapshot.items():
                name = str(entry.get("name", "Unknown")).casefold().strip()
                if name == wanted:
                    matches.append((str(uid), entry))

            if not matches:
                raise ValueError(
                    f"No shared-leaderboard player named '{display_name}' was found."
                )
            if len(matches) > 1:
                raise ValueError(
                    f"More than one shared-leaderboard player matches "
                    f"'{display_name}'. Use the exact display name."
                )

            uid, entry = matches[0]

            if str(transaction_id) in events:
                return float(snapshot.get(uid, {}).get("points", 0))

            current = float(entry.get("points", 0))
            delta = round(target_points - current, 3)
            if delta == 0:
                return target_points

            if not _reset_to_origin():
                time.sleep(min(2, attempt * 0.2))
                continue

            baseline_paths = _ensure_legacy_baselines(events, legacy) if not events else []
            path = _event_filename(transaction_id)
            payload = _event_payload(
                transaction_id,
                uid,
                entry.get("name", display_name),
                delta,
                source,
            )
            _write_event(path, payload)

            if _commit_push(baseline_paths + [path]):
                verified_snapshot, verified = _verified_origin_snapshot(transaction_id)
                if verified and verified_snapshot is not None:
                    return float(
                        verified_snapshot.get(uid, {}).get("points", target_points)
                    )

            time.sleep(min(2, attempt * 0.25))

    raise RuntimeError("Could not safely set the shared leaderboard score.")


def get_score(user_id):
    snapshot = _current_snapshot()
    return float(snapshot.get(str(user_id), {}).get("points", 0))


def format_points(value):
    value = float(value)
    if value.is_integer():
        return str(int(value))
    return f"{value:.1f}"


def _ordered(snapshot):
    return sorted(
        snapshot.items(),
        key=lambda item: (
            -float(item[1].get("points", 0)),
            str(item[1].get("name", "Unknown")).casefold(),
        ),
    )


def personal_ranking(user_id):
    snapshot = _current_snapshot()
    ordered = _ordered(snapshot)
    position = next(
        (i for i, (uid, _) in enumerate(ordered) if str(uid) == str(user_id)),
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
            f"**#{i + 1} {entry.get('name', 'Unknown')} — "
            f"{format_points(points)} {word}{marker}**"
        )
    return "\n".join(lines)


def full_leaderboard(title="🏆 **Shared Leaderboard**"):
    snapshot = _current_snapshot()
    ordered = _ordered(snapshot)
    if not ordered:
        return f"{title}\n\nNo points yet!"

    lines = [title, ""]
    for rank, (_uid, entry) in enumerate(ordered, start=1):
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
            f"{prefix} {entry.get('name', 'Unknown')} — "
            f"**{format_points(points)} {word}**"
        )
    return "\n".join(lines)
