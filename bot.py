import discord

from shared_leaderboard import (
    admin_set_points as shared_admin_set_points,
    add_points as shared_add_points,
    get_score as shared_get_score,
    personal_ranking as shared_personal_ranking,
    full_leaderboard as shared_full_leaderboard,
    format_points as shared_format_points,
    LEDGER_BUILD as SHARED_LEDGER_BUILD,
    REPOSITORY_LOCK,
)
import os
import re
import requests
import chess
import chess.svg
import chess.pgn
from io import BytesIO, StringIO
import cairosvg
import asyncio
import json
import subprocess
import time
import threading
import traceback
import random
import sqlite3
from datetime import datetime, timezone

from puzzle_mode_lock import is_survival_active, active_team

from puzzle_stats import (
    PUZZLE_STATS_BUILD,
    record_puzzle_attempt,
    record_first_solve,
    puzzle_stats_for_user,
    puzzle_stats_for_name,
    format_puzzle_stats,
    format_puzzle_leaderboards,
    ACHIEVEMENT_BY_ID,
)

# Direct remote Survival-state check used by Daily/Random guards.
# This is intentionally independent of puzzle_mode_lock.py so an old/stale
# lock file cannot allow the Daily bot to consume a Survival move.
_survival_check_cache = {
    "time": 0.0,
    "active": False,
    "team": None,
}

# Short local hand-off window after !survival.
# This prevents Daily/Random from consuming the same chess move while
# Survival is starting, but it automatically expires so RP cannot get stuck.
_survival_guard_until = 0.0


def survival_guard_active():
    return time.monotonic() < _survival_guard_until


def set_survival_guard(seconds=90):
    global _survival_guard_until
    _survival_guard_until = max(
        _survival_guard_until,
        time.monotonic() + float(seconds),
    )


def clear_survival_guard():
    global _survival_guard_until
    _survival_guard_until = 0.0


def remote_survival_status():
    now = time.time()

    # Tiny cache prevents doing git work more than once per second.
    if now - _survival_check_cache["time"] < 1.0:
        return (
            _survival_check_cache["active"],
            _survival_check_cache["team"],
        )

    try:
        branch = os.getenv(
            "GITHUB_REF_NAME",
            "main",
        )

        subprocess.run(
            [
                "git",
                "fetch",
                "origin",
                branch,
            ],
            capture_output=True,
            text=True,
            timeout=8,
        )

        result = subprocess.run(
            [
                "git",
                "show",
                f"origin/{branch}:survival_runs.json",
            ],
            capture_output=True,
            text=True,
            timeout=8,
        )

        if result.returncode != 0:
            raise RuntimeError(
                "survival_runs.json not available remotely"
            )

        data = json.loads(
            result.stdout
        )

        if not isinstance(
            data,
            dict,
        ):
            raise RuntimeError(
                "invalid survival_runs.json"
            )

        teams = data.get(
            "teams",
            {},
        )

        active_team_name = None

        if isinstance(
            teams,
            dict,
        ):
            for team_data in teams.values():

                if not isinstance(
                    team_data,
                    dict,
                ):
                    continue

                run = team_data.get(
                    "current"
                )

                if not isinstance(
                    run,
                    dict,
                ):
                    continue

                if run.get(
                    "status"
                ) == "active":

                    active_team_name = (
                        team_data.get(
                            "name",
                            "Survival",
                        )
                    )

                    break

        active = (
            active_team_name
            is not None
        )

        _survival_check_cache[
            "time"
        ] = now

        _survival_check_cache[
            "active"
        ] = active

        _survival_check_cache[
            "team"
        ] = active_team_name

        return (
            active,
            active_team_name,
        )

    except Exception as error:
        # Fail CLOSED for chess-puzzle handling:
        # if we cannot verify that Survival is inactive, do not let
        # Daily/Random consume a chess move.
        print(
            f"Could not verify Survival state; blocking Daily/Random "
            f"chess handling: {error}",
            flush=True,
        )

        _survival_check_cache[
            "time"
        ] = now

        _survival_check_cache[
            "active"
        ] = True

        _survival_check_cache[
            "team"
        ] = "Survival"

        return (
            True,
            "Survival",
        )




# =========================================================
# SETTINGS
# =========================================================

TOKEN = os.getenv("DISCORD_TOKEN")
CHANNEL_ID = 1468320170891022417
GUESS_GAMES_CHANNEL_ID = 1536769340970373241

DAILY_PUZZLE_API = "https://api.chess.com/pub/puzzle"

RP_BUILD = "daily-rp-v12-offline-pool-2026-09-03"
RP_POOL_FILE = "rp_puzzle_pool.sqlite3"
RP_BANDS = (
    (1200, 1499),
    (1500, 1799),
    (1800, 2099),
    (2100, 2399),
    (2400, 2699),
    (2700, 3199),
)

BOSS_PUZZLE_CHANCE = 0.05
BOSS_RP_BAND_INDEX = len(RP_BANDS) - 1
SHARKMEISTER_DEFAULT_USER_ID = "362606514764251137"
SHARK_SPY_BUILD = "shark-spy-v1-ephemeral-status-2026-09-03"

STATE_FILE = "daily_puzzle_state.json"
LEADERBOARD_FILE = "daily_puzzle_leaderboard.json"
SCORE_EVENTS_FILE = "daily_puzzle_score_events.json"

PUZZLE_CHECK_INTERVAL = 5 * 60
LEADERBOARD_INTERVAL = 24 * 60 * 60

ANSWER_WINDOW = 12 * 60 * 60
RANDOM_ANSWER_WINDOW = 12 * 60 * 60

RUN_TIME = 5 * 60 * 60 + 50 * 60


def survival_info_text():
    return """🔥 **SURVIVAL MODE — INFO**

**Start a run**
`!survival`
→ The bot asks for a team name.

The person who starts the run is the **captain**.

**Team / run system**
- Every new run gets its own saved run record.
- The same team name can have multiple runs.
- A run can be active, paused, or dead.
- `!teamname` (for example `!thice`) lets you choose which saved run of that team you want to view.
- Team details show the run's puzzle number, status, difficulty, hearts, captain and contributors.

**Co-op / Solo**
`!solo <team name>`
→ Captain-only. Only the captain may answer an active run.

`!coop <team name>`
→ Captain-only. Everyone may answer again.

Solo/Co-op can only be changed on an **active** run. Dead runs cannot be changed.

**Stopping / resuming**
`!stopsurvival`
→ Saves and pauses the active run.

After inactivity, Survival automatically pauses after **10 minutes without activity**.

`!survival` + the same team name
→ If that team has saved runs, choose which run to continue or start a new one.

A run that died at **3/3 strikes cannot be continued** unless Sharkmeister gives it a heart first.

**Hearts / strikes**
Everyone starts with **❤️❤️❤️**.

A wrong answer costs **1 strike**.

At **3/3 strikes**, the run is **DEAD** and cannot continue normally.

**Puzzle difficulty**
- #1–10: 1200–1400
- #11–20: 1400–1550
- #21–30: 1550–1700
- #31–40: 1700–1850
- #41–50: 1850–2050
- #51–60: 2050–2250
- #61–70: 2250–2400
- #71–80: 2400–2600
- **#81+: 2600+**

**How answering works**
Everyone may answer in co-op mode.

Send one chess move at a time, such as:
`Qh6`
`Qh6+`
`f1=Q`
`O-O`
`!Qh6`

The bot automatically plays the opponent's replies.

If two people submit the same correct move at almost the same time, the duplicate is ignored and **does not cost a heart**.

Some puzzles can have multiple correct mating moves; legal alternative checkmates are accepted.

**Team leaderboard**
`!survivallb`
`!survivalboard`
`!slb`

These show **all saved Survival runs**, so the same team name can appear more than once.

**Team run details**
Use:
`!<team name>`

Example:
`!thice`

If there are multiple Thice runs, the bot lets you choose which run you want to view.

You can then see:
- puzzle number
- status
- mode (SOLO/CO-OP)
- captain
- hearts / strikes
- best difficulty
- contributors and how many correct/wrong answers they gave

**Sharkmeister-only admin commands**
`!delete <team name>`
→ Permanently removes that team's saved runs.

`!addheart <team name>`
→ Adds 1 heart to that team's current/dead run so it can be resumed.

Only **Sharkmeister** can use these two commands.

**Shared points**
Survival itself does **not** award points to the shared leaderboard.
Survival is a separate team competition.
"""


# =========================================================
# DISCORD
# =========================================================

intents = discord.Intents.default()
intents.message_content = True

client = discord.Client(intents=intents)

# Guild slash command used for a private/ephemeral puzzle status response.
# For Sharkmeister it includes the current solution; for everyone else it is
# just an ordinary bot-status command, so the private feature is not exposed.
command_tree = discord.app_commands.CommandTree(client)


# =========================================================
# SHARKMEISTER PRIVATE PUZZLE STATUS
# =========================================================

def _san_list_from_player_moves(puzzle):
    if not isinstance(puzzle, dict):
        return []

    result = []
    for move in puzzle.get("player_moves", []):
        if isinstance(move, dict):
            san = str(move.get("san", "")).strip()
        else:
            san = str(move).strip()
        if san:
            result.append(san)
    return result


def _remote_active_survival_solution():
    """Return (label, moves) for the remotely active Survival puzzle."""
    branch = os.getenv("GITHUB_REF_NAME", "main")

    subprocess.run(
        ["git", "fetch", "origin", branch],
        capture_output=True,
        text=True,
        timeout=8,
    )

    result = subprocess.run(
        ["git", "show", f"origin/{branch}:survival_runs.json"],
        capture_output=True,
        text=True,
        timeout=8,
    )

    if result.returncode != 0:
        return None

    try:
        data = json.loads(result.stdout)
    except Exception:
        return None

    teams = data.get("teams", {}) if isinstance(data, dict) else {}
    if not isinstance(teams, dict):
        return None

    for team_data in teams.values():
        if not isinstance(team_data, dict):
            continue

        run = team_data.get("current")
        if not isinstance(run, dict) or run.get("status") != "active":
            continue

        puzzle = run.get("puzzle")
        if not isinstance(puzzle, dict):
            continue

        player_color = str(puzzle.get("player_color", "")).casefold()
        moves = []
        for move in puzzle.get("solution", []):
            if not isinstance(move, dict):
                continue
            if str(move.get("color", "")).casefold() != player_color:
                continue
            san = str(move.get("san", "")).strip()
            if san:
                moves.append(san)

        if moves:
            team_name = str(team_data.get("name", "Survival"))
            number = int(run.get("puzzle_number", 0) or 0)
            return (f"Survival — {team_name} #{number}", moves)

    return None


def _current_daily_random_solution():
    latest_type = state.get("latest_puzzle_type")

    if latest_type == "random":
        puzzle = state.get("latest_random_puzzle")
        label = "Random Puzzle"
        if isinstance(puzzle, dict):
            puzzle_id = str(puzzle.get("puzzle_id", ""))
            if puzzle_id.startswith("random_lichess_"):
                label = "Exact-rating Lichess Puzzle"
            elif puzzle.get("rating") is not None:
                label = f"Random Puzzle — {puzzle.get('rating')}"
    elif latest_type == "daily":
        puzzle = state.get("current_puzzle")
        label = "Daily Puzzle"
    else:
        return None

    if not isinstance(puzzle, dict):
        return None

    # Do not advertise an old completed puzzle as the current answer.
    if puzzle.get("solved") or puzzle.get("answer_posted"):
        return None

    moves = _san_list_from_player_moves(puzzle)
    if not moves:
        return None

    return (label, moves)


def _shark_private_solution_text():
    # Survival owns chess input while active, so it gets priority.
    try:
        survival = _remote_active_survival_solution()
    except Exception as error:
        print(
            f"Shark private Survival lookup error: {error}",
            flush=True,
        )
        survival = None

    result = survival or _current_daily_random_solution()
    if result is None:
        return (
            "✅ **Puzzle bot is online.**\n"
            "No active puzzle solution is available right now."
        )

    label, moves = result
    return (
        f"🤫 **{label}**\n"
        f"**Your moves only:** `{' '.join(moves)}`\n\n"
        f"`{SHARK_SPY_BUILD}`"
    )


@command_tree.command(
    name="status",
    description="Show puzzle bot status.",
)
async def private_status_command(interaction: discord.Interaction):
    # Guess Games has its own /status handler in its own channel. Returning
    # without acknowledging here prevents the two same-token clients racing.
    if interaction.channel_id == GUESS_GAMES_CHANNEL_ID:
        return

    # Ephemeral responses are only possible as replies to an interaction.
    # This command therefore gives Sharkmeister the private answer without a DM.
    await interaction.response.defer(ephemeral=True)

    shark_id = os.getenv(
        "SHARKMEISTER_USER_ID",
        SHARKMEISTER_DEFAULT_USER_ID,
    ).strip() or SHARKMEISTER_DEFAULT_USER_ID

    if str(interaction.user.id) != shark_id:
        await interaction.edit_original_response(
            content="✅ **Puzzle bot is online.**"
        )
        return

    text = await asyncio.to_thread(
        _shark_private_solution_text
    )
    await interaction.edit_original_response(
        content=text
    )


# =========================================================
# GLOBAL DATA
# =========================================================

state = {}
LICHESS_FILTER_URL = "https://datasets-server.huggingface.co/filter"
LICHESS_DATASET = "Lichess/chess-puzzles"
LICHESS_CONFIG = "default"
LICHESS_SPLIT = "train"
LICHESS_FILTER_TIMEOUT = 20
PARQUET_LIST_URL = (
    "https://datasets-server.huggingface.co/parquet"
)
PARQUET_QUERY_TIMEOUT = 45

scores = {}

data_lock = asyncio.Lock()
github_push_lock = REPOSITORY_LOCK
github_sync_task = None
score_file_lock = threading.Lock()

# Offline RP runtime state. Every six RP requests consume every rating band
# exactly once, in a freshly shuffled order.
rp_command_lock = asyncio.Lock()
_rp_pool_lock = threading.Lock()
_rp_band_bag = []
_rp_recent_ids = []


# =========================================================
# JSON
# =========================================================

