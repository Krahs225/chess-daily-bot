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

EVENT_DIR = "guess_leaderboard_events"
LEGACY_FILE = "guess_leaderboard.json"

# Immutable per-vote events + a compact snapshot power !stats. This is kept
# separate from the points ledger so old leaderboard behavior stays untouched.
STATS_EVENT_DIR = "guess_stats_events"
STATS_FILE = "guess_stats.json"
GUESS_STATS_BUILD = "guess-stats-v1-all-guess-games-2026-09-03"

_LOCK = threading.Lock()
MAX_RETRIES = 12


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


def _configure_git():
    _run([
        "git",
        "config",
        "user.name",
        "Guess Games Ledger",
    ])
    _run([
        "git",
        "config",
        "user.email",
        "guess-games-ledger@users.noreply.github.com",
    ])


def _fetch():
    result = _run([
        "git",
        "fetch",
        "origin",
        _branch(),
    ])
    return result.returncode == 0


def _origin_file(path):
    result = _run([
        "git",
        "show",
        f"origin/{_branch()}:{path}",
    ])

    if result.returncode != 0:
        return None

    return result.stdout


def _origin_legacy_scores():
    raw = _origin_file(
        LEGACY_FILE
    )

    if not raw:
        return {}

    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _origin_events():
    """
    Read all immutable event files from origin in one git-archive call.
    The event files are the source of truth.
    """
    result = subprocess.run(
        [
            "git",
            "archive",
            "--format=tar",
            f"origin/{_branch()}",
            EVENT_DIR,
        ],
        capture_output=True,
    )

    if result.returncode != 0:
        return {}

    events = {}

    try:
        with tarfile.open(
            fileobj=io.BytesIO(
                result.stdout
            ),
            mode="r:",
        ) as archive:

            for member in archive.getmembers():

                if not member.isfile():
                    continue

                if not member.name.startswith(
                    EVENT_DIR + "/"
                ):
                    continue

                extracted = archive.extractfile(
                    member
                )

                if extracted is None:
                    continue

                try:
                    event = json.loads(
                        extracted.read()
                    )

                    tx_id = event.get(
                        "transaction_id"
                    )

                    if tx_id:
                        events[str(tx_id)] = event

                except Exception:
                    continue

    except Exception:
        return {}

    return events


def _encode_name(name):
    raw = str(
        name or "Unknown"
    ).encode(
        "utf-8"
    )

    return base64.urlsafe_b64encode(
        raw
    ).decode(
        "ascii"
    ).rstrip("=")


def _event_filename(
    transaction_id
):
    digest = hashlib.sha256(
        str(
            transaction_id
        ).encode(
            "utf-8"
        )
    ).hexdigest()

    return (
        f"{EVENT_DIR}/"
        f"{digest}.json"
    )


def _event_payload(
    transaction_id,
    user_id,
    display_name,
    amount,
    source,
):
    return {
        "transaction_id":
            str(transaction_id),
        "user_id":
            str(user_id),
        "display_name":
            str(display_name),
        "amount":
            round(float(amount), 3),
        "source":
            source,
        "created_at":
            int(time.time()),
    }


def _write_event(
    path,
    payload,
):
    target = Path(path)

    target.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    temp = target.with_suffix(
        ".tmp"
    )

    with temp.open(
        "w",
        encoding="utf-8",
    ) as file:

        json.dump(
            payload,
            file,
            indent=2,
            ensure_ascii=False,
        )

        file.write("\n")

    temp.replace(
        target
    )


def _reset_to_origin():
    result = _run([
        "git",
        "reset",
        "--hard",
        f"origin/{_branch()}",
    ])

    return result.returncode == 0


def _commit_push(paths):
    _configure_git()

    add = _run([
        "git",
        "add",
        *paths,
    ])

    if add.returncode != 0:
        return False

    commit = _run([
        "git",
        "commit",
        "-m",
        "Add guess leaderboard event",
    ])

    if commit.returncode != 0:
        # Nothing new means the transaction already exists.
        return False

    push = _run([
        "git",
        "push",
        "origin",
        f"HEAD:{_branch()}",
    ])

    return push.returncode == 0


