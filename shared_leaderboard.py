import hashlib
import io
import json
import os
import random
import subprocess
import tarfile
import tempfile
import threading
import time
from pathlib import Path

from shop_catalog import (
    BADGE_BOX_COST, BADGE_POOLS, BADGE_RARITY_WEIGHTS, BOARD_COST,
    BOARD_THEMES, COLOR_COST, NAME_COLORS, RARITY_LABELS,
)

LEDGER_BUILD = "shared-ledger-v12-snapshot-2026-09-03"

EVENT_DIR = "shared_leaderboard_events"
LEGACY_FILE = "shared_leaderboard.json"
MIGRATION_TRANSACTION_ID = "__shared-ledger-v12-snapshot-migration__"
MAX_RETRIES = 12

# Shared by bot.py so Daily state commits and leaderboard writes in the same
# process cannot collide. Cross-process races are handled by Git push retries.
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


def _origin_ref():
    return f"origin/{_branch()}"


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


def _normalize_entry(entry):
    entry = entry if isinstance(entry, dict) else {}
    try:
        points = round(float(entry.get("points", 0)), 3)
    except Exception:
        points = 0.0

    # One-time wallet migration: an old leaderboard row has no ``coins``
    # field, so its starting wallet is exactly its current points. Once coins
    # are persisted they are independent: purchases reduce coins, never points.
    try:
        coins = round(float(entry.get("coins", points)), 3)
    except Exception:
        coins = points
    coins = max(0.0, coins)

    badges = entry.get("badges", [])
    if not isinstance(badges, list):
        badges = []
    badges = [str(item) for item in badges if str(item)]

    boards = entry.get("boards", [])
    if not isinstance(boards, list):
        boards = []
    boards = [str(item).casefold() for item in boards if str(item).casefold() in BOARD_THEMES and str(item).casefold() != "classic"]

    colors = entry.get("colors", [])
    if not isinstance(colors, list):
        colors = []
    colors = [str(item).casefold() for item in colors if str(item).casefold() in NAME_COLORS]

    active_badge = str(entry.get("active_badge", "") or "")
    if active_badge not in badges:
        active_badge = ""

    active_board = str(entry.get("active_board", "classic") or "classic").casefold()
    if active_board != "classic" and active_board not in boards:
        active_board = "classic"

    active_color = str(entry.get("active_color", "") or "").casefold()
    if active_color not in colors:
        active_color = ""

    return {
        "name": str(entry.get("name", "Unknown")),
        "points": points,
        "coins": coins,
        "badges": badges,
        "active_badge": active_badge,
        "boards": boards,
        "active_board": active_board,
        "colors": colors,
        "active_color": active_color,
    }


def _normalize_snapshot(data):
    if not isinstance(data, dict):
        return None

    snapshot = {}
    for uid, entry in data.items():
        if not isinstance(entry, dict):
            continue
        snapshot[str(uid)] = _normalize_entry(entry)
    return snapshot


def _origin_snapshot_file():
    raw = _origin_file(LEGACY_FILE)
    if raw is None:
        return {}
    try:
        return _normalize_snapshot(json.loads(raw))
    except Exception:
        return None