def load_json(filename, default):

    if not os.path.exists(filename):
        return default

    try:
        with open(
            filename,
            "r",
            encoding="utf-8"
        ) as file:

            return json.load(file)

    except Exception as error:

        print(
            f"Could not load {filename}: {error}",
            flush=True
        )

        return default


def save_json(filename, data):

    try:

        with open(
            filename,
            "w",
            encoding="utf-8"
        ) as file:

            json.dump(
                data,
                file,
                indent=2,
                ensure_ascii=False
            )

        return True

    except Exception as error:

        print(
            f"Could not save {filename}: {error}",
            flush=True
        )

        return False



def load_score_ledger():
    """
    Daily-only append-only score ledger.

    The existing daily_puzzle_leaderboard.json is treated as the one-time
    starting balance. After that, every new +1 / +0.5 is recorded as a
    unique transaction in daily_puzzle_score_events.json.

    Replaying the same transaction_id never changes the score twice.
    """
    legacy_scores = load_json(
        LEADERBOARD_FILE,
        {}
    )

    events = load_json(
        SCORE_EVENTS_FILE,
        []
    )

    if not isinstance(events, list):
        events = []

    # Migrate the existing Daily leaderboard exactly once in memory.
    if not events and isinstance(legacy_scores, dict):
        for user_id, entry in legacy_scores.items():

            try:
                points = float(
                    entry.get(
                        "points",
                        0
                    )
                )
            except Exception:
                points = 0.0

            if points == 0:
                continue

            events.append(
                {
                    "transaction_id":
                        f"baseline:{user_id}:{points:g}",
                    "user_id":
                        str(user_id),
                    "name":
                        entry.get(
                            "name",
                            "Unknown"
                        ),
                    "points":
                        points,
                    "source":
                        "legacy-daily-baseline",
                }
            )

    totals = {}

    for event in events:

        user_id = str(
            event.get(
                "user_id",
                ""
            )
        )

        if not user_id:
            continue

        try:
            amount = float(
                event.get(
                    "points",
                    0
                )
            )
        except Exception:
            continue

        entry = totals.setdefault(
            user_id,
            {
                "name":
                    event.get(
                        "name",
                        "Unknown"
                    ),
                "points":
                    0.0,
            }
        )

        entry["points"] = round(
            float(
                entry["points"]
            ) + amount,
            2
        )

        if event.get("name"):
            entry["name"] = event["name"]

    return totals, events


def append_score_transaction(
    user_id,
    display_name,
    points,
    transaction_id,
    source,
):
    """
    Add one Daily score transaction exactly once.
    Returns (added, new_total).
    """
    global scores

    try:
        points = float(points)
    except Exception as error:
        raise ValueError(
            f"Invalid point amount: {error}"
        )

    if points < 0:
        raise ValueError(
            "Negative point changes are not allowed."
        )

    with score_file_lock:
        current_scores, events = load_score_ledger()

        existing_ids = {
            str(
                event.get(
                    "transaction_id",
                    ""
                )
            )
            for event in events
        }

        if str(transaction_id) in existing_ids:
            scores = current_scores

            return (
                False,
                float(
                    current_scores.get(
                        str(user_id),
                        {}
                    ).get(
                        "points",
                        0
                    )
                )
            )

        events.append(
            {
                "transaction_id":
                    str(transaction_id),
                "user_id":
                    str(user_id),
                "name":
                    str(display_name),
                "points":
                    points,
                "source":
                    source,
            }
        )

        totals, _ = rebuild_scores_from_events(
            events
        )

        scores = totals

        save_json(
            SCORE_EVENTS_FILE,
            events
        )

        save_json(
            LEADERBOARD_FILE,
            scores
        )

        return (
            True,
            float(
                scores.get(
                    str(user_id),
                    {}
                ).get(
                    "points",
                    0
                )
            )
        )


def rebuild_scores_from_events(
    events
):
    totals = {}

    for event in events:

        user_id = str(
            event.get(
                "user_id",
                ""
            )
        )

        if not user_id:
            continue

        try:
            amount = float(
                event.get(
                    "points",
                    0
                )
            )
        except Exception:
            continue

        entry = totals.setdefault(
            user_id,
            {
                "name":
                    event.get(
                        "name",
                        "Unknown"
                    ),
                "points":
                    0.0,
            }
        )

        entry["points"] = round(
            float(
                entry["points"]
            ) + amount,
            2
        )

        if event.get("name"):
            entry["name"] = event["name"]

    return totals, events



# =========================================================
# GITHUB SAVE
# =========================================================

def push_to_github():

    # Shared leaderboard updates and Daily state updates use the same
    # repository lock. This prevents two Git operations from colliding.
    with REPOSITORY_LOCK:
        try:
            subprocess.run(
                ["git", "config", "user.name", "Daily Puzzle Bot"],
                check=True,
                capture_output=True,
            )
            subprocess.run(
                [
                    "git",
                    "config",
                    "user.email",
                    "daily-puzzle-bot@users.noreply.github.com",
                ],
                check=True,
                capture_output=True,
            )

            # bot.py is NOT an owner of shared leaderboard files.
            # Only persist Daily puzzle state here.
            subprocess.run(
                ["git", "add", STATE_FILE],
                check=True,
                capture_output=True,
            )

            commit = subprocess.run(
                ["git", "commit", "-m", "Update Daily Puzzle state"],
                capture_output=True,
                text=True,
            )

            if commit.returncode != 0:
                return

            branch = os.getenv("GITHUB_REF_NAME", "main")

            # A leaderboard/Survival process may have advanced origin.
            # Rebase this state-only commit before pushing.
            for attempt in range(1, 5):
                pull = subprocess.run(
                    ["git", "pull", "--rebase", "origin", branch],
                    capture_output=True,
                    text=True,
                )

                if pull.returncode != 0:
                    subprocess.run(
                        ["git", "rebase", "--abort"],
                        capture_output=True,
                    )
                    time.sleep(0.25 * attempt)
                    continue

                push = subprocess.run(
                    ["git", "push", "origin", f"HEAD:{branch}"],
                    capture_output=True,
                    text=True,
                )

                if push.returncode == 0:
                    print("Daily puzzle state saved to GitHub.", flush=True)
                    return

                time.sleep(0.25 * attempt)

            raise RuntimeError("Could not push Daily puzzle state after retries.")

        except Exception as error:
            print(
                f"Could not push Daily puzzle state to GitHub: {error}",
                flush=True,
            )

def queue_github_sync():

    global github_sync_task

    if (
        github_sync_task is not None
        and not github_sync_task.done()
    ):
        return

    github_sync_task = asyncio.create_task(
        asyncio.to_thread(
            push_to_github
        )
    )




async def save_all():

    save_json(
        STATE_FILE,
        state
    )

    queue_github_sync()



# =========================================================
# FETCH DAILY PUZZLE
# =========================================================

def fetch_daily_puzzle():

    response = requests.get(
        DAILY_PUZZLE_API,
        headers={
            "User-Agent":
                "DailyChessPuzzleBot/1.0"
        },
        timeout=15
    )

    if response.status_code != 200:

        raise RuntimeError(
            f"Chess.com returned HTTP "
            f"{response.status_code}"
        )

    data = response.json()

    if not data.get("fen"):
        raise RuntimeError(
            "Daily puzzle has no FEN."
        )

    if not data.get("pgn"):
        raise RuntimeError(
            "Daily puzzle has no PGN."
        )

    return data


# =========================================================
# FETCH RANDOM PUZZLE
# =========================================================

def _next_rp_band():
    global _rp_band_bag

    if not _rp_band_bag:
        _rp_band_bag = list(range(len(RP_BANDS)))
        random.shuffle(_rp_band_bag)

    return _rp_band_bag.pop()


def fetch_random_puzzle(force_band=None):
    """
    Pick one RP puzzle from the prebuilt local SQLite pool.

    Runtime RP performs ZERO HTTP requests. The pool is generated separately
    from the official downloadable Lichess puzzle database. Every six RP
    selections use all six rating bands exactly once in random order.

    force_band is used only for Boss Puzzles and deliberately does NOT consume
    an entry from the normal six-band shuffled bag.
    """
    global _rp_recent_ids

    if not os.path.exists(RP_POOL_FILE):
        raise RuntimeError(
            f"Offline RP pool '{RP_POOL_FILE}' is missing. "
            "Run the Build RP Puzzle Pool workflow first."
        )

    with _rp_pool_lock:
        if force_band is None:
            band = _next_rp_band()
        else:
            band = int(force_band)
            if not (0 <= band < len(RP_BANDS)):
                raise RuntimeError(f"Invalid RP band: {band}")

        minimum, maximum = RP_BANDS[band]

        con = sqlite3.connect(
            f"file:{RP_POOL_FILE}?mode=ro",
            uri=True,
            timeout=5,
        )

        try:
            count = int(
                con.execute(
                    "SELECT COUNT(*) FROM puzzles WHERE band = ?",
                    (band,),
                ).fetchone()[0]
            )

            if count <= 0:
                raise RuntimeError(
                    f"Offline RP band {minimum}-{maximum} is empty."
                )

            row = None
            recent = set(_rp_recent_ids[-1000:])

            # A 50k band makes a repeat extremely unlikely, but retry a few
            # offsets so recently used puzzles are explicitly avoided.
            for _ in range(12):
                offset = random.randrange(count)
                candidate = con.execute(
                    """
                    SELECT puzzle_id, fen, moves, rating, band
                    FROM puzzles
                    WHERE band = ?
                    LIMIT 1 OFFSET ?
                    """,
                    (band, offset),
                ).fetchone()

                if candidate and str(candidate[0]) not in recent:
                    row = candidate
                    break

            if row is None:
                offset = random.randrange(count)
                row = con.execute(
                    """
                    SELECT puzzle_id, fen, moves, rating, band
                    FROM puzzles
                    WHERE band = ?
                    LIMIT 1 OFFSET ?
                    """,
                    (band, offset),
                ).fetchone()

        finally:
            con.close()

        if not row:
            raise RuntimeError(
                f"Could not select an offline RP puzzle in {minimum}-{maximum}."
            )

        puzzle_id, raw_fen, moves_text, rating, stored_band = row
        rating = int(rating)
        stored_band = int(stored_band)

        if stored_band != band or not (minimum <= rating <= maximum):
            raise RuntimeError(
                "Offline RP pool returned a puzzle outside its rating band."
            )

        moves = str(moves_text).split()
        if len(moves) < 2:
            raise RuntimeError("Offline RP puzzle has no solution line.")

        # Lichess database FEN is before the opponent's setup move. Play that
        # move first; the resulting position is what Discord users must solve.
        board = board_from_fen_safe(str(raw_fen))
        first_move = chess.Move.from_uci(moves[0])
        if first_move not in board.legal_moves:
            raise RuntimeError("Offline RP puzzle has an illegal setup move.")
        board.push(first_move)

        puzzle_fen = board.fen()
        solution_san = []

        for uci in moves[1:]:
            move = chess.Move.from_uci(str(uci))
            if move not in board.legal_moves:
                raise RuntimeError("Offline RP puzzle has an illegal solution move.")
            solution_san.append(board.san(move))
            board.push(move)

        _rp_recent_ids.append(str(puzzle_id))
        _rp_recent_ids = _rp_recent_ids[-1000:]

        return {
            "fen": puzzle_fen,
            "pgn": " ".join(solution_san),
            "url": f"https://lichess.org/training/{puzzle_id}",
            "title": f"Lichess • {rating}",
            "lichess_id": str(puzzle_id),
            "rating": rating,
            "rp_band": band,
        }


# =========================================================
# SAFE FEN / BOARD HELPERS
# =========================================================

def sanitize_fen(fen):
    """
    Chess.com sometimes returns perfectly valid-looking FEN data
    that can expose compatibility issues in older python-chess builds.
    Normalize the six FEN fields and keep castling rights explicit.
    """
    parts = str(fen).strip().split()

    if len(parts) < 4:
        raise RuntimeError("Random puzzle FEN is incomplete.")

    # Fill optional FEN fields.
    while len(parts) < 6:
        if len(parts) == 4:
            parts.append("0")
        elif len(parts) == 5:
            parts.append("1")

    # Castling rights must always be a string consisting of KQkq or -.
    castling = parts[2]
    if castling == "" or castling == "-":
        parts[2] = "-"
    else:
        cleaned = "".join(
            c for c in "KQkq"
            if c in castling
        )
        parts[2] = cleaned or "-"

    # Normalize active color.
    parts[1] = "b" if parts[1].lower() == "b" else "w"

    # Normalize en-passant.
    if parts[3] == "":
        parts[3] = "-"

    try:
        return " ".join(parts[:6])
    except Exception as error:
        raise RuntimeError(
            f"Could not normalize FEN: {error}"
        )


def board_from_fen_safe(fen):
    """
    Build a board manually instead of letting python-chess parse the
    complete FEN in one step. This avoids the str/bool XOR bug that can
    occur in some python-chess/FEN combinations.
    """
    clean_fen = sanitize_fen(fen)
    parts = clean_fen.split()

    board = chess.Board(None)

    # Piece placement.
    board.set_board_fen(parts[0])

    # Side to move.
    # IMPORTANT:
    # python-chess represents colors internally as booleans:
    # True = White, False = Black.
    #
    # Use literal booleans here instead of chess.WHITE/chess.BLACK
    # so this still works if a conflicting "chess" package exposes
    # those names as strings.
    board.turn = (
        False
        if parts[1].lower() == "b"
        else True
    )

    # Castling rights as an integer bitboard.
    #
    # Square indexes are:
    # a1=0, h1=7, a8=56, h8=63.
    rights = 0

    if parts[2] != "-":
        if "K" in parts[2]:
            rights |= chess.BB_SQUARES[7]   # h1
        if "Q" in parts[2]:
            rights |= chess.BB_SQUARES[0]   # a1
        if "k" in parts[2]:
            rights |= chess.BB_SQUARES[63]  # h8
        if "q" in parts[2]:
            rights |= chess.BB_SQUARES[56]  # a8

    board.castling_rights = int(rights)

    # En-passant square.
    ep = parts[3]
    if ep == "-":
        board.ep_square = None
    else:
        board.ep_square = chess.parse_square(ep)

    # Move counters.
    try:
        board.halfmove_clock = int(parts[4])
    except Exception:
        board.halfmove_clock = 0

    try:
        board.fullmove_number = int(parts[5])
    except Exception:
        board.fullmove_number = 1

    return board