def _snapshot(events, legacy):
    """
    Derive totals from immutable events.

    If there are NO events yet, preserve the old leaderboard as a
    one-time legacy starting point. The first point transaction creates
    deterministic baseline events for those old scores.
    """
    if not events:
        totals = {}
        for uid, entry in legacy.items():
            points = float(
                entry.get(
                    "points",
                    0,
                )
            )
            if points:
                totals[str(uid)] = {
                    "points": points,
                    "name": entry.get(
                        "name",
                        "Unknown",
                    ),
                    "last_created": 0,
                }
        return totals

    totals = {}

    for event in events.values():

        uid = str(
            event.get(
                "user_id",
                "",
            )
        )

        if not uid:
            continue

        amount = float(
            event.get(
                "amount",
                0,
            )
        )

        entry = totals.setdefault(
            uid,
            {
                "points": 0.0,
                "name": "Unknown",
                "last_created": -1,
            },
        )

        entry["points"] = round(
            entry["points"] + amount,
            3,
        )

        created = int(
            event.get(
                "created_at",
                0,
            )
        )

        if created >= entry["last_created"]:

            entry["last_created"] = (
                created
            )

            entry["name"] = event.get(
                "display_name",
                "Unknown",
            )

    return totals


def _ensure_legacy_baselines(
    events,
    legacy,
):
    """
    Convert the old mutable leaderboard into deterministic baseline
    events exactly once. Both bots may attempt this simultaneously;
    the filenames are deterministic, so it is idempotent.
    """
    if events:
        return []

    paths = []

    for uid, entry in legacy.items():

        points = float(
            entry.get(
                "points",
                0,
            )
        )

        if points <= 0:
            continue

        tx_id = (
            "baseline:"
            f"{uid}:"
            f"{points:.3f}"
        )

        path = _event_filename(
            tx_id
        )

        if Path(path).exists():
            continue

        payload = _event_payload(
            tx_id,
            uid,
            entry.get(
                "name",
                "Unknown",
            ),
            points,
            "legacy-baseline",
        )

        _write_event(
            path,
            payload,
        )

        paths.append(
            path
        )

    return paths


def add_points(
    user_id,
    display_name,
    amount,
    transaction_id,
    source="guess-games",
):
    """
    Add points exactly once.

    transaction_id MUST identify the actual game/poll + voter, e.g.
    'guess:123456789:987654321'.

    Repeating the same transaction_id never adds the points twice.
    """
    if not transaction_id:
        raise ValueError(
            "A unique transaction_id is required."
        )

    amount = float(
        amount
    )

    if amount < 0:
        raise ValueError(
            "Negative point changes are not allowed."
        )

    path = _event_filename(
        transaction_id
    )

    with _LOCK:

        for attempt in range(
            1,
            MAX_RETRIES + 1,
        ):

            if not _fetch():
                time.sleep(
                    min(
                        2,
                        attempt * 0.2,
                    )
                )
                continue

            events = _origin_events()
            legacy = _origin_legacy_scores()

            # Idempotency: this exact reward was already recorded.
            tx_hash = hashlib.sha256(
                str(
                    transaction_id
                ).encode(
                    "utf-8"
                )
            ).hexdigest()

            already_recorded = False

            for event in events.values():
                digest = hashlib.sha256(
                    str(
                        event.get(
                            "transaction_id",
                            "",
                        )
                    ).encode(
                        "utf-8"
                    )
                ).hexdigest()

                if digest == tx_hash:
                    already_recorded = True
                    break

            if already_recorded:
                snapshot = _snapshot(
                    events,
                    legacy,
                )

                return float(
                    snapshot.get(
                        str(user_id),
                        {},
                    ).get(
                        "points",
                        0,
                    )
                )

            # Start from the newest committed repository state.
            if not _reset_to_origin():
                time.sleep(
                    min(
                        2,
                        attempt * 0.2,
                    )
                )
                continue

            baseline_paths = []

            if not events:
                baseline_paths = _ensure_legacy_baselines(
                    events,
                    legacy,
                )

            payload = _event_payload(
                transaction_id,
                user_id,
                display_name,
                amount,
                source,
            )

            _write_event(
                path,
                payload,
            )

            commit_paths = (
                baseline_paths
                + [path]
            )

            if _commit_push(
                commit_paths
            ):

                # Recalculate from the committed logical state.
                # Local event + baselines are enough for this return.
                base = legacy if not events else {}
                current = _snapshot(
                    {
                        **events,
                        str(transaction_id): payload,
                    },
                    base,
                )

                return float(
                    current.get(
                        str(user_id),
                        {},
                    ).get(
                        "points",
                        0,
                    )
                )

            # Another bot changed origin. The same transaction is still
            # present locally; retry from newest origin.
            time.sleep(
                min(
                    2,
                    attempt * 0.25,
                )
            )

        raise RuntimeError(
            "Could not safely record leaderboard transaction "
            f"{transaction_id}."
        )


def _current_snapshot():
    with _LOCK:

        if not _fetch():

            return _snapshot(
                {},
                _origin_legacy_scores(),
            )

        events = _origin_events()
        legacy = _origin_legacy_scores()

        return _snapshot(
            events,
            legacy,
        )


def get_score(
    user_id,
):
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


def _ordered(
    snapshot,
):
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