def _local_snapshot_file():
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
    """Read old immutable events only for the one-time v12 migration."""
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
    Reproduce the pre-v12 leaderboard exactly once.

    Old behavior was: if immutable events exist, sum their amounts and ignore
    shared_leaderboard.json; otherwise use shared_leaderboard.json. After the
    migration marker exists, this reconstruction is never used again.
    """
    if not events:
        return {
            str(uid): _normalize_entry(entry)
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
            created = int(event.get("created_at_ns", 0))
        except Exception:
            created = 0
        if created <= 0:
            try:
                created = int(event.get("created_at", 0)) * 1_000_000_000
            except Exception:
                created = 0

        if created >= entry["_last_created"]:
            entry["_last_created"] = created
            entry["name"] = str(
                event.get("display_name", event.get("name", "Unknown"))
            )

    for entry in totals.values():
        entry.pop("_last_created", None)
    return {uid: _normalize_entry(entry) for uid, entry in totals.items()}


def _origin_state():
    """Return (snapshot, migrated). Call only after a successful fetch."""
    migrated = _origin_event(MIGRATION_TRANSACTION_ID) is not None

    if migrated:
        snapshot = _origin_snapshot_file()
        if snapshot is None:
            raise RuntimeError(
                "Canonical shared_leaderboard.json is missing or invalid "
                "after v12 migration."
            )
        return snapshot, True

    legacy = _origin_snapshot_file()
    if legacy is None:
        legacy = {}
    return _historical_snapshot(_origin_events_all(), legacy), False


def _local_state():
    migrated = _local_event(MIGRATION_TRANSACTION_ID) is not None
    if migrated:
        return _local_snapshot_file(), True
    return _historical_snapshot(_local_events_all(), _local_snapshot_file()), False


def _snapshot_json(snapshot):
    clean = {}
    for uid, entry in snapshot.items():
        clean[str(uid)] = _normalize_entry(entry)
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
        "created_at_ns": int(time.time_ns()),
    }


def _migration_event():
    return {
        "transaction_id": MIGRATION_TRANSACTION_ID,
        "operation": "migration",
        "source": "v12-snapshot-migration",
        "ledger_build": LEDGER_BUILD,
        "created_at": int(time.time()),
        "created_at_ns": int(time.time_ns()),
    }


def _git_blob(content):
    result = _run(["git", "hash-object", "-w", "--stdin"], input_text=content)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "git hash-object failed")
    return result.stdout.strip()


def _commit_snapshot(base_commit, files, message):
    """
    Build a commit with a temporary Git index.

    No reset, checkout, or worktree mutation is used, so a leaderboard write
    cannot overwrite the running bot's Daily/Random/Survival files.
    """
    with tempfile.TemporaryDirectory(prefix="shared-ledger-index-") as temp_dir:
        index_file = os.path.join(temp_dir, "index")
        index_env = os.environ.copy()
        index_env["GIT_INDEX_FILE"] = index_file

        read_tree = _run(["git", "read-tree", base_commit], env=index_env)
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
                env=index_env,
            )
            if update.returncode != 0:
                raise RuntimeError(
                    update.stderr.strip() or "git update-index failed"
                )

        tree = _run(["git", "write-tree"], env=index_env)
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

    _CACHE_SNAPSHOT = {uid: dict(entry) for uid, entry in snapshot.items()}
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
            entry = _normalize_entry(snapshot.get(uid, {"name": display_name, "points": before}))
            entry["name"] = str(display_name)
            entry["points"] = after
            entry["coins"] = round(max(0.0, float(entry.get("coins", before)) + amount), 3)
            snapshot[uid] = entry

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


def adjust_points(user_id, display_name, amount, transaction_id, source="adjustment"):
    """Apply a signed point adjustment exactly once.

    This is deliberately separate from add_points(), which continues to reject
    negative amounts. Anti-spam penalties use this function with -1.0 and a
    deterministic transaction ID so retries cannot deduct twice.
    """
    global _CACHE_SNAPSHOT

    if not transaction_id:
        raise ValueError("A unique transaction_id is required.")

    amount = float(amount)
    if amount == 0:
        return get_score(user_id)

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
            entry = _normalize_entry(snapshot.get(uid, {"name": display_name, "points": before}))
            entry["name"] = str(display_name)
            entry["points"] = after
            entry["coins"] = round(max(0.0, float(entry.get("coins", before)) + amount), 3)
            snapshot[uid] = entry

            payload = _audit_event(
                transaction_id,
                uid,
                display_name,
                operation="adjust",
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

            if _push_files(files, "Adjust shared leaderboard snapshot"):
                verified_snapshot, verified = _verified_origin_snapshot(transaction_id)
                if verified and verified_snapshot is not None:
                    return float(verified_snapshot.get(uid, {}).get("points", after))

            time.sleep(min(2.0, 0.25 * attempt))

    raise RuntimeError(
        "Could not safely record shared leaderboard adjustment "
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
                canonical_name = str(existing_entry.get("name", display_name))

            if existing is not None:
                _CACHE_SNAPSHOT = {k: dict(v) for k, v in snapshot.items()}
                return float(snapshot.get(uid, {}).get("points", 0))

            before = float(snapshot.get(uid, {}).get("points", 0))
            after = target_points
            entry = _normalize_entry(snapshot.get(uid, {"name": canonical_name, "points": before}))
            entry["name"] = canonical_name
            entry["points"] = after
            entry["coins"] = round(max(0.0, float(entry.get("coins", before)) + (after - before)), 3)
            snapshot[uid] = entry

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
                LEGACY_FILE: _snapshot_json(snapshot),
                _event_filename(transaction_id): _event_json(payload),
            }
            if not migrated:
                files[_event_filename(MIGRATION_TRANSACTION_ID)] = _event_json(
                    _migration_event()
                )

            if _push_files(files, "Set shared leaderboard score"):
                verified_snapshot, verified = _verified_origin_snapshot(transaction_id)
                if verified and verified_snapshot is not None:
                    return float(verified_snapshot.get(uid, {}).get("points", after))

            time.sleep(min(2.0, 0.25 * attempt))

    raise RuntimeError("Could not safely set the shared leaderboard score.")


def get_score(user_id):
    snapshot = _current_snapshot()
    return float(snapshot.get(str(user_id), {}).get("points", 0))


def get_coins(user_id):
    snapshot = _current_snapshot()
    entry = _normalize_entry(snapshot.get(str(user_id), {}))
    return float(entry.get("coins", entry.get("points", 0)))


def get_cosmetic_profile(user_id, fallback_name=None):
    snapshot = _current_snapshot()
    uid = str(user_id)
    raw = snapshot.get(uid, {})
    entry = _normalize_entry(raw)
    if fallback_name and (not raw or entry.get("name") == "Unknown"):
        entry["name"] = str(fallback_name)
    return {"user_id": uid, **entry}


def resolve_cosmetic_profile(display_name, target_user_id=None):
    """Resolve a shared cosmetic-wallet player by exact display name or user id."""
    snapshot = _current_snapshot()

    if target_user_id is not None:
        uid = str(target_user_id)
        raw = snapshot.get(uid, {})
        entry = _normalize_entry(raw)
        if display_name and (not raw or entry.get("name") == "Unknown"):
            entry["name"] = str(display_name)
        return {"user_id": uid, **entry}

    wanted = str(display_name).casefold().strip()
    matches = []
    for uid, raw in snapshot.items():
        entry = _normalize_entry(raw)
        if str(entry.get("name", "Unknown")).casefold().strip() == wanted:
            matches.append((str(uid), entry))

    if not matches:
        raise ValueError(
            f"No shared-leaderboard player named '{display_name}' was found."
        )
    if len(matches) > 1:
        raise ValueError(
            f"More than one shared-leaderboard player matches '{display_name}'. "
            "Use the exact display name."
        )

    uid, entry = matches[0]
    return {"user_id": uid, **entry}


def badge_for_user(user_id):
    return str(get_cosmetic_profile(user_id).get("active_badge", "") or "")


def badge_map(user_ids=None):
    snapshot = _current_snapshot()
    wanted = None if user_ids is None else {str(uid) for uid in user_ids}
    result = {}
    for uid, entry in snapshot.items():
        if wanted is not None and str(uid) not in wanted:
            continue
        result[str(uid)] = str(entry.get("active_badge", "") or "")
    return result


def badge_prefix(user_id):
    badge = badge_for_user(user_id)
    return (badge + " ") if badge else ""


def _shop_mutation(user_id, display_name, transaction_id, operation, mutate):
    global _CACHE_SNAPSHOT
    if not transaction_id:
        raise ValueError("A unique transaction_id is required.")
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
                return _normalize_entry(snapshot.get(uid, {})), existing

            entry = _normalize_entry(snapshot.get(uid, {
                "name": display_name,
                "points": 0,
            }))
            entry["name"] = str(display_name)
            before_coins = float(entry.get("coins", 0))
            details = mutate(entry) or {}
            after_coins = float(entry.get("coins", 0))
            snapshot[uid] = entry

            payload = {
                "transaction_id": str(transaction_id),
                "operation": str(operation),
                "user_id": uid,
                "display_name": str(display_name),
                "before_coins": round(before_coins, 3),
                "after_coins": round(after_coins, 3),
                "details": details,
                "ledger_build": LEDGER_BUILD,
                "created_at": int(time.time()),
                "created_at_ns": int(time.time_ns()),
            }

            files = {
                LEGACY_FILE: _snapshot_json(snapshot),
                _event_filename(transaction_id): _event_json(payload),
            }
            if not migrated:
                files[_event_filename(MIGRATION_TRANSACTION_ID)] = _event_json(
                    _migration_event()
                )

            if _push_files(files, "Update cosmetic shop state"):
                verified_snapshot, verified = _verified_origin_snapshot(transaction_id)
                if verified and verified_snapshot is not None:
                    return _normalize_entry(verified_snapshot.get(uid, entry)), payload

            time.sleep(min(2.0, 0.25 * attempt))

    raise RuntimeError(f"Could not safely record shop transaction {transaction_id}.")


def spend_coins(user_id, display_name, amount, transaction_id, source="shop"):
    amount = round(float(amount), 3)
    if amount <= 0:
        raise ValueError("Coin spend must be positive.")

    def mutate(entry):
        before = float(entry.get("coins", 0))
        if before + 1e-9 < amount:
            raise ValueError(
                f"Not enough coins. Need {format_points(amount)}, have {format_points(before)}."
            )
        entry["coins"] = round(before - amount, 3)
        return {"source": str(source), "spent": amount}

    entry, _event = _shop_mutation(
        user_id, display_name, transaction_id, "coin-spend", mutate
    )
    return float(entry.get("coins", 0))


def credit_coins(user_id, display_name, amount, transaction_id, source="coin-credit"):
    amount = round(float(amount), 3)
    if amount <= 0:
        raise ValueError("Coin credit must be positive.")

    def mutate(entry):
        entry["coins"] = round(float(entry.get("coins", 0)) + amount, 3)
        return {"source": str(source), "credited": amount}

    entry, _event = _shop_mutation(
        user_id, display_name, transaction_id, "coin-credit", mutate
    )
    return float(entry.get("coins", 0))


def admin_set_coins(
    display_name,
    target_coins,
    transaction_id,
    source="admin-editcoins",
    target_user_id=None,
):
    """Sharkmeister admin repair: set wallet coins without changing points."""
    target_coins = round(float(target_coins), 3)
    if target_coins < 0:
        raise ValueError("Coins cannot be negative.")

    target = resolve_cosmetic_profile(display_name, target_user_id=target_user_id)
    uid = target["user_id"]
    canonical_name = target.get("name", display_name)

    def mutate(entry):
        before = round(float(entry.get("coins", 0)), 3)
        entry["coins"] = target_coins
        return {
            "source": str(source),
            "before_coins": before,
            "after_coins": target_coins,
        }

    entry, _event = _shop_mutation(
        uid, canonical_name, transaction_id, "admin-set-coins", mutate
    )
    return float(entry.get("coins", 0))


def admin_set_color(
    display_name,
    color_name,
    transaction_id,
    source="admin-editcolor",
    target_user_id=None,
):
    """Sharkmeister admin repair: force a supported active shop color for a user."""
    color_name = str(color_name or "").casefold().strip()
    if color_name == "default":
        color_name = ""
    if color_name and color_name not in NAME_COLORS:
        raise ValueError("Unknown shop color.")

    target = resolve_cosmetic_profile(display_name, target_user_id=target_user_id)
    uid = target["user_id"]
    canonical_name = target.get("name", display_name)

    def mutate(entry):
        before = str(entry.get("active_color", "") or "")
        granted = False
        if color_name and color_name not in entry.get("colors", []):
            entry.setdefault("colors", []).append(color_name)
            granted = True
        entry["active_color"] = color_name
        return {
            "source": str(source),
            "before_color": before,
            "after_color": color_name,
            "granted_if_missing": granted,
        }

    entry, _event = _shop_mutation(
        uid, canonical_name, transaction_id, "admin-set-color", mutate
    )
    return {"user_id": uid, **entry}


def buy_badge_box(user_id, display_name, transaction_id):
    rarity_names = list(BADGE_RARITY_WEIGHTS)
    weights = [BADGE_RARITY_WEIGHTS[name] for name in rarity_names]
    rarity = random.choices(rarity_names, weights=weights, k=1)[0]
    badge = random.choice(BADGE_POOLS[rarity])

    def mutate(entry):
        before = float(entry.get("coins", 0))
        if before + 1e-9 < BADGE_BOX_COST:
            raise ValueError(
                f"Not enough coins. Need {format_points(BADGE_BOX_COST)}, have {format_points(before)}."
            )
        entry["coins"] = round(before - BADGE_BOX_COST, 3)
        entry.setdefault("badges", []).append(badge)
        if not entry.get("active_badge"):
            entry["active_badge"] = badge
        return {
            "spent": BADGE_BOX_COST,
            "rarity": rarity,
            "rarity_label": RARITY_LABELS[rarity],
            "badge": badge,
        }

    entry, event = _shop_mutation(
        user_id, display_name, transaction_id, "badge-box", mutate
    )
    details = event.get("details", {}) if isinstance(event, dict) else {}
    return {
        "badge": details.get("badge", badge),
        "rarity": details.get("rarity", rarity),
        "rarity_label": details.get("rarity_label", RARITY_LABELS.get(rarity, rarity.title())),
        "coins": float(entry.get("coins", 0)),
        "profile": {"user_id": str(user_id), **entry},
    }


def equip_badge(user_id, display_name, badge, transaction_id):
    badge = str(badge)
    def mutate(entry):
        if badge not in entry.get("badges", []):
            raise ValueError("You do not own that badge.")
        entry["active_badge"] = badge
        return {"badge": badge}
    entry, _ = _shop_mutation(user_id, display_name, transaction_id, "equip-badge", mutate)
    return {"user_id": str(user_id), **entry}


def buy_board(user_id, display_name, board_name, transaction_id):
    board_name = str(board_name).casefold()
    if board_name not in BOARD_THEMES or board_name == "classic":
        raise ValueError("Unknown or free default board theme.")
    def mutate(entry):
        if board_name in entry.get("boards", []):
            raise ValueError("You already own that board theme.")
        before = float(entry.get("coins", 0))
        if before + 1e-9 < BOARD_COST:
            raise ValueError(
                f"Not enough coins. Need {format_points(BOARD_COST)}, have {format_points(before)}."
            )
        entry["coins"] = round(before - BOARD_COST, 3)
        entry.setdefault("boards", []).append(board_name)
        return {"spent": BOARD_COST, "board": board_name}
    entry, _ = _shop_mutation(user_id, display_name, transaction_id, "buy-board", mutate)
    return {"user_id": str(user_id), **entry}


def equip_board(user_id, display_name, board_name, transaction_id):
    board_name = str(board_name).casefold()
    if board_name not in BOARD_THEMES:
        raise ValueError("Unknown board theme.")
    def mutate(entry):
        if board_name != "classic" and board_name not in entry.get("boards", []):
            raise ValueError("You do not own that board theme.")
        entry["active_board"] = board_name
        return {"board": board_name}
    entry, _ = _shop_mutation(user_id, display_name, transaction_id, "equip-board", mutate)
    return {"user_id": str(user_id), **entry}


def buy_color(user_id, display_name, color_name, transaction_id):
    color_name = str(color_name).casefold()
    if color_name not in NAME_COLORS:
        raise ValueError("Unknown shop color.")
    def mutate(entry):
        if color_name in entry.get("colors", []):
            raise ValueError("You already own that color.")
        before = float(entry.get("coins", 0))
        if before + 1e-9 < COLOR_COST:
            raise ValueError(
                f"Not enough coins. Need {format_points(COLOR_COST)}, have {format_points(before)}."
            )
        entry["coins"] = round(before - COLOR_COST, 3)
        entry.setdefault("colors", []).append(color_name)
        return {"spent": COLOR_COST, "color": color_name}
    entry, _ = _shop_mutation(user_id, display_name, transaction_id, "buy-color", mutate)
    return {"user_id": str(user_id), **entry}


def equip_color(user_id, display_name, color_name, transaction_id):
    color_name = str(color_name).casefold().strip()
    if color_name and color_name not in NAME_COLORS:
        raise ValueError("Unknown shop color.")
    def mutate(entry):
        if color_name and color_name not in entry.get("colors", []):
            raise ValueError("You do not own that color.")
        entry["active_color"] = color_name
        return {"color": color_name}
    entry, _ = _shop_mutation(user_id, display_name, transaction_id, "equip-color", mutate)
    return {"user_id": str(user_id), **entry}


def format_points(value):
    value = float(value)
    if value.is_integer():
        return str(int(value))
    return f"{value:.3f}".rstrip("0").rstrip(".")


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
            f"**#{i + 1} {entry.get('active_badge', '') + ' ' if entry.get('active_badge') else ''}{entry.get('name', 'Unknown')} — "
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
            f"{prefix} {entry.get('active_badge', '') + ' ' if entry.get('active_badge') else ''}{entry.get('name', 'Unknown')} — "
            f"**{format_points(points)} {word}**"
        )

    return "\n".join(lines)