# =========================================================
# PARSE PUZZLE SOLUTION
# =========================================================


def _strip_pgn_headers_and_noise(pgn_text):
    """
    Extract SAN move tokens without invoking chess.pgn.read_game().
    This avoids python-chess PGN parser compatibility issues with some
    Chess.com puzzle FEN headers.
    """
    text = str(pgn_text)

    # Remove tag pairs such as [FEN "..."] and [SetUp "1"].
    text = re.sub(
        r'(?m)^\s*\[[^\]]*\]\s*$',
        ' ',
        text
    )

    # Remove comments.
    text = re.sub(
        r'\{.*?\}',
        ' ',
        text,
        flags=re.DOTALL
    )

    # Remove semicolon comments.
    text = re.sub(
        r';[^\n]*',
        ' ',
        text
    )

    # Remove recursive parenthesized variations. A small loop is enough
    # for normal Chess.com PGNs and avoids pulling alternative lines in.
    for _ in range(8):
        new_text = re.sub(
            r'\([^()]*\)',
            ' ',
            text
        )
        if new_text == text:
            break
        text = new_text

    # Remove NAGs.
    text = re.sub(
        r'\$\d+',
        ' ',
        text
    )

    # Protect move numbers such as 1... and 12.
    tokens = text.replace("\n", " ").split()

    result = []

    for token in tokens:
        token = token.strip()

        if not token:
            continue

        # Move numbers: 1. 12. 12... etc.
        if re.fullmatch(r'\d+\.(\.\.)?', token):
            continue

        # Game results.
        if token in {
            "1-0",
            "0-1",
            "1/2-1/2",
            "*"
        }:
            continue

        # Occasionally a move number is attached to SAN:
        # 12.Qxe5 or 12...Qxe5.
        token = re.sub(
            r'^\d+\.(\.\.)?',
            '',
            token
        )

        if token:
            result.append(token)

    return result


def _parse_san_sequence(
    board,
    tokens
):
    """
    Parse SAN tokens from a starting board and return the moves with
    UCI/SAN/color. No PGN parser is used.
    """
    parsed = []

    for token in tokens:

        try:
            move = board.parse_san(token)
        except Exception:
            return None

        parsed.append(
            {
                "uci": move.uci(),
                "san": board.san(move),
                "color": (
                    "white"
                    if board.turn
                    else "black"
                )
            }
        )

        board.push(move)

    return parsed


def _extract_header_fen(pgn_text):
    match = re.search(
        r'(?mi)^\s*\[FEN\s+"([^"]+)"\]\s*$',
        str(pgn_text)
    )

    if not match:
        return None

    return match.group(1)


def get_solution(data):

    try:
        target_fen = sanitize_fen(
            data["fen"]
        )

        target_board = board_from_fen_safe(
            target_fen
        )

        tokens = _strip_pgn_headers_and_noise(
            data["pgn"]
        )

        if not tokens:
            raise RuntimeError(
                "Random puzzle PGN contains no SAN moves."
            )

        # ---------------------------------------------------------
        # MODE 1: The PGN starts directly from the puzzle FEN.
        # This is the normal form for Chess.com puzzle API data.
        # ---------------------------------------------------------

        puzzle_board = board_from_fen_safe(
            target_fen
        )

        solution = _parse_san_sequence(
            puzzle_board,
            tokens
        )

        if solution:
            start_index = 0

        else:
            # -----------------------------------------------------
            # MODE 2: The PGN contains the original game from an
            # earlier position. Replay it from the PGN header FEN
            # (or standard chess) until the API puzzle FEN appears.
            # -----------------------------------------------------

            header_fen = _extract_header_fen(
                data["pgn"]
            )

            if header_fen:
                replay_board = board_from_fen_safe(
                    sanitize_fen(header_fen)
                )
            else:
                replay_board = board_from_fen_safe(
                    "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/"
                    "RNBQKBNR w KQkq - 0 1"
                )

            parsed_before_puzzle = []

            start_index = None

            for index, token in enumerate(tokens):

                if (
                    replay_board.board_fen()
                    == target_board.board_fen()
                    and replay_board.turn
                    == target_board.turn
                ):
                    start_index = index
                    break

                try:
                    move = replay_board.parse_san(
                        token
                    )
                except Exception:
                    break

                parsed_before_puzzle.append(
                    move
                )

                replay_board.push(
                    move
                )

            if start_index is None:

                if (
                    replay_board.board_fen()
                    == target_board.board_fen()
                    and replay_board.turn
                    == target_board.turn
                ):
                    start_index = len(tokens)

            if start_index is None:
                raise RuntimeError(
                    "Could not match the puzzle FEN to the "
                    "random puzzle PGN. The PGN is neither a "
                    "solution line starting from the puzzle "
                    "position nor a replayable full-game line."
                )

            solution_board = board_from_fen_safe(
                target_fen
            )

            solution = _parse_san_sequence(
                solution_board,
                tokens[start_index:]
            )

            if not solution:
                raise RuntimeError(
                    "The random puzzle PGN contains no legal "
                    "solution moves after the puzzle position."
                )

        player_color = (
            "white"
            if target_board.turn
            else "black"
        )

        player_moves = [
            move
            for move in solution
            if move["color"] == player_color
        ]

        if not player_moves:
            raise RuntimeError(
                "Random puzzle has no moves for the side to solve."
            )

        return {
            "all_moves": solution,
            "player_moves": player_moves,
            "player_color": player_color,
            "player_move_count": len(player_moves),
            "first_uci": solution[0]["uci"]
        }

    except RuntimeError:
        raise

    except Exception as error:
        raise RuntimeError(
            f"Random puzzle solution parsing failed: {error}"
        )


def build_puzzle(data):

    solution = get_solution(
        data
    )

    return {
        "url":
            data.get("url"),

        "title":
            data.get(
                "title",
                "Chess Puzzle"
            ),

        "fen":
            data["fen"],

        # Runtime state for interactive random puzzles.
        # Daily puzzles continue to use the original FEN.
        "current_fen":
            data["fen"],

        "pgn":
            data["pgn"],

        "all_moves":
            solution["all_moves"],

        "player_moves":
            solution["player_moves"],

        "player_color":
            solution["player_color"],

        "player_move_count":
            solution["player_move_count"],

        "posted_at":
            None,

        "answer_posted":
            False,

        "winner_user_id":
            None,

        "winner_name":
            None,

        "latest_attempts":
            {},

        "puzzle_id":
            None
    }



def _extract_exact_rating_row(
    wrapper,
    rating,
):
    item = (
        wrapper.get(
            "row",
            wrapper,
        )
        if isinstance(wrapper, dict)
        else None
    )

    if not isinstance(
        item,
        dict,
    ):
        return None

    try:
        row_rating = int(
            item.get(
                "Rating"
            )
        )
    except Exception:
        return None

    if row_rating != int(
        rating
    ):
        return None

    puzzle_id = item.get(
        "PuzzleId"
    )
    fen = item.get(
        "FEN"
    )
    moves = item.get(
        "Moves"
    )

    if not puzzle_id or not fen or not moves:
        return None

    if isinstance(
        moves,
        str,
    ):
        moves = moves.split()

    if not isinstance(
        moves,
        list,
    ) or len(moves) < 2:
        return None

    return {
        "PuzzleId":
            str(puzzle_id),
        "FEN":
            str(fen),
        "Moves":
            [
                str(move)
                for move in moves
            ],
        "Rating":
            row_rating,
        "Themes":
            item.get(
                "Themes",
                "",
            ),
    }


def _request_lichess_filter(
    where,
    rating,
):
    response = requests.get(
        LICHESS_FILTER_URL,
        params={
            "dataset":
                LICHESS_DATASET,
            "config":
                LICHESS_CONFIG,
            "split":
                LICHESS_SPLIT,
            "where":
                where,
            "offset":
                0,
            "length":
                100,
        },
        headers={
            "Accept":
                "application/json",
            "User-Agent":
                "Chess-Puzzle-Bot/1.2",
        },
        timeout=LICHESS_FILTER_TIMEOUT,
    )

    response.raise_for_status()

    rows = response.json().get(
        "rows",
        [],
    )

    for wrapper in rows:
        row = _extract_exact_rating_row(
            wrapper,
            rating,
        )

        if row:
            return row

    return None


def fetch_lichess_parquet_urls():
    response = requests.get(
        PARQUET_LIST_URL,
        params={
            "dataset":
                LICHESS_DATASET,
        },
        headers={
            "Accept":
                "application/json",
            "User-Agent":
                "Chess-Puzzle-Bot/1.4",
        },
        timeout=15,
    )

    response.raise_for_status()

    payload = response.json()

    urls = []

    for item in payload.get(
        "parquet_files",
        [],
    ):
        if (
            item.get("split")
            == LICHESS_SPLIT
            and item.get("config")
            == LICHESS_CONFIG
            and item.get("url")
        ):
            urls.append(
                item["url"]
            )

    if not urls:
        raise RuntimeError(
            "No Lichess puzzle Parquet files were returned."
        )

    return urls


def fetch_exact_lichess_puzzle(
    rating,
    excluded_ids=None,
):
    """
    Query the current Lichess puzzle Parquet shards directly with DuckDB.

    This avoids the Dataset Viewer /filter service, which has been returning
    intermittent 500s/timeouts for the bot. DuckDB can query remote Parquet
    files over HTTP and only returns the matching puzzle row.
    """
    rating = int(
        rating
    )

    try:
        import duckdb
    except ImportError as error:
        raise RuntimeError(
            "DuckDB is not installed. Add `duckdb` to requirements.txt."
        ) from error

    urls = fetch_lichess_parquet_urls()

    con = duckdb.connect(
        database=":memory:"
    )

    try:
        con.execute(
            "INSTALL httpfs"
        )
        con.execute(
            "LOAD httpfs"
        )

        # Query all three current shards in one statement.
        parquet_list = ", ".join(
            "'" + url.replace("'", "''") + "'"
            for url in urls
        )

        excluded_ids = [
            str(value)
            for value in (excluded_ids or [])
        ][-100:]

        exclusion_sql = ""

        if excluded_ids:
            literals = ", ".join(
                "'" + value.replace("'", "''") + "'"
                for value in excluded_ids
            )
            exclusion_sql = (
                f" AND PuzzleId NOT IN ({literals})"
            )

        query = f"""
            SELECT
                PuzzleId,
                FEN,
                Moves,
                Rating,
                Themes
            FROM read_parquet(
                [{parquet_list}],
                union_by_name=true
            )
            WHERE Rating = ?
            {exclusion_sql}
            ORDER BY random()
            LIMIT 1
        """

        result = con.execute(
            query,
            [rating],
        ).fetchone()

        if not result:
            return None

        puzzle_id, fen, moves, row_rating, themes = result

        if isinstance(
            moves,
            str,
        ):
            moves = moves.split()

        if (
            not puzzle_id
            or not fen
            or not isinstance(
                moves,
                list,
            )
            or len(moves) < 2
        ):
            return None

        return {
            "PuzzleId":
                str(puzzle_id),
            "FEN":
                str(fen),
            "Moves":
                [
                    str(move)
                    for move in moves
                ],
            "Rating":
                int(row_rating),
            "Themes":
                themes or "",
        }

    finally:
        con.close()


async def post_exact_lichess_puzzle(
    channel,
    rating,
):
    try:
        used_by_rating = state.setdefault(
            "lichess_rating_used",
            {}
        )

        used_ids = list(
            used_by_rating.get(
                str(rating),
                []
            )
        )

        raw = await asyncio.to_thread(
            fetch_exact_lichess_puzzle,
            rating,
            used_ids,
        )

        if raw is None:
            await channel.send(
                f"❌ No Lichess puzzle with **exact rating {rating}** was found."
            )
            return

        # Reuse the existing Lichess -> internal puzzle structure.
        puzzle_source = {
            "id": raw["PuzzleId"],
            "fen": raw["FEN"],
            "moves": raw["Moves"],
            "rating": raw["Rating"],
            "themes": (
                " ".join(raw["Themes"])
                if isinstance(raw["Themes"], list)
                else str(raw["Themes"])
            ),
            "url": (
                f"https://lichess.org/training/"
                f"{raw['PuzzleId']}"
            ),
        }

        puzzle = build_puzzle_from_lichess(
            puzzle_source
        )

        puzzle["posted_at"] = (
            datetime.now(timezone.utc).isoformat()
        )
        puzzle["puzzle_id"] = (
            f"random_lichess_{rating}_"
            f"{raw['PuzzleId']}_"
            f"{int(time.time() * 1000)}"
        )

        used_ids.append(
            str(raw["PuzzleId"])
        )

        used_by_rating[
            str(rating)
        ] = used_ids[-100:]

        # Exact-rating puzzles use the same interactive state machine
        # as Daily/Random.
        puzzle["current_fen"] = sanitize_fen(
            puzzle["fen"]
        )
        puzzle["next_solution_index"] = 0
        puzzle["next_player_index"] = 0
        puzzle["solved"] = False
        puzzle["message_id"] = None
        puzzle["attempted_users"] = {}
        puzzle["first_move_user_id"] = None
        puzzle["first_move_user_name"] = None
        puzzle["first_move_awarded"] = False
        puzzle["helper_awarded_users"] = []
        puzzle["helper_candidate_users"] = []

        state["latest_random_puzzle"] = puzzle
        state["latest_puzzle_type"] = "random"

        await save_all()

        file, board = await make_board_file(
            puzzle,
            "lichess_rating_puzzle.png",
        )

        side = "White" if board.turn else "Black"

        if puzzle["player_move_count"] == 1:
            move_description = "Find the best move."
        else:
            move_description = (
                f"Find the best line in **"
                f"{puzzle['player_move_count']} "
                f"{move_word(puzzle['player_move_count'])}**."
            )

        embed = discord.Embed(
            title=(
                f"♟️ Lichess Puzzle — {rating}"
            ),
            description=(
                f"**{side} to move.**\n"
                f"{move_description}\n\n"
                f"Play one move at a time."
            ),
        )

        embed.set_image(
            url="attachment://lichess_rating_puzzle.png"
        )

        await channel.send(
            embed=embed,
            file=file,
        )

    except Exception as error:
        print(
            f"Exact Lichess rating puzzle error: {error}",
            flush=True,
        )

        error_text = (
            str(error).strip()
            or repr(error)
        )

        if len(error_text) > 900:
            error_text = (
                error_text[:900]
                + "..."
            )

        await channel.send(
            f"❌ **Could not load Lichess rating {rating}.**\n"
            f"`{error_text}`"
        )