def personal_ranking(
    user_id,
):
    snapshot = _current_snapshot()
    ordered = _ordered(
        snapshot
    )

    position = next(
        (
            i
            for i, (
                uid,
                _,
            ) in enumerate(
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
            if str(uid) == str(
                user_id
            )
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
    title="🏆 **Guess Games Leaderboard**",
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
            f"**{format_points(points)} {word}**"
        )

    return "\n".join(
        lines
    )

# ============================================================
# GUESS VOTE STATS
# ============================================================


def _empty_stats_snapshot():
    return {
        "version": 1,
        "build": GUESS_STATS_BUILD,
        "users": {},
    }


def _normalize_stats_snapshot(data):
    snapshot = _empty_stats_snapshot()

    if not isinstance(data, dict):
        return snapshot

    users = data.get("users", {})
    if not isinstance(users, dict):
        return snapshot

    clean_users = {}

    for uid, entry in users.items():
        if not isinstance(entry, dict):
            continue

        try:
            total = max(0, int(entry.get("total", 0)))
        except Exception:
            total = 0

        try:
            correct = max(0, int(entry.get("correct", 0)))
        except Exception:
            correct = 0

        correct = min(correct, total)
        wrong = total - correct

        clean_users[str(uid)] = {
            "name": str(entry.get("name", "Unknown")),
            "total": total,
            "correct": correct,
            "wrong": wrong,
            "updated_at": int(entry.get("updated_at", 0) or 0),
        }

    snapshot["users"] = clean_users
    return snapshot


def _origin_stats_snapshot():
    raw = _origin_file(STATS_FILE)

    if not raw:
        return _empty_stats_snapshot()

    try:
        return _normalize_stats_snapshot(
            json.loads(raw)
        )
    except Exception:
        return _empty_stats_snapshot()


def _local_stats_snapshot():
    path = Path(STATS_FILE)

    if not path.exists():
        return _empty_stats_snapshot()

    try:
        return _normalize_stats_snapshot(
            json.loads(path.read_text(encoding="utf-8"))
        )
    except Exception:
        return _empty_stats_snapshot()


def _stats_event_filename(transaction_id):
    digest = hashlib.sha256(
        str(transaction_id).encode("utf-8")
    ).hexdigest()

    return f"{STATS_EVENT_DIR}/{digest}.json"


def _stats_event_payload(
    transaction_id,
    poll_message_id,
    user_id,
    display_name,
    correct,
    source,
):
    return {
        "transaction_id": str(transaction_id),
        "poll_message_id": str(poll_message_id),
        "user_id": str(user_id),
        "display_name": str(display_name),
        "correct": bool(correct),
        "source": str(source),
        "stats_build": GUESS_STATS_BUILD,
        "created_at": int(time.time()),
    }


def _write_json_atomic(path, payload):
    target = Path(path)
    target.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    temp = target.with_suffix(
        target.suffix + ".tmp"
    )

    temp.write_text(
        json.dumps(
            payload,
            indent=2,
            ensure_ascii=False,
            sort_keys=True,
        ) + "\n",
        encoding="utf-8",
    )

    temp.replace(target)


def record_poll_votes(
    poll_message_id,
    votes,
    *,
    source="guess-games",
):
    """
    Record each Discord user's vote exactly once for this poll.

    votes is an iterable of dictionaries containing:
      user_id, display_name, correct

    The stats ledger is independent from Guess points. Correct and wrong votes
    both count as one vote, while the existing points system remains unchanged.
    """
    normalized_votes = []
    seen_users = set()

    for vote in votes or []:
        if not isinstance(vote, dict):
            continue

        user_id = str(vote.get("user_id", "")).strip()
        if not user_id or user_id in seen_users:
            continue

        seen_users.add(user_id)
        normalized_votes.append(
            {
                "user_id": user_id,
                "display_name": str(
                    vote.get("display_name", "Unknown")
                ),
                "correct": bool(vote.get("correct", False)),
            }
        )

    if not normalized_votes:
        return _current_stats_snapshot()

    with _LOCK:
        for attempt in range(1, MAX_RETRIES + 1):
            if not _fetch():
                time.sleep(
                    min(2, attempt * 0.2)
                )
                continue

            snapshot = _origin_stats_snapshot()
            pending = []

            for vote in normalized_votes:
                transaction_id = (
                    f"guess-stats:{poll_message_id}:"
                    f"{vote['user_id']}"
                )
                event_path = _stats_event_filename(
                    transaction_id
                )

                # This exact poll/user vote was already committed.
                if _origin_file(event_path) is not None:
                    continue

                pending.append(
                    (
                        transaction_id,
                        event_path,
                        vote,
                    )
                )

            if not pending:
                return snapshot

            if not _reset_to_origin():
                time.sleep(
                    min(2, attempt * 0.2)
                )
                continue

            working = _normalize_stats_snapshot(
                snapshot
            )
            users = working["users"]
            commit_paths = []
            now = int(time.time())

            for transaction_id, event_path, vote in pending:
                uid = vote["user_id"]
                entry = users.setdefault(
                    uid,
                    {
                        "name": vote["display_name"],
                        "total": 0,
                        "correct": 0,
                        "wrong": 0,
                        "updated_at": 0,
                    },
                )

                entry["name"] = vote["display_name"]
                entry["total"] = int(entry.get("total", 0)) + 1

                if vote["correct"]:
                    entry["correct"] = int(entry.get("correct", 0)) + 1

                entry["wrong"] = (
                    int(entry["total"])
                    - int(entry.get("correct", 0))
                )
                entry["updated_at"] = now

                payload = _stats_event_payload(
                    transaction_id,
                    poll_message_id,
                    uid,
                    vote["display_name"],
                    vote["correct"],
                    source,
                )
                _write_json_atomic(
                    event_path,
                    payload,
                )
                commit_paths.append(event_path)

            _write_json_atomic(
                STATS_FILE,
                working,
            )
            commit_paths.append(STATS_FILE)

            if _commit_push(commit_paths):
                return working

            # Another process changed origin. Retry from the newest state;
            # immutable event filenames keep this idempotent.
            time.sleep(
                min(2, attempt * 0.25)
            )

        raise RuntimeError(
            f"Could not safely record Guess stats for poll {poll_message_id}."
        )


def _current_stats_snapshot():
    with _LOCK:
        if _fetch():
            return _origin_stats_snapshot()

        return _local_stats_snapshot()


def _stats_result(user_id, entry, fallback_name=None):
    entry = entry if isinstance(entry, dict) else {}

    total = max(0, int(entry.get("total", 0) or 0))
    correct = max(0, int(entry.get("correct", 0) or 0))
    correct = min(correct, total)
    wrong = total - correct

    accuracy = (
        (correct / total) * 100.0
        if total
        else 0.0
    )

    return {
        "user_id": str(user_id),
        "name": str(
            entry.get(
                "name",
                fallback_name or "Unknown",
            )
        ),
        "total": total,
        "correct": correct,
        "wrong": wrong,
        "accuracy": accuracy,
    }


def guess_stats_for_user(
    user_id,
    fallback_name=None,
):
    snapshot = _current_stats_snapshot()
    entry = snapshot.get(
        "users",
        {},
    ).get(
        str(user_id)
    )

    return _stats_result(
        user_id,
        entry,
        fallback_name=fallback_name,
    )


def _stats_name_key(value):
    return "".join(
        character
        for character in str(value).casefold()
        if character.isalnum()
    )


def guess_stats_for_name(name):
    snapshot = _current_stats_snapshot()
    users = snapshot.get("users", {})

    query = _stats_name_key(name)
    if not query:
        return None

    candidates = []

    for uid, entry in users.items():
        display_name = str(
            entry.get("name", "Unknown")
        )
        key = _stats_name_key(display_name)

        if key:
            candidates.append(
                (
                    uid,
                    entry,
                    key,
                )
            )

    exact = [
        item
        for item in candidates
        if item[2] == query
    ]

    if exact:
        uid, entry, _ = max(
            exact,
            key=lambda item: int(
                item[1].get("total", 0)
            ),
        )
        return _stats_result(uid, entry)

    # Small spelling mistakes are accepted, similar to the player-info command.
    keys = sorted(
        set(item[2] for item in candidates)
    )

    if len(query) >= 4 and keys:
        from difflib import get_close_matches

        close = get_close_matches(
            query,
            keys,
            n=1,
            cutoff=0.80,
        )

        if close:
            matched_key = close[0]
            matching = [
                item
                for item in candidates
                if item[2] == matched_key
            ]
            uid, entry, _ = max(
                matching,
                key=lambda item: int(
                    item[1].get("total", 0)
                ),
            )
            return _stats_result(uid, entry)

    return None


def format_guess_stats(stats):
    stats = stats or {}

    name = str(
        stats.get("name", "Unknown")
    )
    total = int(stats.get("total", 0) or 0)
    correct = int(stats.get("correct", 0) or 0)
    wrong = int(stats.get("wrong", 0) or 0)
    accuracy = float(stats.get("accuracy", 0.0) or 0.0)

    return (
        f"📊 **Guess Stats — {name}**\n\n"
        f"🗳️ **Total votes:** {total}\n"
        f"✅ **Correct:** {correct}\n"
        f"❌ **Wrong:** {wrong}\n"
        f"🎯 **Accuracy:** {accuracy:.1f}%"
    )