# =========================================================
# BUILD LICHESS PUZZLE
# =========================================================

def build_puzzle_from_lichess(
    data,
):
    """
    Lichess dataset:
    FEN is the position before the puzzle's first (opponent) move.
    Moves contains that first move followed by the solution line.
    """
    start_board = chess.Board(
        data["fen"]
    )

    first_move = chess.Move.from_uci(
        data["moves"][0]
    )

    if first_move not in start_board.legal_moves:
        raise ValueError(
            "Lichess puzzle has an illegal first move."
        )

    start_board.push(
        first_move
    )

    player_color = (
        "white"
        if start_board.turn
        else "black"
    )

    solution = []
    board = start_board.copy()

    for uci in data["moves"][1:]:
        move = chess.Move.from_uci(
            uci
        )

        if move not in board.legal_moves:
            raise ValueError(
                "Lichess puzzle solution contains "
                "an illegal move."
            )

        solution.append(
            {
                "uci": uci,
                "san": board.san(move),
                "color": (
                    "white"
                    if board.turn
                    else "black"
                ),
            }
        )

        board.push(
            move
        )

    player_moves = [
        move
        for move in solution
        if move["color"] == player_color
    ]

    starting_fen = (
        start_board.fen()
    )

    return {
        "title":
            f"{data['rating']} • "
            f"{data['id']}",
        "fen":
            starting_fen,
        "current_fen":
            starting_fen,
        "all_moves":
            solution,
        "player_moves":
            player_moves,
        "player_color":
            player_color,
        "player_move_count":
            len(player_moves),
        "pgn":
            "",
        "url":
            data.get(
                "url",
                f"https://lichess.org/training/"
                f"{data['id']}",
            ),
    }


def board_fen_after_lichess_first(
    data,
):
    board = chess.Board(data["fen"])
    board.push(
        chess.Move.from_uci(
            data["moves"][0]
        )
    )
    return board.fen()


# =========================================================
# BOARD IMAGE
# =========================================================

async def make_board_file(
    puzzle,
    filename
):
    # For interactive random puzzles, render the CURRENT position.
    # For daily puzzles, this remains the original FEN.
    current_fen = puzzle.get(
        "current_fen",
        puzzle["fen"]
    )

    board = board_from_fen_safe(
        current_fen
    )

    # Random puzzle: keep the player's POV fixed even after
    # the final move, so the board never flips when the puzzle
    # is finished. Daily puzzles keep their normal orientation.
    if str(puzzle.get("puzzle_id", "")).startswith("random_"):
        player_color = puzzle.get(
            "player_color",
            "white"
        )
        orientation = (
            True
            if str(player_color).lower() == "white"
            else False
        )
    else:
        # board.turn is guaranteed to be a real bool.
        orientation = bool(board.turn)

    svg_board = chess.svg.board(
        board=board,
        orientation=orientation,
        size=500,
        coordinates=True
    )

    png_bytes = await asyncio.to_thread(
        cairosvg.svg2png,
        bytestring=svg_board.encode(
            "utf-8"
        )
    )

    image = BytesIO(
        png_bytes
    )

    file = discord.File(
        fp=image,
        filename=filename
    )

    return file, board


# =========================================================
# MOVE WORD
# =========================================================

def move_word(count):

    return (
        "move"
        if count == 1
        else "moves"
    )


# =========================================================
# POST DAILY PUZZLE
# =========================================================

async def post_daily_puzzle(
    channel,
    puzzle
):

    file, board = await make_board_file(
        puzzle,
        "daily_puzzle.png"
    )

    side = (
        "White"
        if board.turn
        else "Black"
    )

    count = puzzle[
        "player_move_count"
    ]

    title = puzzle[
        "title"
    ]

    embed = discord.Embed(
        title=(
            f"♟️ Daily Puzzle — {title}"
        ),
        description=(
            f"**{side} to move.**\n"
            f"Find the best line in "
            f"**{count} {move_word(count)}**."
        ),
        color=0x2ecc71
    )

    embed.set_image(
        url="attachment://daily_puzzle.png"
    )

    await channel.send(
        embed=embed,
        file=file
    )

    print(
        f"Daily Puzzle posted "
        f"({count} player moves).",
        flush=True
    )


# =========================================================
# POST RANDOM PUZZLE
# =========================================================

async def post_random_puzzle(
    channel
):
    survival_active, survival_team = remote_survival_status()

    if survival_active:
        team = survival_team or active_team() or "another team"
        await channel.send(
            f"⚠️ **Survival Mode is active for {team}.** "
            "Random Puzzle is unavailable until Survival is paused."
        )
        return

    if rp_command_lock.locked():
        await channel.send(
            "⏳ **A Random Puzzle is already loading.**"
        )
        return

    async with rp_command_lock:
        try:
            boss = random.random() < BOSS_PUZZLE_CHANCE

            if boss:
                data = await asyncio.to_thread(
                    fetch_random_puzzle,
                    BOSS_RP_BAND_INDEX,
                )
            else:
                data = await asyncio.to_thread(
                    fetch_random_puzzle
                )

            puzzle = build_puzzle(
                data
            )

            puzzle["posted_at"] = (
                datetime.now(
                    timezone.utc
                ).isoformat()
            )

            puzzle["puzzle_id"] = (
                "random_"
                + str(data.get("lichess_id", "offline"))
                + "_"
                + str(int(time.time() * 1000))
            )
            puzzle["rating"] = data.get("rating")
            puzzle["rp_band"] = data.get("rp_band")
            puzzle["boss"] = bool(boss)

            # Interactive state.
            puzzle["current_fen"] = sanitize_fen(
                puzzle["fen"]
            )
            puzzle["next_solution_index"] = 0
            puzzle["next_player_index"] = 0
            puzzle["solved"] = False
            puzzle["message_id"] = None
            puzzle["attempted_users"] = {}

            puzzle["first_move_user_id"] = None
            puzzle["first_move_user_name"] = None
            puzzle["first_move_awarded"] = False
            puzzle["helper_awarded_users"] = []
            puzzle["helper_candidate_users"] = []

            state["latest_random_puzzle"] = puzzle
            state["latest_puzzle_type"] = "random"

            await save_all()

            file, board = await make_board_file(
                puzzle,
                "random_puzzle.png"
            )

            side = (
                "White"
                if board.turn
                else "Black"
            )

            count = puzzle["player_move_count"]
            title = puzzle["title"]

            if count == 1:
                move_description = "Find the best move."
            else:
                move_description = (
                    f"Find the best line in "
                    f"**{count} {move_word(count)}**."
                )

            if boss:
                embed_title = (
                    f"☠️ BOSS PUZZLE — {data.get('rating', '?')}"
                )
                reward_text = (
                    "\n\n🔥 **Boss rewards:** first solver **+2**, "
                    "helpers **+1**."
                )
            else:
                embed_title = f"🎲 Random Puzzle — {title}"
                reward_text = ""

            embed = discord.Embed(
                title=embed_title,
                description=(
                    f"**{side} to move.**\n"
                    f"{move_description}\n\n"
                    f"You only enter **your own moves**. "
                    f"The opponent's replies will be played automatically."
                    f"{reward_text}"
                ),
                color=0x3498db
            )

            embed.set_image(
                url="attachment://random_puzzle.png"
            )

            message = await channel.send(
                embed=embed,
                file=file
            )

            puzzle["message_id"] = message.id
            save_json(STATE_FILE, state)

            print(
                f"{'BOSS ' if boss else ''}Random Puzzle posted: rating {data.get('rating')} "
                f"(band {data.get('rp_band')}, {count} player moves).",
                flush=True
            )

        except Exception as error:
            print("RANDOM PUZZLE ERROR:", flush=True)
            traceback.print_exc()

            error_text = str(error).strip() or repr(error)
            if len(error_text) > 1400:
                error_text = error_text[:1400] + "..."

            await channel.send(
                "❌ **Random Puzzle Error**\n"
                f"```{error_text}```"
            )


# =========================================================
# NORMALIZE MOVE
# =========================================================

def normalize_move(text):

    """
    Case-insensitive.

    + and # are optional.

    Examples:

        Nc6   -> nc6
        nc6   -> nc6
        NC6   -> nc6

        Bf2+  -> bf2
        bf2   -> bf2

        Qh7#  -> qh7
        qh7   -> qh7
    """

    text = text.strip()

    text = "".join(
        text.split()
    )

    text = text.casefold()

    while (
        text.endswith("+")
        or text.endswith("#")
    ):

        text = text[:-1]

    return text


# =========================================================
# MATCH ONE MOVE
# =========================================================

def san_matches_move(
    board,
    submitted,
    expected_move
):

    submitted_normalized = (
        normalize_move(submitted)
    )

    if not submitted_normalized:
        return False

    for legal_move in board.legal_moves:

        san = board.san(
            legal_move
        )

        if (
            normalize_move(san)
            == submitted_normalized
        ):

            return (
                legal_move.uci()
                == expected_move["uci"]
            )

    # UCI support
    try:

        move = board.parse_uci(
            submitted_normalized
        )

        return (
            move.uci()
            == expected_move["uci"]
        )

    except ValueError:

        return False


# =========================================================
# CHECK PLAYER'S FULL LINE
# =========================================================

def solution_is_correct(
    submitted_text,
    puzzle
):

    """
    IMPORTANT:

    The user ONLY enters their own moves.

    Example:

        Puzzle starts with White.

        Actual puzzle line:

        White: Qh7+
        Black: Kg8
        White: Qh8+
        Black: Kf7
        White: Qg7+
        Black: Ke6
        White: Qe7#

        User enters:

        !Qh7+ Qh8+ Qg7+ Qe7#

    The bot automatically plays:

        Kg8
        Kf7
        Ke6

    The user never enters Black's moves.
    """

    if not submitted_text:
        return False

    submitted_moves = (
        submitted_text.strip().split()
    )

    player_moves = puzzle.get(
        "player_moves",
        []
    )

    all_moves = puzzle.get(
        "all_moves",
        []
    )

    # Must enter exactly the number of
    # moves belonging to the player.
    if len(submitted_moves) != len(
        player_moves
    ):

        return False

    board = board_from_fen_safe(
        puzzle["fen"]
    )

    submitted_index = 0

    for expected in all_moves:

        # This is the user's move.
        if (
            expected["color"]
            == puzzle["player_color"]
        ):

            submitted = submitted_moves[
                submitted_index
            ]

            if not san_matches_move(
                board,
                submitted,
                expected
            ):

                return False

            submitted_index += 1

        # This is the opponent's move.
        #
        # We don't ask the user for it.
        # We simply play the exact move from
        # the puzzle solution.
        move = chess.Move.from_uci(
            expected["uci"]
        )

        if move not in board.legal_moves:

            return False

        board.push(move)

    return (
        submitted_index
        == len(submitted_moves)
    )


# =========================================================
# PUZZLE OPEN?
# =========================================================

def puzzle_is_open(
    puzzle,
    window
):

    if not puzzle:
        return False

    if puzzle.get(
        "answer_posted",
        False
    ):
        return False

    posted_at = puzzle.get(
        "posted_at"
    )

    if not posted_at:
        return False

    try:

        posted_time = datetime.fromisoformat(
            posted_at
        )

    except ValueError:

        return False

    elapsed = (
        datetime.now(timezone.utc)
        - posted_time
    ).total_seconds()

    return elapsed < window


# =========================================================
# SCORE
# =========================================================

def get_player_score(
    user_id
):
    return float(
        shared_get_score(
            user_id
        )
    )


# =========================================================
# PERSONAL RANKING
# =========================================================

def get_personal_ranking(
    user_id
):
    return shared_personal_ranking(
        user_id
    )


# =========================================================
# SAVE ATTEMPT
# =========================================================

async def save_attempt(
    puzzle,
    user,
    move_text,
    correct
):

    user_id = str(
        user.id
    )

    async with data_lock:

        attempts = puzzle.setdefault(
            "latest_attempts",
            {}
        )

        attempts[user_id] = {
            "name":
                user.display_name,

            "moves":
                move_text,

            "correct":
                correct,

            "timestamp":
                datetime.now(
                    timezone.utc
                ).isoformat()
        }

        save_json(
            STATE_FILE,
            state
        )


# =========================================================
# PERSONAL PUZZLE STATS / ELO / STREAK
# =========================================================

async def record_official_puzzle_result(
    puzzle,
    user,
    correct,
):
    """Record this user's first official result for this puzzle.

    Exact-rating !2500-style puzzles remain practice-only. Daily puzzles count
    for stats/streaks but have no Elo movement because Chess.com does not expose
    a trustworthy puzzle rating here. Random/Boss RP uses the real Lichess rating.
    """
    puzzle_id = str(puzzle.get("puzzle_id", ""))

    if puzzle_id.startswith("random_lichess_"):
        return None

    source = "daily" if puzzle_id.startswith("daily_") else "random"

    try:
        result = await asyncio.to_thread(
            record_puzzle_attempt,
            puzzle_id,
            user.id,
            user.display_name,
            bool(correct),
            puzzle_rating=puzzle.get("rating"),
            boss=bool(puzzle.get("boss", False)),
            source=source,
        )
    except Exception as error:
        print(
            f"Puzzle stats error for {user.display_name}: {error}",
            flush=True,
        )
        return None

    # A 10/20/30/... correct streak gives +1 shared point. The transaction ID
    # is deterministic, so a retry can never duplicate this bonus.
    if result.get("streak_bonus"):
        try:
            await asyncio.to_thread(
                shared_add_points,
                user.id,
                user.display_name,
                1.0,
                f"puzzle-streak-bonus:{puzzle_id}:{user.id}",
                source="puzzle-streak-bonus",
            )
        except Exception as error:
            print(
                f"Puzzle streak bonus error for {user.display_name}: {error}",
                flush=True,
            )

    return result


def achievement_unlock_text(result):
    if not result or not result.get("recorded"):
        return ""

    ids = result.get("new_achievements", [])
    names = [
        ACHIEVEMENT_BY_ID[item][0]
        for item in ids
        if item in ACHIEVEMENT_BY_ID
    ]

    if not names:
        return ""

    return "🏅 **Achievement unlocked:** " + " • ".join(
        f"**{name}**" for name in names
    )


# =========================================================
# RANDOM PUZZLE SCORING
# =========================================================

async def award_random_move_points(
    puzzle,
    user,
    first_move
):
    if str(
        puzzle.get(
            "puzzle_id",
            ""
        )
    ).startswith(
        "random_lichess_"
    ):
        return "none"

    user_id = str(user.id)

    if first_move:
        first_user_id = str(
            puzzle.get(
                "first_move_user_id",
                user_id,
            )
        )

        if user_id != first_user_id:
            return "none"

        if puzzle.get(
            "first_move_awarded",
            False,
        ):
            return "none"

        transaction_id = (
            f"puzzle:"
            f"{puzzle.get('puzzle_id', 'unknown')}:"
            f"first:{user_id}"
        )

        first_amount = (
            2.0
            if puzzle.get("boss", False)
            else 1.0
        )

        await asyncio.to_thread(
            shared_add_points,
            user.id,
            user.display_name,
            first_amount,
            transaction_id,
            source=(
                "puzzle-boss-first"
                if puzzle.get("boss", False)
                else "puzzle-first"
            ),
        )

        try:
            await asyncio.to_thread(
                record_first_solve,
                puzzle.get("puzzle_id", "unknown"),
                user.id,
                user.display_name,
                boss=bool(puzzle.get("boss", False)),
            )
        except Exception as error:
            print(
                f"Puzzle first-solve stats error for {user.display_name}: {error}",
                flush=True,
            )

        puzzle[
            "first_move_awarded"
        ] = True

        await save_all()
        return "first"

    first_user_id = str(
        puzzle.get(
            "first_move_user_id",
            "",
        )
    )

    if user_id == first_user_id:
        return "none"

    helper_users = puzzle.setdefault(
        "helper_awarded_users",
        []
    )

    if user_id in helper_users:
        return "none"

    transaction_id = (
        f"puzzle:"
        f"{puzzle.get('puzzle_id', 'unknown')}:"
        f"helper:{user_id}"
    )

    helper_amount = (
        1.0
        if puzzle.get("boss", False)
        else 0.5
    )

    await asyncio.to_thread(
        shared_add_points,
        user.id,
        user.display_name,
        helper_amount,
        transaction_id,
        source=(
            "puzzle-boss-helper"
            if puzzle.get("boss", False)
            else "puzzle-helper"
        ),
    )

    helper_users.append(
        user_id
    )

    await save_all()
    return "helper"


# =========================================================
# DAILY PUZZLE +1
# =========================================================

async def award_point(
    puzzle,
    user
):
    result = await award_random_move_points(
        puzzle,
        user,
        first_move=True,
    )

    if result != "first":
        return False

    puzzle[
        "winner_user_id"
    ] = str(user.id)

    puzzle[
        "winner_name"
    ] = user.display_name

    return True



def format_points(points):
    value = float(points)
    return str(int(value)) if value.is_integer() else f"{value:.1f}"


# =========================================================
# FULL LEADERBOARD
# =========================================================

def make_leaderboard():
    return shared_full_leaderboard(
        "🏆 **Shared Leaderboard**"
    )


# =========================================================
# HELP
# =========================================================

def help_message():

    return """🧠 **Chess Puzzle Game**

**Daily Puzzle**
`!daily <move>` — Play the Chess.com Daily Puzzle one move at a time.
`<move>` or `!<move>` works too.

**Random Puzzle**
`rp` or `!rp` — Start a random Lichess puzzle.
Play it one move at a time. A small number become **2600+ Boss Puzzles**.
Boss first solver: **+2 points**. Boss helper: **+1 point**.

**Lichess Rating Puzzle**
`!400`, `!2500`, `!2552`, etc. — load a Lichess puzzle with that **exact puzzle rating**.

**Shared Points / Personal Progress**
Daily and Random use the **shared leaderboard**.
First solver: **+1 point**
Helper: **+0.5 point**
Every **10th correct puzzle in a row** gives **+1 bonus point**.
Rated Random/Boss puzzles also update your real **Puzzle Elo**.
Your first valid attempt on each official puzzle decides your personal win/loss.

**Commands**
`!info` / `!help` / `!i` — show this info.
`!leaderboard` / `!lb` / `!l` — shared points + Top Puzzle Elo + best streaks.
`!stats` / `!me` / `!profile` — your Puzzle profile, Elo, streaks and achievements.
`!stats <name>` — view another player's Puzzle profile.
`!edit <name> <points>` — Sharkmeister-only shared leaderboard correction.

🔥 **Survival Mode**
`!survival` — Start or resume a team Survival run.
The person who starts the run is the captain.

`!slb` / `!survivallb` / `!survivalboard` — show saved Survival runs.
`!<team>` — choose/view a saved run.

`!stopsurvival` — pause and save the active run.
`!solo <team>` — captain only; only the captain may answer.
`!coop <team>` — captain only; everyone may answer again.

Survival starts with **3 hearts**.
A wrong answer costs 1 strike.
At **3/3 strikes**, the run is **DEAD**.
After 10 minutes without activity, an active run is paused.

Everyone can help in co-op mode.
Duplicate simultaneous correct answers do not cost a heart.
Some puzzles can have multiple correct mating moves.
Promotions such as `f1=Q`, `f1=Q+`, `f1=Q#` are accepted.

The Survival leaderboard tracks **runs**, so the same team name can appear multiple times.
Survival does **not** award shared leaderboard points.
"""



# =========================================================
# POST ANSWER
# =========================================================

async def post_answer(
    channel,
    puzzle,
    puzzle_type
):

    player_moves = puzzle.get(
        "player_moves",
        []
    )

    if not player_moves:
        return

    solution_text = " ".join(
        move["san"]
        for move in player_moves
    )

    if puzzle_type == "daily":

        title = "💡 **Daily Puzzle — Answer**"

    else:

        title = "💡 **Random Puzzle — Answer**"

    await channel.send(
        f"{title}\n\n"
        f"**Your moves:** {solution_text}"
    )


# =========================================================
# FINALIZE PUZZLE
# =========================================================

async def finalize_expired_puzzle(
    channel,
    puzzle,
    puzzle_type
):

    if not puzzle:
        return

    if puzzle.get(
        "answer_posted",
        False
    ):
        return

    await post_answer(
        channel,
        puzzle,
        puzzle_type
    )

    puzzle[
        "answer_posted"
    ] = True

    save_json(
        STATE_FILE,
        state
    )

    asyncio.create_task(
        asyncio.to_thread(
            push_to_github
        )
    )


# =========================================================
# EXPIRED PUZZLES
# =========================================================

async def check_expired_puzzles(
    channel
):

    daily = state.get(
        "current_puzzle"
    )

    if daily:

        if not daily.get(
            "answer_posted",
            False
        ):

            if not puzzle_is_open(
                daily,
                ANSWER_WINDOW
            ):

                await finalize_expired_puzzle(
                    channel,
                    daily,
                    "daily"
                )

    random_puzzle = state.get(
        "latest_random_puzzle"
    )

    if random_puzzle:

        if not random_puzzle.get(
            "answer_posted",
            False
        ):

            if not puzzle_is_open(
                random_puzzle,
                RANDOM_ANSWER_WINDOW
            ):

                await finalize_expired_puzzle(
                    channel,
                    random_puzzle,
                    "random"
                )


# =========================================================
# NEW DAILY PUZZLE
# =========================================================

async def check_for_new_puzzle(
    channel
):
    survival_active, survival_team = remote_survival_status()

    if survival_active:
        print(
            f"Survival Mode is active for {survival_team or 'Survival'}; "
            "Daily Puzzle posting is paused.",
            flush=True,
        )
        return

        print(
            "Survival Mode is active; Daily Puzzle posting is paused.",
            flush=True,
        )
        return


    try:

        data = await asyncio.to_thread(
            fetch_daily_puzzle
        )

        puzzle = build_puzzle(
            data
        )

    except Exception as error:

        print(
            f"Daily puzzle error: {error}",
            flush=True
        )

        return

    current = state.get(
        "current_puzzle"
    )

    current_url = (
        current.get("url")
        if current
        else None
    )

    if current_url == puzzle["url"]:
        return

    print(
        "NEW DAILY PUZZLE DETECTED.",
        flush=True
    )

    if current:

        if not current.get(
            "answer_posted",
            False
        ):

            await finalize_expired_puzzle(
                channel,
                current,
                "daily"
            )

    puzzle[
        "posted_at"
    ] = datetime.now(
        timezone.utc
    ).isoformat()

    puzzle[
        "puzzle_id"
    ] = (
        "daily_"
        + str(
            int(
                time.time() * 1000
            )
        )
    )

    puzzle["current_fen"] = sanitize_fen(
        puzzle["fen"]
    )
    puzzle["next_solution_index"] = 0
    puzzle["next_player_index"] = 0
    puzzle["solved"] = False
    puzzle["message_id"] = None
    puzzle["attempted_users"] = {}
    puzzle["first_move_user_id"] = None
    puzzle["first_move_user_name"] = None
    puzzle["first_move_awarded"] = False
    puzzle["helper_awarded_users"] = []
    puzzle["helper_candidate_users"] = []

    state[
        "current_puzzle"
    ] = puzzle

    state[
        "latest_puzzle_type"
    ] = "daily"

    await save_all()

    await post_daily_puzzle(
        channel,
        puzzle
    )


# =========================================================
# PUZZLE LOOP
# =========================================================

async def puzzle_loop(
    channel
):

    while True:

        try:

            await check_for_new_puzzle(
                channel
            )

        except Exception as error:

            print(
                f"Puzzle loop error: {error}",
                flush=True
            )

        await asyncio.sleep(
            PUZZLE_CHECK_INTERVAL
        )


# =========================================================
# MAINTENANCE LOOP
# =========================================================

async def maintenance_loop(
    channel
):

    while True:

        try:

            await check_expired_puzzles(
                channel
            )

            today = datetime.now(
                timezone.utc
            ).date().isoformat()

            if state.get(
                "leaderboard_last_posted_date"
            ) != today:

                await channel.send(
                    make_leaderboard()
                )

                state[
                    "leaderboard_last_posted_date"
                ] = today

                save_json(
                    STATE_FILE,
                    state
                )

                queue_github_sync()

        except Exception as error:

            print(
                f"Maintenance error: {error}",
                flush=True
            )

        await asyncio.sleep(30)


# =========================================================
# RUN TIMER
# =========================================================

async def run_timer():

    await asyncio.sleep(
        RUN_TIME
    )

    print(
        "Ending run cleanly.",
        flush=True
    )

    await client.close()


# =========================================================
# FUN WRONG-ANSWER MESSAGES
# =========================================================


PERSONAL_WRONG_WRAPPERS = (
    '{name}, {roast}.',
    'Wrong, {name}. {roast}.',
    'Nope, {name}. {roast}.',
    'Bad news, {name}: {roast}.',
    'Stockfish report for {name}: {roast}.',
    '{name}, the board would like you to know that {roast}.',
    'Another one for the collection, {name}: {roast}.',
    'Puzzle verdict on {name}: {roast}.',
    '{name}, congratulations: {roast}.',
    'Live from the blunder department, {name}: {roast}.',
)

THICE_WRONG_CORES = (
    'your confidence calculated a winning line that your pieces never agreed to',
    'you found the only move that makes XD look like serious analysis',
    'your ego saw mate in three while the board saw nonsense in one',
    'you spent all that calculation just to blunder with extra paperwork',
    'your tactical vision just submitted a formal resignation',
    'the move was 2400 confidence and 900 accuracy',
    'you somehow overthought the position and still underthought the move',
    'your pieces are starting to request a second opinion',
    'you played that like the engine owed you an apology',
    'the puzzle asked for precision and you answered with pure ego',
    'your calculation tree had a lot of branches and zero fruit',
    'you found a move so creative the position wants it deleted',
    'even your XD cannot make that move look intentional',
    'you treated a forced line like an optional suggestion',
    'your brain said probably and the board said absolutely not',
    'you managed to calculate everything except the correct move',
    'that move had the confidence of a grandmaster and the evidence of a coin flip',
    'you just turned tactical confidence into tactical fiction',
    'the Dutch Defense wants no association with what just happened',
    'the French Defense has officially revoked your speaking rights',
    'you saw ghosts, tactics, sacrifices and apparently no legal solution',
    'you made the board look complicated enough to hide the fact that you were wrong',
    'your rating is doing unpaid PR work for that move',
    'you played the position like being certain automatically makes you correct',
    'your move was so convinced of itself it forgot to be good',
    "you found another line that only wins in the director's cut",
    'your calculation was deep enough to drown in and still missed the shore',
    'you made a simple puzzle look like a dissertation on being wrong',
    'the engine checked your move twice because it assumed there had to be a typo',
    'you just discovered a brand-new category between blunder and confidence trick',
    'your tactical instinct arrived, looked at the board and immediately left',
    'you played that move like everyone else was too weak to understand it',
    'the only thing more forcing than the line was your confidence',
    'you somehow made overconfidence look like an opening system',
    'your move deserves its own evaluation symbol and none of them are positive',
    'you calculated ten moves ahead and forgot move one',
    'your inner grandmaster took the day off and left the ego in charge',
    'the position was asking for chess and you gave it a TED Talk',
    'you turned a puzzle into a demonstration of why checking your work matters',
    'your move had theory, confidence and absolutely no relationship with the solution',
    'you found the best move in a completely different position',
    'your pieces followed your plan and now they want compensation',
    'you just proved that long calculation and correct calculation are different hobbies',
    'the board gave you all the clues and you filed them under irrelevant',
    'your move was so wrong it made the obvious move look brilliant',
    'you managed to lose the argument against a position that cannot speak',
    'your chess brain said trust me and that was the first warning sign',
    'you played that like a 2400 who accidentally opened the wrong puzzle',
    'your calculation had confidence, style and a missing conclusion',
    'Mr Thick has entered the chat and the correct move has quietly left',
)

STEPU_WRONG_CORES = (
    'you typed skill issue and then personally demonstrated one',
    'your confidence moved faster than your calculation and won the race',
    'you played that like bullet time controls were threatening your family',
    'the move was fast, confident and impressively unrelated to the solution',
    'you roasted the position so hard you accidentally roasted your own move',
    'your tactical instinct clicked first and asked questions never',
    'you turned wassup energy into what-was-that chess',
    'the engine saw your move and briefly considered taking the day off',
    'you played first, thought second and apparently skipped the second part',
    'your move had more swagger than accuracy',
    'you found a tactical idea that tactically loses to reality',
    'you were so quick the correct move had no chance to catch up',
    'your confidence is carrying your chess harder than your pieces are',
    'you treated calculation like an optional DLC',
    'the board asked for precision and you speedran the wrong answer',
    'you just converted a winning thought into a losing move',
    'your move entered the position with main-character energy and left as comic relief',
    'you made a one-move puzzle look like a premove accident',
    'the only thing sharper than your style was the drop in evaluation',
    'you played that like the opponent had already resigned out of fear',
    'your tactic was so practical it practically did not work',
    'you saw the move, trusted the vibe and ignored the chess',
    'your pieces are learning what skill issue means in real time',
    'you just speedran from confidence to correction',
    'the engine did not refute your move so much as publicly embarrass it',
    'you played like there were bonus points for answering before thinking',
    'your move was fearless because apparently it had never met consequences',
    'you brought blitz confidence to a position that required one extra second',
    'your tactical radar detected everything except the target',
    'you found another move that looks strong until literally anyone checks it',
    'the position offered you a solution and you chose violence against your own evaluation',
    'you made the wrong move with enough confidence to almost gaslight the board',
    'your calculation was a drive-by and the position deserved a full investigation',
    'you just proved that speed chess and speed thinking are not the same thing',
    'your move had attitude, tempo and no legal claim to being good',
    'you were already celebrating while the engine was still writing the rejection letter',
    'you played that like every tactical idea deserves to be executed immediately',
    'your move was all gas, no brakes and no destination',
    'the puzzle gave you a clean shot and you somehow hit your own position',
    'you made confidence look like a tactical weakness',
    'your chess instincts just got hit with their own skill issue',
    'you found a move so fast even the blunder had motion blur',
    'your plan was aggressive enough to attack the evaluation bar directly',
    'you played the kind of move that makes premoving look thoughtful',
    'your tactic had exactly one problem and unfortunately it was chess',
    'you turned a simple calculation into a speedrun category nobody asked for',
    'your move was brave in the same way jumping without looking is brave',
    'you roasted everyone else so often the board finally returned the favor',
    'your practical decision was extremely practical for the opponent',
    'you just gave the phrase confidence without evidence a perfect chess example',
)


def personal_wrong_message(name, cores):
    # 10 wrappers x 50 unique roast cores = exactly 500 possible messages.
    return "❌ **" + random.choice(PERSONAL_WRONG_WRAPPERS).format(
        name=name,
        roast=random.choice(cores),
    ) + "**"



NORMAL_WRONG_WRAPPERS = (
    '{name}, {roast}.',
    'Not quite, {name} — {roast}.',
    'Close, {name}: {roast}.',
    'Nope, {name} — {roast}.',
    'Almost, {name}. {roast}.',
    'Nice try, {name} — {roast}.',
    'The board says no, {name}: {roast}.',
    'The engine disagrees, {name} — {roast}.',
    'Wrong move, {name}, but {roast}.',
    'One more try, {name}: {roast}.',
)

NORMAL_WRONG_CORES = (
    'you were only one idea away',
    'that was a reasonable try',
    'the right move is still hiding',
    'you had the right kind of idea, just not the right move',
    'the position had one small trick left',
    'you were close enough to make the board nervous',
    'that move looked tempting for a reason',
    'the puzzle had a different plan',
    'you found a good-looking move, just not the best one',
    'the solution is a little more precise',
    'you were on the right track',
    'that was a very human move',
    'the tactic needs one more look',
    'there is a cleaner move in the position',
    'the board is asking for a little more calculation',
    'the idea was fine, the execution was just off',
    'you spotted something useful, but there is more',
    'the winning move is one step further',
    'that was close, but the puzzle is picky',
    'the position has a sneaky detail you missed',
    'you almost had the tactical point',
    'the engine prefers another route',
    'there is a stronger continuation available',
    'your move makes sense, but the puzzle wants something sharper',
    'you were looking in the right area of the board',
    'the answer is nearby, just not on that square',
    'the puzzle managed to dodge that attempt',
    'that move is playable-looking, but not the solution',
    'you had part of the pattern',
    'the final detail escaped this time',
    'the position still has a surprise left',
    'one extra check of the forcing moves might do it',
    'the tactic is there, but it starts differently',
    'you were closer than the evaluation bar makes it look',
    'the board wants a slightly more accurate move',
    'the idea had potential',
    'you found a candidate move, just not the winner',
    'the solution needs a bit more patience',
    'that was a solid guess',
    'the puzzle is being annoyingly specific',
    'you saw the theme, but not the exact move order',
    'there is one stronger move waiting',
    'that attempt was respectable',
    'the correct move is still within reach',
    'you were one calculation branch away',
    'the position rewards a different first move',
    'that was not far off',
    'the puzzle wants the most forcing option',
    'you had the right instinct, just the wrong finish',
    'the next attempt could easily be the one',
)


def normal_wrong_message(name):
    # 10 mild wrappers x 50 mild chess replies = exactly 500 possibilities.
    return "❌ **" + random.choice(NORMAL_WRONG_WRAPPERS).format(
        name=name,
        roast=random.choice(NORMAL_WRONG_CORES),
    ) + "**"


def wrong_message(user):
    name = user.display_name
    lower = name.casefold()

    special_lines = [
        f"❌ **Wrong, {name}. Your ego was more accurate than your move.**",
            f"❌ **Nope, {name}. Maybe calm down with the confidence.**",
            f"❌ **Bro has 2000 confidence and 900 calculation.**",
            f"❌ **{name} thought he was Magnus again.**",
            f"❌ **Your confidence is honestly more impressive than your chess.**",
            f"❌ **{name}, Stockfish has more faith in random moves than you.**",
            f"❌ **You played that with so much confidence. That's what makes it worse.**",
            f"❌ **{name}'s ego just took another critical hit.**",
            f"❌ **Bro plays like he already knows the answer. He absolutely does not.**",
            f"❌ **{name}, you're more dangerous to your own position than your opponent.**",
            f"❌ **That wasn't a blunder. That was a personality test.**",
            f"❌ **{name} found another move nobody else was brave enough to play. For good reason.**",
            f"❌ **Your rating is apparently based entirely on confidence.**",
            f"❌ **{name}, the puzzle asked for a move, not an ego trip.**",
            f"❌ **You have an incredible talent for being confidently wrong.**",
            f"❌ **{name} plays chess like nobody has ever told him no.**",
            f"❌ **That was a grandmaster move if you remove the grandmaster.**",
            f"❌ **Bro saw the solution and personally decided to avoid it.**",
            f"❌ **{name}, even your blunders have more character than that move.**",
            f"❌ **You really think you're better than you are, huh?**",
            f"❌ **{name}'s tactical vision has officially resigned.**",
            f"❌ **Your ego somehow sees every move except the correct one.**",
            f"❌ **{name}, maybe look at the board before trying to calculate it.**",
            f"❌ **That move had way more confidence than substance.**",
            f"❌ **You played that with the certainty of someone who had absolutely no idea.**",
            f"❌ **{name} is once again the victim of his own self-image.**",
            f"❌ **You think you're a chess monster. The board disagrees.**",
            f"❌ **{name}, the puzzle isn't losing to you. You're losing to the puzzle.**",
            f"❌ **Bro has a master's degree in overconfidence.**",
            f"❌ **{name}'s biggest opponent is still {name}.**",
            f"❌ **That was genuinely impressive. You managed to be completely wrong with confidence.**",
            f"❌ **{name}, your chess IQ is apparently on vacation today.**",
            f"❌ **Your move says more about your ego than your rating.**",
            f"❌ **{name}, confidence is not a chess strategy.**",
            f"❌ **The move was wrong. The confidence was even more wrong.**",
            f"❌ **You don't need to be that confident about a move that makes no sense.**",
            f"❌ **{name}, even your own pieces don't trust you anymore.**",
            f"❌ **You play like every bad idea becomes genius just because you thought of it.**",
            f"❌ **That was an ego move, not a chess move.**",
            f"❌ **{name}'s rating wants to distance itself from this move.**",
            f"❌ **You should've developed your chess skills instead of your ego.**",
            f"❌ **Bro found another move that only makes sense inside his own head.**",
            f"❌ **{name}, you're somehow turning every puzzle into a personal argument.**",
            f"❌ **You really thought you saw that coming. Adorable.**",
            f"❌ **{name}, you're not blunder-proof. You're blunder-powered.**",
            f"❌ **That move was so bad your ego is probably still defending it.**",
            f"❌ **You play with 2500 confidence and gambler accuracy.**",
            f"❌ **{name}, 'I think it's good' isn't enough.**",
            f"❌ **Bro is hallucinating tactics again.**",
            f"❌ **{name}'s self-esteem just got checkmated.**",
            f"❌ **You wanted to look clever. The board had other plans.**",
            f"❌ **{name}, you're trying to outsmart the puzzle before even understanding it.**",
            f"❌ **That was an elite-level miscalculation.**",
            f"❌ **You just got proven wrong by everybody, including the board.**",
            f"❌ **{name}, your confidence is literally the only thing that doesn't blunder.**",
            f"❌ **The engine isn't even angry. It's just disappointed.**",
            f"❌ **Bro plays like he knows a secret nobody else knows. Apparently he doesn't know the answer either.**",
            f"❌ **{name}, you're so convinced of yourself that even the numbers can't convince you.**",
            f"❌ **That wasn't a close miss. That was another continent.**",
            f"❌ **Your ego says 'brilliant.' The board says 'bro what?'**",
            f"❌ **{name}, maybe think less highly of yourself before making the move.**",
            f"❌ **That move was about as accurate as your self-assessment.**",
            f"❌ **You just delivered another masterpiece in the art of self-overestimation.**",
            f"❌ **{name}, this is exactly why you can't rely on confidence.**",
            f"❌ **Bro has officially got more confidence than calculation.**",
            f"❌ **You played that like you were quoting theory. Unfortunately, it was the wrong book.**",
            f"❌ **{name}'s brain found an answer. Just not the right one.**",
            f"❌ **You're so confident you can somehow be wrong with style.**",
            f"❌ **That was pure {name}-core.**",
            f"❌ **{name} just defeated his own hype once again.**",
            f"❌ **Your rating has nothing to do with this. This was just you.**",
            f"❌ **Bro isn't playing against the puzzle. He's playing against reality.**",
            f"❌ **{name}, even a beginner would've found that with more hesitation and better accuracy.**",
            f"❌ **That was so confidently wrong it almost became impressive.**",
            f"❌ **Your ego needs a rematch against reality.**",
            f"❌ **{name}, not every move you make is secretly brilliant.**",
            f"❌ **You thought you were a genius. The board just gave you a reality check.**",
            f"❌ **This puzzle personally offended {name}'s ego.**",
            f"❌ **{name}'s 'I see it' moment became an 'I saw absolutely nothing' moment.**",
            f"❌ **You literally chose the one move you weren't supposed to play.**",
            f"❌ **Bro wanted to prove how smart he is. Mission failed.**",
            f"❌ **{name} is somehow better at being confidently wrong than being right.**",
            f"❌ **That was more ego than elo.**",
            f"❌ **{name}, your self-confidence is currently your strongest chess piece.**",
            f"❌ **You played that like Stockfish told you to. Stockfish would fire itself.**",
            f"❌ **That move was so arrogant it almost needed a punishment.**",
            f"❌ **{name}, maybe admit that you don't actually see everything.**",
            f"❌ **The best part of that move was how sure you were about it.**",
            f"❌ **Bro found a solution that only exists in his imagination.**",
            f"❌ **{name}'s ego played a perfect game. You didn't.**",
            f"❌ **That wasn't a misclick. That was a fully conscious bad decision.**",
            f"❌ **You genuinely have a talent for choosing the one move you shouldn't play.**",
            f"❌ **{name}, even your own pieces wouldn't take you seriously right now.**",
            f"❌ **You played that like you had something to prove. The board answered for you.**",
            f"❌ **That was a monumental demonstration of overconfidence.**",
            f"❌ **{name}, maybe think less about how good you are and more about the position.**",
            f"❌ **Your ego is GM. Your moves are still waiting for the interview.**",
            f"❌ **{name}, confidence is not a substitute for vision.**",
            f"❌ **The puzzle gave you one job. You chose chaos.**",
            f"❌ **{name}, congratulations — your ego is still 2500 while that move just applied for 900 elo.**"
    ]

    if "makina" in lower:
        return random.choice(special_lines).format(
            name=name
        )

    if lower in {"thice", "mr_thice", "mr thice", "mr_thick", "mr thick"}:
        return personal_wrong_message(
            name,
            THICE_WRONG_CORES,
        )

    if lower in {"stepu", "stepu6568"}:
        return personal_wrong_message(
            name,
            STEPU_WRONG_CORES,
        )

    if "sharkmeister" in lower:
        shark_lines = [
            f"❌ **So close, {name}... Magnus would call that a mouse slip.**",
            f"❌ **Almost, {name}. That's the kind of miss Magnus gets once every 100 games.**",
            f"❌ **So close, {name}. Your inner Magnus almost found it.**",
            f"❌ **Mouse slipped, {name}? Because that was basically Magnus-level otherwise.**",
            f"❌ **Nearly, {name}. The idea was there — one tiny Magnus moment.**",
            f"❌ **Oof, {name}. Magnus would blame the mouse for that one.**",
            f"❌ **Almost, {name}. Very Magnus-before-the-mouse-slip energy.**",
            f"❌ **That was painfully close, {name}. Even Carlsen gets those.**",
            f"❌ **One tiny detail, {name}. We'll blame the mouse.**",
            f"❌ **Close enough to be a Magnus mouse-slip, {name}.**",
        ]
        return random.choice(shark_lines)

    return normal_wrong_message(name)



# =========================================================
# RANDOM PUZZLE — STEP BY STEP
# =========================================================

async def update_random_puzzle_message(
    channel,
    puzzle,
    message_text=None
):
    message_id = puzzle.get(
        "message_id"
    )

    if not message_id:
        return

    file, board = await make_board_file(
        puzzle,
        "random_puzzle.png"
    )

    remaining = (
        puzzle["player_move_count"]
        - puzzle.get("next_player_index", 0)
    )

    side = (
        "White"
        if puzzle.get("player_color") == "white"
        or puzzle.get("player_color") == chess.WHITE
        else "Black"
    )

    if puzzle.get("solved", False):
        description = (
            "🎉 **Puzzle solved!**"
        )
    elif remaining == 1:
        description = (
            f"**{side} to move.**\n"
            f"**Final move.**"
        )
    else:
        description = (
            f"**{side} to move.**\n"
            f"**{remaining} {move_word(remaining)} remaining.**"
        )

    if message_text:
        description = (
            f"{message_text}\n\n"
            + description
        )

    embed = discord.Embed(
        title=(
            f"🎲 Random Puzzle — "
            f"{puzzle['title']}"
        ),
        description=description,
        color=0x3498db
    )

    embed.set_image(
        url="attachment://random_puzzle.png"
    )

    try:
        message = await channel.fetch_message(
            message_id
        )

        await message.edit(
            embed=embed,
            attachments=[file]
        )

    except Exception as error:
        print(
            f"Could not update random puzzle message: {error}",
            flush=True
        )


async def handle_random_answer(
    message,
    puzzle,
    move_text
):
    if not puzzle:
        return

    if puzzle.get(
        "answer_posted",
        False
    ):
        return

    answer_window = (
        RANDOM_ANSWER_WINDOW
        if str(
            puzzle.get(
                "puzzle_id",
                "",
            )
        ).startswith("random_")
        else ANSWER_WINDOW
    )

    if not puzzle_is_open(
        puzzle,
        answer_window
    ):
        return

    if puzzle.get(
        "solved",
        False
    ):
        return

    player_color = puzzle[
        "player_color"
    ]

    puzzle_id = str(
        puzzle.get(
            "puzzle_id",
            "",
        )
    )

    is_daily = puzzle_id.startswith("daily_")
    practice_only = puzzle_id.startswith("random_lichess_")
    is_boss = bool(puzzle.get("boss", False))

    puzzle_label = (
        "♟️ Daily Puzzle"
        if is_daily
        else "☠️ BOSS PUZZLE"
        if is_boss
        else "🎲 Random Puzzle"
    )

    next_index = puzzle.get(
        "next_solution_index",
        0
    )

    all_moves = puzzle.get(
        "all_moves",
        []
    )

    player_moves = puzzle.get(
        "player_moves",
        []
    )

    if next_index >= len(all_moves):
        return

    # -----------------------------------------------------
    # SHARED PUZZLE:
    # Everyone can attempt the current move. The first
    # correct move advances the shared position.
    # -----------------------------------------------------

    user_id = str(
        message.author.id
    )

    submitted = move_text.strip()

    # One move at a time.
    if len(submitted.split()) != 1:
        await message.channel.send(
            f"❌ **One move at a time, "
            f"{message.author.display_name}.**"
        )
        return

    # Serialize state changes so two people cannot both
    # advance the same shared position at exactly the same time.
    # Capture this BEFORE advancing the shared state.
    move_was_first = (
        next_index == 0
    )

    async with data_lock:

        expected = all_moves[next_index]

        board = board_from_fen_safe(
            puzzle.get(
                "current_fen",
                puzzle["fen"]
            )
        )

        # The next solution move must belong to the player.
        if expected["color"] != player_color:
            await message.channel.send(
                "❌ **The puzzle state got out of sync. "
                "Please start a new random puzzle.**"
            )
            return

        correct = san_matches_move(
            board,
            submitted,
            expected
        )

        puzzle.setdefault(
            "attempted_users",
            {}
        )[user_id] = {
            "name": message.author.display_name,
            "move": submitted,
            "correct": correct,
            "timestamp": datetime.now(
                timezone.utc
            ).isoformat()
        }

        if not correct:
            # Do not hold the lock while sending to Discord.
            pass
        else:
            # -------------------------------------------------
            # PLAY THE USER'S CORRECT MOVE
            # -------------------------------------------------

            move = chess.Move.from_uci(
                expected["uci"]
            )

            if move not in board.legal_moves:
                correct = False
            else:
                board.push(move)

                next_index += 1
                next_player_index = (
                    puzzle.get(
                        "next_player_index",
                        0
                    ) + 1
                )

                opponent_replies = []

                # ---------------------------------------------
                # AUTOMATICALLY PLAY OPPONENT REPLIES
                # ---------------------------------------------

                while next_index < len(all_moves):
                    reply = all_moves[next_index]

                    if reply["color"] == player_color:
                        break

                    reply_move = chess.Move.from_uci(
                        reply["uci"]
                    )

                    if reply_move not in board.legal_moves:
                        break

                    board.push(reply_move)

                    opponent_replies.append(
                        reply["san"]
                    )

                    next_index += 1

                puzzle["current_fen"] = board.fen()
                puzzle["next_solution_index"] = next_index
                puzzle["next_player_index"] = next_player_index

    personal_result = await record_official_puzzle_result(
        puzzle,
        message.author,
        correct,
    )

    if personal_result and personal_result.get("recorded"):
        if personal_result.get("streak_bonus"):
            streak_value = int(
                personal_result.get("stats", {}).get("current_streak", 0)
            )
            await message.channel.send(
                f"🔥 **{streak_value}-puzzle streak!** "
                f"**{message.author.display_name} +1 bonus point.**"
            )

        unlock_text = achievement_unlock_text(personal_result)
        if unlock_text:
            await message.channel.send(unlock_text)

    if not correct:
        await save_all()
        await message.channel.send(
            wrong_message(message.author)
        )
        return

    # -----------------------------------------------------
    # PUZZLE COMPLETE
    #
    # IMPORTANT:
    # No points are awarded yet. We only record the first
    # move solver and helpers while the puzzle is in progress.
    # Points are awarded ONLY when the full puzzle is solved.
    # -----------------------------------------------------

    if move_was_first and puzzle.get(
        "first_move_user_id"
    ) is None:
        puzzle["first_move_user_id"] = str(
            message.author.id
        )
        puzzle["first_move_user_name"] = (
            message.author.display_name
        )

    # Record a helper candidate after a later correct move.
    # We only award +0.5 after the puzzle is completely solved.
    if (
        not move_was_first
        and str(message.author.id)
        != str(
            puzzle.get(
                "first_move_user_id"
            )
        )
    ):
        helpers = puzzle.setdefault(
            "helper_candidate_users",
            []
        )

        user_id = str(
            message.author.id
        )

        if user_id not in helpers:
            helpers.append(
                user_id
            )

    # -----------------------------------------------------
    # PUZZLE COMPLETE
    # -----------------------------------------------------

    if next_player_index >= len(player_moves):
        puzzle["solved"] = True

        # -----------------------------------------------------
        # NOW, AND ONLY NOW, AWARD POINTS
        # -----------------------------------------------------

        first_user_id = puzzle.get(
            "first_move_user_id"
        )

        helper_users = [
            uid
            for uid in puzzle.get(
                "helper_candidate_users",
                []
            )
            if str(uid) != str(first_user_id)
        ]

        # First mover: normal +1, Boss +2
        if first_user_id:
            first_user = None

            if str(first_user_id) == str(
                message.author.id
            ):
                first_user = message.author

            else:
                # The first mover may have zero points so far and
                # therefore may not exist in `scores` yet. Recover
                # their display name from the puzzle's recorded move
                # history instead of requiring a leaderboard entry.
                first_user_name = (
                    puzzle.get(
                        "first_move_user_name"
                    )
                    or puzzle.get(
                        "attempted_users",
                        {}
                    )
                    .get(
                        str(first_user_id),
                        {}
                    )
                    .get(
                        "name",
                        "Unknown"
                    )
                )

                class StoredUser:
                    def __init__(self, user_id, name):
                        self.id = int(user_id)
                        self.display_name = name

                first_user = StoredUser(
                    first_user_id,
                    first_user_name
                )

            if not puzzle.get(
                "first_move_awarded",
                False
            ):
                await award_random_move_points(
                    puzzle,
                    first_user,
                    first_move=True
                )

        # Helpers: normal +0.5, Boss +1 each, max once per puzzle.
        for helper_id in helper_users:
            if helper_id in puzzle.get(
                "helper_awarded_users",
                []
            ):
                continue

            if helper_id == first_user_id:
                continue

            helper_name = (
                puzzle.get(
                    "attempted_users",
                    {}
                )
                .get(
                    helper_id,
                    {}
                )
                .get(
                    "name",
                    "Unknown"
                )
            )

            class StoredHelper:
                def __init__(self, user_id, name):
                    self.id = int(user_id)
                    self.display_name = name

            helper_user = StoredHelper(
                helper_id,
                helper_name
            )

            result = await award_random_move_points(
                puzzle,
                helper_user,
                first_move=False
            )

            if result == "helper":
                puzzle.setdefault(
                    "helper_awarded_users",
                    []
                ).append(
                    helper_id
                )

        practice_only = str(
            puzzle.get(
                "puzzle_id",
                "",
            )
        ).startswith(
            "random_lichess_"
        )

        points = get_player_score(
            message.author.id
        )

        ranking = (
            ""
            if practice_only
            else get_personal_ranking(
                message.author.id
            )
        )

        embed_progress = (
            "🎉 **Puzzle solved!**"
        )

        if opponent_replies:
            embed_progress += (
                "\n"
                f"↩️ **Opponent:** "
                f"{' '.join(opponent_replies)}"
            )

        # The final board is now a NEW message too.
        final_file, final_board = await make_board_file(
            puzzle,
            "random_puzzle_final.png"
        )

        final_embed = discord.Embed(
            title=(
                f"{puzzle_label} — "
                f"{puzzle['title']}"
            ),
            description=embed_progress,
            color=0x3498db
        )

        final_embed.set_image(
            url="attachment://random_puzzle_final.png"
        )

        await message.channel.send(
            embed=final_embed,
            file=final_file
        )

        await save_all()

        first_reward = 2.0 if is_boss else 1.0
        helper_reward = 1.0 if is_boss else 0.5
        awarded_for_solver = 0.0

        if (
            not practice_only
            and str(
                message.author.id
            ) == str(first_user_id)
        ):
            awarded_for_solver = first_reward

        elif (
            not practice_only
            and str(
                message.author.id
            ) in helper_users
        ):
            awarded_for_solver = helper_reward

        if practice_only:
            score_message = (
                f"✅ **Correct, {message.author.display_name}!**\n"
                f"🎉 **Puzzle solved!**\n"
                f"Practice puzzle — **no shared leaderboard points**."
            )
        elif awarded_for_solver == first_reward and awarded_for_solver > 0:
            score_message = (
                f"✅ **Correct, {message.author.display_name}!**\n"
                f"🎉 **Puzzle solved!**\n"
                f"**+{format_points(first_reward)} point"
                f"{'s' if first_reward != 1 else ''}** — you now have "
                f"**{format_points(points)} points.**"
            )
        elif awarded_for_solver == helper_reward and awarded_for_solver > 0:
            score_message = (
                f"✅ **Correct, {message.author.display_name}!**\n"
                f"🎉 **Puzzle solved!**\n"
                f"**+{format_points(helper_reward)} point"
                f"{'s' if helper_reward != 1 else ''} for helping** — "
                f"you now have **{format_points(points)} points.**"
            )
        else:
            score_message = (
                f"✅ **Correct, {message.author.display_name}!**\n"
                f"🎉 **Puzzle solved!**\n"
                f"You have **{format_points(points)} points.**"
            )

        await message.channel.send(
            score_message
        )

        # If the finisher was not the first-move player, separately
        # notify the first-move player that their +1 was awarded.
        if (
            not practice_only
            and first_user_id
            and str(message.author.id)
            != str(first_user_id)
        ):
            first_name = puzzle.get(
                "first_move_user_name",
                "First solver"
            )

            first_reward = 2.0 if is_boss else 1.0
            await message.channel.send(
                f"🏆 **{first_name} found the first move!** "
                f"**+{format_points(first_reward)} point"
                f"{'s' if first_reward != 1 else ''}**."
            )

        if ranking:
            await message.channel.send(
                ranking
            )

        puzzle["answer_posted"] = True

        await post_answer(
            message.channel,
            puzzle,
            "daily" if is_daily else "random"
        )

        await save_all()

        return

    # -----------------------------------------------------
    # MORE PLAYER MOVES TO GO
    # -----------------------------------------------------

    remaining = (
        len(player_moves)
        - next_player_index
    )

    if opponent_replies:
        reply_text = (
            f"↩️ **Opponent replies:** "
            f"{' '.join(opponent_replies)}"
        )
    else:
        reply_text = ""

    if remaining == 1:
        progress = (
            "**✅ Correct! Now make your final move.**"
        )
    else:
        progress = (
            f"**✅ Correct! {remaining} "
            f"{move_word(remaining)} remaining.**"
        )

    if reply_text:
        progress += (
            f"\n{reply_text}"
        )

    # -----------------------------------------------------
    # NEW MESSAGE instead of editing the previous one.
    # This keeps every solved step visible in chat.
    # -----------------------------------------------------

    step_file, step_board = await make_board_file(
        puzzle,
        "random_puzzle_step.png"
    )

    step_embed = discord.Embed(
        title=(
            f"{puzzle_label} — "
            f"{puzzle['title']}"
        ),
        description=progress,
        color=0x3498db
    )

    step_embed.set_image(
        url="attachment://random_puzzle_step.png"
    )

    await message.channel.send(
        embed=step_embed,
        file=step_file
    )

    await save_all()


# =========================================================
# HANDLE ANSWER
# =========================================================

    if is_survival_active():
        return

async def handle_answer(
    message,
    puzzle,
    answer_window,
    move_text
):
    survival_active, _survival_team = remote_survival_status()

    if survival_active:
        return

    if not puzzle:
        return

    # Random puzzles are solved interactively:
    # one user move -> automatic opponent reply -> next user move.
    if str(
        puzzle.get("puzzle_id", "")
    ).startswith((
        "random_",
        "daily_",
    )):
        await handle_random_answer(
            message,
            puzzle,
            move_text
        )
        return

    # Legacy/non-interactive fallback.
    if puzzle.get(
        "answer_posted",
        False
    ):
        return

    if not puzzle_is_open(
        puzzle,
        answer_window
    ):
        return

    required = puzzle.get(
        "player_move_count",
        1
    )

    submitted_moves = (
        move_text.strip().split()
    )

    if len(submitted_moves) != required:
        await message.channel.send(
            f"❌ **Not quite, "
            f"{message.author.display_name}.**\n"
            f"This puzzle requires **{required} "
            f"{move_word(required)}** from your side."
        )
        return

    correct = solution_is_correct(
        move_text,
        puzzle
    )

    await save_attempt(
        puzzle,
        message.author,
        move_text,
        correct
    )

    if not correct:
        await message.channel.send(
            wrong_message(message.author)
        )
        return

    got_point = await award_point(
        puzzle,
        message.author
    )

    current_points = get_player_score(
        message.author.id
    )

    personal_ranking = get_personal_ranking(
        message.author.id
    )

    if got_point:
        response = (
            f"✅ **Correct, "
            f"{message.author.display_name}!**\n"
            f"**+1 point** — you now have "
            f"**{current_points} points**."
        )
    else:
        response = (
            f"✅ **Correct, "
            f"{message.author.display_name}!**\n"
            f"Someone else got the point first.\n"
            f"You have **{current_points} points**."
        )

    await message.channel.send(response)

    if personal_ranking:
        await message.channel.send(personal_ranking)


# =========================================================
# MESSAGE HANDLER
# =========================================================

@client.event
async def on_message(
    message
):

    try:



        if message.author.bot:
            return

        if message.channel.id != CHANNEL_ID:
            return

        content = message.content.strip()

        command_lower = content.casefold()

        # IMPORTANT: claim Survival immediately from the human command itself.
        # Waiting for survival_runs.json caused a race: Daily/Random could say
        # "Wrong" while Survival correctly accepted the exact same move.
        if command_lower == "!survival" or command_lower.startswith("!survival "):
            # Give Survival time to create/load the run and persist its state.
            # Unlike the previous version this is NOT permanent.
            set_survival_guard(90)
            return

        # Survival owns this command. Daily only clears its temporary hand-off
        # guard; Survival itself performs the actual pause/save.
        if command_lower == "!stopsurvival":
            clear_survival_guard()
            return

        # Sharkmeister-only shared leaderboard correction:
        # !edit <name> <points>
        if command_lower.startswith("!edit "):
            sharkmeister_user_id = os.getenv(
                "SHARKMEISTER_USER_ID",
                SHARKMEISTER_DEFAULT_USER_ID,
            ).strip() or SHARKMEISTER_DEFAULT_USER_ID

            if (
                not sharkmeister_user_id
                or str(message.author.id)
                != sharkmeister_user_id
            ):
                await message.channel.send(
                    "❌ Only **Sharkmeister** can edit the shared leaderboard."
                )
                return

            parts = content.split()
            if len(parts) < 3:
                await message.channel.send(
                    "❌ Usage: `!edit <name> <points>`"
                )
                return

            points_text = parts[-1]
            name = " ".join(
                parts[1:-1]
            ).strip()

            try:
                target_points = float(
                    points_text
                )

                if target_points < 0:
                    raise ValueError(
                        "negative"
                    )

                if target_points.is_integer():
                    target_points = int(
                        target_points
                    )

            except Exception:
                await message.channel.send(
                    "❌ Points must be a non-negative number, "
                    "for example `200` or `57.5`."
                )
                return

            transaction_id = (
                "admin-edit:"
                f"{message.id}:"
                f"{name.casefold()}:"
                f"{target_points}"
            )

            try:
                target_user_id = (
                    SHARKMEISTER_DEFAULT_USER_ID
                    if name.casefold().strip() == "sharkmeister"
                    else None
                )

                new_score = await asyncio.to_thread(
                    shared_admin_set_points,
                    name,
                    target_points,
                    transaction_id,
                    target_user_id=target_user_id,
                )

            except Exception as error:
                await message.channel.send(
                    f"❌ Could not edit the shared leaderboard: "
                    f"`{str(error)[:900]}`"
                )
                return

            await message.channel.send(
                f"✅ **{name}** is now on "
                f"**{format_points(new_score)} points** "
                "on the shared leaderboard."
            )
            return

        # Personal Puzzle profile. !stats <name> can inspect someone else;
        # !stats / !me / !profile are aliases for your own profile.
        if (
            command_lower in {"!stats", "!me", "!profile"}
            or command_lower.startswith("!stats ")
        ):
            if command_lower in {"!stats", "!me", "!profile"}:
                puzzle_profile = await asyncio.to_thread(
                    puzzle_stats_for_user,
                    message.author.id,
                    message.author.display_name,
                )
            else:
                requested_name = content[len("!stats"):].strip()

                if message.mentions:
                    target = message.mentions[0]
                    puzzle_profile = await asyncio.to_thread(
                        puzzle_stats_for_user,
                        target.id,
                        target.display_name,
                    )
                elif requested_name:
                    puzzle_profile = await asyncio.to_thread(
                        puzzle_stats_for_name,
                        requested_name,
                    )
                else:
                    puzzle_profile = None

                if puzzle_profile is None:
                    await message.channel.send(
                        f"❌ **No Puzzle stats found for `{requested_name}` yet.**"
                    )
                    return

            await message.channel.send(
                format_puzzle_stats(puzzle_profile)
            )
            return

        # Exact Lichess puzzle rating, e.g. !400 or !2552.
        if re.fullmatch(
            r"!\d+",
            command_lower,
        ):
            rating = int(
                command_lower[1:]
            )

            if not (
                100 <= rating <= 4000
            ):
                await message.channel.send(
                    "❌ Lichess puzzle rating must be between **100 and 4000**."
                )
                return

            if is_survival_active():
                team = active_team() or "another team"
                await message.channel.send(
                    f"⚠️ **Survival Mode is active for {team}.** "
                    "Lichess rating puzzles are unavailable until Survival is paused."
                )
                return

            await post_exact_lichess_puzzle(
                message.channel,
                rating,
            )
            return

        # Runtime build check. Safe for everyone; it changes no data.
        if command_lower in ("!v", "!version"):
            await message.channel.send(
                f"**Bot:** `{RP_BUILD}`\n"
                f"**Ledger:** `{SHARED_LEDGER_BUILD}`\n"
                f"**Puzzle Stats:** `{PUZZLE_STATS_BUILD}`"
            )
            return

        # Fast exact aliases. Handle these before any puzzle logic.
        if command_lower in (
            "!leaderboard",
            "!lb",
            "!l"
        ):
            await message.channel.send(
                make_leaderboard()
            )
            try:
                puzzle_board = await asyncio.to_thread(
                    format_puzzle_leaderboards,
                    10,
                )
                await message.channel.send(
                    puzzle_board
                )
            except Exception as error:
                print(
                    f"Puzzle leaderboard stats error: {error}",
                    flush=True,
                )
            return

        # =====================================================
        # HELP / INFO
        # =====================================================

        if command_lower in (
            "!help",
            "!info",
            "!i",
        ):

            await message.channel.send(
                help_message()
            )

            return

        # =====================================================
        # RANDOM PUZZLE
        # =====================================================

        if command_lower in (
            "!random",
            "!rp",
            "!r",
            "!randompuzzle",
            "rp",
        ):

            if survival_guard_active():
                await message.channel.send(
                    "⏳ **Survival is starting.** Try `rp` again in a moment "
                    "if no Survival run appears."
                )
                return

            if is_survival_active():
                team = active_team() or "another team"
                await message.channel.send(
                    f"⚠️ **Survival Mode is active for {team}.** "
                    "Random Puzzle is unavailable until Survival is paused."
                )
                return

            previous_random = state.get(
                "latest_random_puzzle"
            )

            if (
                previous_random
                and not previous_random.get(
                    "answer_posted",
                    False
                )
                and not previous_random.get(
                    "solved",
                    False
                )
            ):
                await finalize_expired_puzzle(
                    message.channel,
                    previous_random,
                    "random"
                )

            await post_random_puzzle(
                message.channel
            )

            return

        # Survival owns all chess-puzzle messages while it is active.
        # The local command guard is immediate; the remote state is the
        # persistent fallback across process restarts.
        if survival_guard_active():
            return

        survival_active, survival_team = remote_survival_status()

        if survival_active:
            return

        # =====================================================
        # RANDOM ANSWER
        # =====================================================

        if command_lower.startswith(
            "!random "
        ):

            move_text = content[
                len("!random "):
            ].strip()

            if not move_text:
                return

            puzzle = state.get(
                "latest_random_puzzle"
            )

            await handle_answer(
                message,
                puzzle,
                RANDOM_ANSWER_WINDOW,
                move_text
            )

            return

        # =====================================================
        # DAILY ANSWER
        # =====================================================

        if command_lower.startswith(
            "!daily "
        ):

            move_text = content[
                len("!daily "):
            ].strip()

            if not move_text:
                return

            puzzle = state.get(
                "current_puzzle"
            )

            await handle_answer(
                message,
                puzzle,
                ANSWER_WINDOW,
                move_text
            )

            return

        # =====================================================
        # QUICK ANSWER
        #
        # !Bf2
        # !Bf2
        # !bf2
        # =====================================================

        # Moves accept both forms:
        #   !Qh6
        #   Qh6
        candidate_move = (
            content[1:].strip()
            if content.startswith("!")
            else content.strip()
        )

        if not candidate_move:
            return

        # Do NOT treat normal chat as a chess move.
        #
        # A move candidate must:
        # - be short (<= 12 chars), and
        # - consist only of chess-move-looking tokens.
        #
        # This keeps messages such as:
        #   "what can the answer be?"
        # from triggering the puzzle.
        move_tokens = candidate_move.split()

        if len(candidate_move) > 12:
            return

        chess_move_pattern = re.compile(
            r"^(?:"
            r"[KQRBN]?[a-h]?[1-8]?x?[a-h][1-8][+#]?"
            r"|[a-h](?:x[a-h])?[18]=[QRBN][+#]?"
            r"|O-O-O[+#]?"
            r"|O-O[+#]?"
            r"|0-0-0[+#]?"
            r"|0-0[+#]?"
            r")$",
            re.IGNORECASE,
        )

        if not move_tokens or not all(
            chess_move_pattern.fullmatch(
                token
            )
            for token in move_tokens
        ):
            return

        # Plain chess-like text is now treated as a move.
        latest_type = state.get(
            "latest_puzzle_type"
        )

        if latest_type == "random":

            puzzle = state.get(
                "latest_random_puzzle"
            )

            answer_window = (
                RANDOM_ANSWER_WINDOW
            )

        elif latest_type == "daily":

            puzzle = state.get(
                "current_puzzle"
            )

            answer_window = (
                ANSWER_WINDOW
            )

        else:

            return

        await handle_answer(
            message,
            puzzle,
            answer_window,
            candidate_move
        )

    except Exception as error:
        print(
            f"COMMAND ERROR: {error}",
            flush=True
        )
        traceback.print_exc()

        try:
            await message.channel.send(
                f"❌ **Bot error:** `{str(error)[:1000]}`"
            )
        except Exception:
            pass


# =========================================================
# READY
# =========================================================

@client.event
async def on_ready():

    if getattr(
        client,
        "started",
        False
    ):
        return

    client.started = True

    global state

    state = load_json(
        STATE_FILE,
        {}
    )

    state.setdefault(
        "leaderboard_last_posted_date",
        None
    )

    # Restore the current random puzzle position after a restart.
    random_puzzle = state.get(
        "latest_random_puzzle"
    )

    if random_puzzle:
        random_puzzle.setdefault(
            "current_fen",
            random_puzzle.get("fen")
        )
        random_puzzle.setdefault(
            "next_solution_index",
            0
        )
        random_puzzle.setdefault(
            "next_player_index",
            0
        )
        random_puzzle.setdefault(
            "solved",
            False
        )
        random_puzzle.setdefault(
            "attempted_users",
            {}
        )

        random_puzzle.setdefault(
            "first_move_user_id",
            None
        )

        random_puzzle.setdefault(
            "first_move_user_name",
            None
        )

        random_puzzle.setdefault(
            "first_move_awarded",
            False
        )

        random_puzzle.setdefault(
            "helper_awarded_users",
            []
        )

        random_puzzle.setdefault(
            "helper_candidate_users",
            []
        )

    print(
        f"READY! Logged in as {client.user}",
        flush=True
    )

    try:

        channel = await client.fetch_channel(
            CHANNEL_ID
        )

    except Exception as error:

        print(
            f"Could not find channel: {error}",
            flush=True
        )

        return

    print(
        f"Channel found: {channel.name}",
        flush=True
    )

    try:
        command_tree.copy_global_to(
            guild=channel.guild
        )
        synced_commands = await command_tree.sync(
            guild=channel.guild
        )
        print(
            f"Guild commands synced: {len(synced_commands)}",
            flush=True,
        )
    except Exception as error:
        # Never stop the puzzle bot just because Discord command sync failed.
        print(
            f"Could not sync private status command: {error}",
            flush=True,
        )

    await check_for_new_puzzle(
        channel
    )

    asyncio.create_task(
        puzzle_loop(channel)
    )

    asyncio.create_task(
        maintenance_loop(channel)
    )

    # Do not intentionally close the Discord client after 5h50.
    # discord.py reconnects automatically after transient Discord/network
    # disconnects. The hosting workflow controls the process lifetime.

    print(
        "Daily Puzzle Bot is running and listening continuously.",
        flush=True
    )


@client.event
async def on_disconnect():
    print(
        "Discord connection lost; discord.py will reconnect automatically.",
        flush=True,
    )


@client.event
async def on_resumed():
    print(
        "Discord connection resumed.",
        flush=True,
    )


# =========================================================
# START
# =========================================================

print(
    "Starting Daily Chess Puzzle Bot...",
    flush=True
)
print(f"Daily Puzzle build: {RP_BUILD}", flush=True)
print(f"Shared leaderboard build: {SHARED_LEDGER_BUILD}", flush=True)

client.run(TOKEN)
