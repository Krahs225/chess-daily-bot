import discord

from shared_leaderboard import (
    admin_set_points as shared_admin_set_points,
    admin_set_coins as shared_admin_set_coins,
    admin_set_color as shared_admin_set_color,
    resolve_cosmetic_profile as shared_resolve_cosmetic_profile,
    add_points as shared_add_points,
    adjust_points as shared_adjust_points,
    get_score as shared_get_score,
    get_coins as shared_get_coins,
    get_cosmetic_profile,
    badge_prefix,
    badge_map as shared_badge_map,
    buy_badge_box,
    equip_badge,
    buy_board,
    equip_board,
    buy_piece,
    equip_piece,
    buy_color,
    equip_color,
    transfer_coins,
    transfer_badge,
    reserve_chess_wager as shared_reserve_chess_wager,
    settle_chess_wager as shared_settle_chess_wager,
    resolve_badge as shared_resolve_badge,
    propose_trade as shared_propose_trade,
    accept_trade as shared_accept_trade,
    decline_trade as shared_decline_trade,
    format_trade_asset as shared_format_trade_asset,
    personal_ranking as shared_personal_ranking,
    full_leaderboard as shared_full_leaderboard,
    format_points as shared_format_points,
    LEDGER_BUILD as SHARED_LEDGER_BUILD,
    REPOSITORY_LOCK,
)
from shop_catalog import (
    BADGE_BOX_COST, BADGE_POOLS, RARITY_LABELS, BOARD_COST, BOARD_THEMES, BOARD_DISPLAY_NAMES,
    PIECE_COST, PIECE_SETS, PIECE_DISPLAY_NAMES, COLOR_COST, NAME_COLORS, SHOP_COLOR_ROLE_PREFIX, SURVIVAL_HEART_COST,
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
import math
from collections import Counter
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

from chess_play import (
    CHESS_PLAY_BUILD,
    CHESS_START_ELO,
    BOT_MIN_ELO,
    BOT_MAX_ELO,
    normalize_rating_entry as normalize_chess_rating_entry,
    rating_entry as chess_rating_entry,
    apply_single_result as apply_chess_single_result,
    apply_head_to_head_result as apply_chess_head_to_head_result,
    random_bot_rating,
    clamp_bot_rating,
    elo_after as chess_elo_after,
    choose_bot_move,
    stockfish_engine_info,
    StockfishUnavailableError,
    move_like_text as chess_game_move_like,
    parse_move as parse_chess_game_move,
)
from chess_reactions import (
    CHESS_REACTIONS_BUILD,
    bot_result_reaction,
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
_survival_stop_requested_at = 0.0


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


def note_survival_stop_requested():
    global _survival_stop_requested_at
    _survival_stop_requested_at = time.monotonic()
    _survival_check_cache["time"] = 0.0


async def settle_recent_survival_stop():
    # !stopsurvival is handled by the separate Survival process. For a few
    # seconds afterwards, actively re-read the persisted source of truth so an
    # immediate RP/Practice command does not see the old active commit.
    global _survival_stop_requested_at
    age = time.monotonic() - _survival_stop_requested_at
    if not (0 <= age < 8.0):
        return

    await asyncio.sleep(max(0.0, 0.35 - age))
    for _ in range(4):
        _survival_check_cache["time"] = 0.0
        active, _team = await asyncio.to_thread(remote_survival_status)
        if not active:
            _survival_stop_requested_at = 0.0
            return
        await asyncio.sleep(0.35)

    # Keep fail-closed behavior if Survival could not be verified inactive.
    _survival_check_cache["time"] = 0.0


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

CHESS_CHALLENGE_SECONDS = 10 * 60
PUZZLE_RUSH_SECONDS = 5 * 60
PUZZLE_RUSH_WINDOW = 100


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

A run that died at **3/3 strikes cannot be continued or revived**.

**Hearts / strikes**
Everyone starts with **❤️❤️❤️**.

A wrong answer costs **1 strike**.

At **3/3 strikes**, the run is **DEAD** and cannot be revived.
The active run's captain may use `!heart` once per run after losing a heart; it costs **100 personal coins** and restores exactly one heart.

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

**Personal rewards**
- First solver on a completed Survival puzzle: **+1 coin**.
- Each unique later helper: **+0.5 coin**.
- Your first real attempt on each Survival puzzle updates your personal **Puzzle Elo/stats/streak**.

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
github_sync_dirty = False
github_sync_last_ok = True
score_file_lock = threading.Lock()

# Offline RP runtime state. Every six RP requests consume every rating band
# exactly once, in a freshly shuffled order.
rp_command_lock = asyncio.Lock()
chess_game_lock = asyncio.Lock()
rush_lock = asyncio.Lock()
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

            # `nothing to commit` is fine, but do NOT return here. A previous
            # attempt may already have created a local state commit whose push
            # failed. We must still run pull/rebase + push below.
            if commit.returncode != 0:
                status = subprocess.run(
                    ["git", "status", "--porcelain", "--", STATE_FILE],
                    capture_output=True,
                    text=True,
                )
                if status.returncode != 0 or status.stdout.strip():
                    raise RuntimeError(commit.stderr.strip() or commit.stdout.strip() or "git commit failed")

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
                    return True

                time.sleep(0.25 * attempt)

            raise RuntimeError("Could not push Daily puzzle state after retries.")

        except Exception as error:
            print(
                f"Could not push Daily puzzle state to GitHub: {error}",
                flush=True,
            )
            return False


async def _github_sync_worker():
    global github_sync_dirty, github_sync_last_ok

    # Important: state can change while a Git push is already running.
    # Keep looping until no save happened during the previous push. This fixes
    # Chess Elo (and any other Daily state) being visible in memory but missing
    # again after a runner restart.
    while True:
        github_sync_dirty = False
        github_sync_last_ok = bool(await asyncio.to_thread(push_to_github))
        if github_sync_dirty:
            continue
        return github_sync_last_ok


def queue_github_sync():

    global github_sync_task, github_sync_dirty

    github_sync_dirty = True
    if (
        github_sync_task is not None
        and not github_sync_task.done()
    ):
        return github_sync_task

    github_sync_task = asyncio.create_task(_github_sync_worker())
    return github_sync_task


async def save_all(wait_for_remote=False):

    save_json(
        STATE_FILE,
        state
    )

    task = queue_github_sync()
    if wait_for_remote and task is not None:
        try:
            return bool(await asyncio.shield(task))
        except Exception as error:
            print(f"Could not await Daily state sync: {error}", flush=True)
            return False
    return True


async def save_all_critical(attempts=3):
    """Persist important state (especially rated Chess Elo) remotely."""
    for attempt in range(1, max(1, int(attempts)) + 1):
        if await save_all(wait_for_remote=True):
            return True
        if attempt < attempts:
            await asyncio.sleep(0.75 * attempt)
    return False



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



def fetch_practice_puzzle(target_rating, window=100):
    """Pick an offline Lichess puzzle close to the user's current Puzzle Elo."""
    global _rp_recent_ids

    if not os.path.exists(RP_POOL_FILE):
        raise RuntimeError(
            f"Offline RP pool '{RP_POOL_FILE}' is missing. "
            "Run the Build RP Puzzle Pool workflow first."
        )

    target = max(RP_BANDS[0][0], min(int(round(target_rating)), RP_BANDS[-1][1]))
    minimum = max(RP_BANDS[0][0], target - int(window))
    maximum = min(RP_BANDS[-1][1], target + int(window))

    with _rp_pool_lock:
        con = sqlite3.connect(
            f"file:{RP_POOL_FILE}?mode=ro",
            uri=True,
            timeout=5,
        )
        try:
            # Randomize only inside a narrow rating window. If the exact window
            # is unexpectedly empty, fall back to the closest puzzle in the pool.
            rows = con.execute(
                """
                SELECT puzzle_id, fen, moves, rating, band
                FROM puzzles
                WHERE rating BETWEEN ? AND ?
                ORDER BY RANDOM()
                LIMIT 80
                """,
                (minimum, maximum),
            ).fetchall()

            recent = set(_rp_recent_ids[-1000:])
            available = [row for row in rows if str(row[0]) not in recent]
            row = random.choice(available or rows) if rows else None

            if row is None:
                row = con.execute(
                    """
                    SELECT puzzle_id, fen, moves, rating, band
                    FROM puzzles
                    ORDER BY ABS(rating - ?), RANDOM()
                    LIMIT 1
                    """,
                    (target,),
                ).fetchone()
        finally:
            con.close()

        if not row:
            raise RuntimeError("Could not select an offline Practice puzzle.")

        puzzle_id, raw_fen, moves_text, rating, stored_band = row
        rating = int(rating)
        moves = str(moves_text).split()
        if len(moves) < 2:
            raise RuntimeError("Offline Practice puzzle has no solution line.")

        board = board_from_fen_safe(str(raw_fen))
        first_move = chess.Move.from_uci(moves[0])
        if first_move not in board.legal_moves:
            raise RuntimeError("Offline Practice puzzle has an illegal setup move.")
        board.push(first_move)

        puzzle_fen = board.fen()
        solution_san = []
        for uci in moves[1:]:
            move = chess.Move.from_uci(str(uci))
            if move not in board.legal_moves:
                raise RuntimeError("Offline Practice puzzle has an illegal solution move.")
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
            "rp_band": int(stored_band),
            "practice_target": target,
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
    owner=None,
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
        puzzle["practice_only"] = True
        puzzle["rated_practice"] = False
        if owner is not None:
            try:
                cosmetic = await asyncio.to_thread(
                    get_cosmetic_profile, owner.id, owner.display_name
                )
                puzzle["board_theme"] = cosmetic.get("active_board", "classic")
                puzzle["piece_theme"] = cosmetic.get("active_piece", "classic")
            except Exception:
                puzzle["board_theme"] = "classic"
                puzzle["piece_theme"] = "classic"
        else:
            puzzle["board_theme"] = "classic"
            puzzle["piece_theme"] = "classic"

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

_UNICODE_CHESS_GLYPHS = {
    "K": "♔", "Q": "♕", "R": "♖", "B": "♗", "N": "♘", "P": "♙",
    "k": "♚", "q": "♛", "r": "♜", "b": "♝", "n": "♞", "p": "♟",
}


def _piece_overlay_svg(board, orientation, piece_theme):
    style = PIECE_SETS.get(piece_theme, PIECE_SETS["classic"])
    shape = style.get("shape", "classic")
    if shape == "classic":
        return ""

    square_size = 45.0
    board_offset = 15.0  # python-chess coordinate margin when coordinates=True.
    white_fill = style.get("white_fill", "#f7f7f2")
    black_fill = style.get("black_fill", "#111111")
    white_stroke = style.get("white_stroke", "#111111")
    black_stroke = style.get("black_stroke", "#f7f7f2")
    letters = {1: "P", 2: "N", 3: "B", 4: "R", 5: "Q", 6: "K"}
    parts = ['<g class="custom-piece-set">']

    for square, piece in board.piece_map().items():
        file_index = chess.square_file(square)
        rank_index = chess.square_rank(square)
        x = (file_index if orientation else 7 - file_index) * square_size + board_offset
        y = (7 - rank_index if orientation else rank_index) * square_size + board_offset
        cx = x + square_size / 2
        cy = y + square_size / 2
        fill = white_fill if piece.color else black_fill
        stroke = white_stroke if piece.color else black_stroke
        symbol = piece.symbol()
        letter = letters[piece.piece_type]

        if shape == "figurine":
            glyph = _UNICODE_CHESS_GLYPHS[symbol]
            parts.append(
                f'<text x="{cx:.2f}" y="{cy + 1:.2f}" text-anchor="middle" '
                f'dominant-baseline="central" font-family="DejaVu Sans, serif" '
                f'font-size="38" font-weight="700" fill="{fill}" stroke="{stroke}" '
                f'stroke-width="0.7" paint-order="stroke">{glyph}</text>'
            )
        elif shape in {"monogram", "minimal"}:
            size = 29 if shape == "monogram" else 25
            weight = 800 if shape == "monogram" else 600
            parts.append(
                f'<text x="{cx:.2f}" y="{cy + 1:.2f}" text-anchor="middle" '
                f'dominant-baseline="central" font-family="DejaVu Sans, sans-serif" '
                f'font-size="{size}" font-weight="{weight}" fill="{fill}" stroke="{stroke}" '
                f'stroke-width="0.8" paint-order="stroke">{letter}</text>'
            )
        else:
            if shape == "token":
                parts.append(
                    f'<circle cx="{cx:.2f}" cy="{cy:.2f}" r="17" fill="{fill}" '
                    f'stroke="{stroke}" stroke-width="2" />'
                )
            elif shape == "diamond":
                pts = f'{cx:.2f},{cy-19:.2f} {cx+18:.2f},{cy:.2f} {cx:.2f},{cy+19:.2f} {cx-18:.2f},{cy:.2f}'
                parts.append(f'<polygon points="{pts}" fill="{fill}" stroke="{stroke}" stroke-width="2" />')
            else:  # shield
                pts = f'{cx-16:.2f},{cy-17:.2f} {cx+16:.2f},{cy-17:.2f} {cx+18:.2f},{cy+5:.2f} {cx:.2f},{cy+19:.2f} {cx-18:.2f},{cy+5:.2f}'
                parts.append(f'<polygon points="{pts}" fill="{fill}" stroke="{stroke}" stroke-width="2" />')
            text_fill = "#111111" if piece.color else "#ffffff"
            parts.append(
                f'<text x="{cx:.2f}" y="{cy + 1:.2f}" text-anchor="middle" '
                f'dominant-baseline="central" font-family="DejaVu Sans, sans-serif" '
                f'font-size="22" font-weight="800" fill="{text_fill}">{letter}</text>'
            )

    parts.append('</g>')
    return "".join(parts)


def render_custom_board_svg(board, *, orientation, board_theme="classic", piece_theme="classic", size=500):
    board_theme = str(board_theme or "classic").casefold()
    piece_theme = str(piece_theme or "classic").casefold()
    light, dark = BOARD_THEMES.get(board_theme, BOARD_THEMES["classic"])

    if piece_theme == "classic" or piece_theme not in PIECE_SETS:
        return chess.svg.board(
            board=board,
            orientation=orientation,
            size=size,
            coordinates=True,
            colors={"square light": light, "square dark": dark},
        )

    svg = chess.svg.board(
        board=None,
        orientation=orientation,
        size=size,
        coordinates=True,
        colors={"square light": light, "square dark": dark},
    )
    overlay = _piece_overlay_svg(board, orientation, piece_theme)
    return svg.replace("</svg>", overlay + "</svg>")


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
    if str(puzzle.get("puzzle_id", "")).startswith(("random_", "practice_")):
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

    theme_name = str(puzzle.get("board_theme", "classic") or "classic").casefold()
    piece_theme = str(puzzle.get("piece_theme", "classic") or "classic").casefold()
    light, dark = BOARD_THEMES.get(theme_name, BOARD_THEMES["classic"])

    svg_board = render_custom_board_svg(
        board,
        orientation=orientation,
        board_theme=theme_name,
        piece_theme=piece_theme,
        size=500,
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
# NORMAL CHESS ELO / PLAY BOT / PLAYER VS PLAYER
# =========================================================

def _chess_ratings_state():
    return state.setdefault("chess_ratings", {})


def _chess_games_state():
    return state.setdefault("chess_games", {})


def _chess_challenges_state():
    return state.setdefault("chess_challenges", {})


def _rush_state():
    return state.setdefault("puzzle_rush", {})


def _rush_bests_state():
    return state.setdefault("puzzle_rush_bests", {})


def chess_rating_profile(user_id, display_name="Unknown"):
    raw = _chess_ratings_state().get(str(user_id))
    return normalize_chess_rating_entry(raw, display_name)


def _signed_elo(value):
    rounded = int(round(float(value)))
    return f"+{rounded}" if rounded > 0 else str(rounded)


def format_chess_profile_line(user_id, display_name="Unknown"):
    entry = chess_rating_profile(user_id, display_name)
    suffix = "" if int(entry.get("games", 0)) else " *(unrated until first game)*"
    return (
        f"♜ **Chess Elo:** {int(round(float(entry['elo'])))}{suffix}\n"
        f"🎮 **Chess games:** {entry['games']} — "
        f"{entry['wins']}W / {entry['draws']}D / {entry['losses']}L\n"
        f"📈 **Peak Chess Elo:** {int(round(float(entry['peak_elo'])))}"
    )


def format_chess_elo_leaderboard(limit=10):
    rows = []
    for user_id, raw in _chess_ratings_state().items():
        entry = normalize_chess_rating_entry(raw, raw.get("name", "Unknown") if isinstance(raw, dict) else "Unknown")
        if int(entry.get("games", 0)) <= 0:
            continue
        rows.append((str(user_id), entry))

    rows.sort(
        key=lambda item: (
            -float(item[1].get("elo", CHESS_START_ELO)),
            -int(item[1].get("games", 0)),
            str(item[1].get("name", "Unknown")).casefold(),
        )
    )
    rows = rows[:max(1, int(limit))]

    lines = ["♜ **Top Chess Elo**"]
    if not rows:
        lines.append("No rated Chess Elo results yet.")
        return "\n".join(lines)

    try:
        badges = shared_badge_map([user_id for user_id, _entry in rows])
    except Exception:
        badges = {}

    for rank, (user_id, entry) in enumerate(rows, 1):
        badge = badges.get(str(user_id), "")
        prefix = f"{badge} " if badge else ""
        lines.append(
            f"**{rank}.** {prefix}{entry.get('name', 'Unknown')} — "
            f"**{int(round(float(entry.get('elo', CHESS_START_ELO))))} Elo**"
        )
    return "\n".join(lines)


def split_puzzle_leaderboards(limit=10):
    combined = format_puzzle_leaderboards(limit)
    marker = "🔥 **Best Puzzle Streaks**"
    before, found, after = combined.partition(marker)
    puzzle_elo = before.rstrip()
    if found:
        streaks = marker + after
    else:
        streaks = "🔥 **Best Puzzle Streaks**\nNo puzzle streaks yet."
    return puzzle_elo, streaks


def recover_chess_ratings_from_game_history():
    """Recover missing Chess Elo entries from retained finished game snapshots.

    Finished games already store the post-game rating. If `chess_ratings` was
    ever lost/stale while `chess_games` survived, use those snapshots so a
    player does not silently fall back to the 1500 default after a restart.
    Existing rated entries are never overwritten.
    """
    ratings = _chess_ratings_state()
    games = sorted(
        (
            game for game in _chess_games_state().values()
            if isinstance(game, dict) and game.get("status") == "finished"
        ),
        key=lambda game: float(game.get("finished_at", game.get("started_at", 0)) or 0),
    )
    recovered = {}

    def touch(user_id, name, before, after, score):
        uid = str(user_id or "")
        if not uid or uid == "BOT" or after is None:
            return
        item = recovered.setdefault(uid, {
            "name": str(name or "Unknown"),
            "elo": CHESS_START_ELO,
            "peak_elo": CHESS_START_ELO,
            "games": 0,
            "wins": 0,
            "draws": 0,
            "losses": 0,
        })
        if name:
            item["name"] = str(name)
        try:
            before_value = float(before) if before is not None else float(item["elo"])
        except Exception:
            before_value = float(item["elo"])
        try:
            after_value = float(after)
        except Exception:
            return
        item["elo"] = after_value
        item["peak_elo"] = max(float(item.get("peak_elo", CHESS_START_ELO)), before_value, after_value)
        item["games"] += 1
        if score > 0.75:
            item["wins"] += 1
        elif score < 0.25:
            item["losses"] += 1
        else:
            item["draws"] += 1

    for game in games:
        result = str(game.get("result") or "")
        if result not in {"1-0", "0-1", "1/2-1/2"}:
            continue
        white_score = 1.0 if result == "1-0" else 0.0 if result == "0-1" else 0.5
        if game.get("mode") == "bot":
            human_id = str(game.get("human_id") or "")
            human_white = str(game.get("white_id")) == human_id
            human_score = white_score if human_white else 1.0 - white_score
            before = game.get("white_rating") if human_white else game.get("black_rating")
            touch(
                human_id,
                game.get("human_name", "Player"),
                before,
                game.get("human_rating_after"),
                human_score,
            )
        else:
            touch(
                game.get("white_id"),
                game.get("white_name", "White"),
                game.get("white_rating"),
                game.get("white_rating_after"),
                white_score,
            )
            touch(
                game.get("black_id"),
                game.get("black_name", "Black"),
                game.get("black_rating"),
                game.get("black_rating_after"),
                1.0 - white_score,
            )

    changed = False
    for uid, recovered_entry in recovered.items():
        current = normalize_chess_rating_entry(ratings.get(uid), recovered_entry["name"])
        if uid not in ratings or int(current.get("games", 0)) <= 0:
            ratings[uid] = normalize_chess_rating_entry(recovered_entry, recovered_entry["name"])
            changed = True
    return changed


def _active_chess_game_for_user(user_id):
    uid = str(user_id)
    for game in _chess_games_state().values():
        if game.get("status") != "active":
            continue
        if uid in {str(game.get("white_id")), str(game.get("black_id"))}:
            return game
    return None


def _active_rush_for_user(user_id):
    session = _rush_state().get(str(user_id))
    if not isinstance(session, dict) or not session.get("active"):
        return None
    return session


def _prune_chess_game_history(limit=200):
    games = _chess_games_state()
    if len(games) <= limit:
        return
    finished = sorted(
        (
            (float(game.get("finished_at", game.get("started_at", 0)) or 0), game_id)
            for game_id, game in games.items()
            if game.get("status") != "active"
        ),
        key=lambda item: item[0],
    )
    while len(games) > limit and finished:
        _when, game_id = finished.pop(0)
        games.pop(game_id, None)


def _game_side_for_user(game, user_id):
    uid = str(user_id)
    if str(game.get("white_id")) == uid:
        return chess.WHITE
    if str(game.get("black_id")) == uid:
        return chess.BLACK
    return None


def _turn_display(game, board):
    return game.get("white_name", "White") if board.turn == chess.WHITE else game.get("black_name", "Black")


def _normalize_person_name(value):
    return "".join(ch for ch in str(value or "").casefold() if ch.isalnum())


async def resolve_server_member(message, typed_name):
    if message.mentions:
        return message.mentions[0]

    query = str(typed_name or "").strip()
    if not query:
        return None

    # Existing wallet/leaderboard names are the most reliable route because
    # the bot does not need Discord's privileged member-list intent for them.
    try:
        profile = await asyncio.to_thread(
            shared_resolve_cosmetic_profile,
            query,
        )
        member = message.guild.get_member(int(profile["user_id"]))
        if member is None:
            try:
                member = await message.guild.fetch_member(int(profile["user_id"]))
            except Exception:
                member = None
        if member is not None:
            return member
    except Exception:
        pass

    query_key = _normalize_person_name(query)
    candidates = []
    for member in getattr(message.guild, "members", []):
        keys = {
            _normalize_person_name(getattr(member, "display_name", "")),
            _normalize_person_name(getattr(member, "name", "")),
            _normalize_person_name(getattr(member, "global_name", "")),
        }
        if query_key and query_key in keys:
            return member
        if query_key and any(key.startswith(query_key) for key in keys if key):
            candidates.append(member)
    return candidates[0] if len(candidates) == 1 else None


def _shop_asset_from_text(text, owned_badges):
    raw = str(text or "").strip()
    try:
        amount = round(float(raw), 3)
        if amount > 0 and " " not in raw:
            return {"type": "coins", "amount": amount}
    except Exception:
        pass
    badge = shared_resolve_badge(raw, owned_badges)
    return {"type": "badge", "badge": badge}


async def resolve_chess_challenge_target_and_wager(message, raw_text):
    raw_text = str(raw_text or "").strip()
    if not raw_text:
        return None, 0.0

    if message.mentions:
        target = message.mentions[0]
        mention_forms = (f"<@{target.id}>", f"<@!{target.id}>")
        remaining = raw_text
        for form in mention_forms:
            remaining = remaining.replace(form, " ")
        remaining = " ".join(remaining.split())
        if not remaining:
            return target, 0.0
        try:
            wager = round(float(remaining), 3)
        except Exception:
            raise ValueError("After the player mention, add only the coin wager, for example `!play @Thice 10`.")
        if wager < 0:
            raise ValueError("Chess wager must be 0 coins or more.")
        return target, wager

    # First try the entire text as a player name. This keeps names ending in a
    # number working as free challenges whenever they resolve exactly.
    whole_target = await resolve_server_member(message, raw_text)
    if whole_target is not None:
        return whole_target, 0.0

    parts = raw_text.split()
    if len(parts) < 2:
        return None, 0.0

    try:
        wager = round(float(parts[-1]), 3)
    except Exception:
        return None, 0.0
    if wager < 0:
        raise ValueError("Chess wager must be 0 coins or more.")

    target_text = " ".join(parts[:-1]).strip()
    target = await resolve_server_member(message, target_text)
    return target, wager


async def _shop_target_identity(message, typed_name):
    member = await resolve_server_member(message, typed_name)
    if member is not None:
        return str(member.id), member.display_name
    target = await asyncio.to_thread(shared_resolve_cosmetic_profile, typed_name)
    return str(target["user_id"]), target.get("name", typed_name)


async def _parse_donation_args(message, arg_text):
    words = str(arg_text or "").split()
    if len(words) < 2:
        raise ValueError("Usage: `!donate <name> <coins|badge>`")
    sender_profile = await asyncio.to_thread(
        get_cosmetic_profile, message.author.id, message.author.display_name
    )
    candidates = []
    if message.mentions:
        target = message.mentions[0]
        mention_forms = {f"<@{target.id}>", f"<@!{target.id}>"}
        remaining = [word for word in words if word not in mention_forms]
        if not remaining:
            raise ValueError("Add coins or a badge after the player name.")
        asset = _shop_asset_from_text(" ".join(remaining), sender_profile.get("badges", []))
        return str(target.id), target.display_name, asset

    for split in range(1, len(words)):
        typed_name = " ".join(words[:split])
        item_text = " ".join(words[split:])
        try:
            target_id, target_name = await _shop_target_identity(message, typed_name)
            asset = _shop_asset_from_text(item_text, sender_profile.get("badges", []))
        except Exception:
            continue
        key = (str(target_id), asset["type"], str(asset.get("amount", asset.get("badge", ""))))
        if key not in {item[0] for item in candidates}:
            candidates.append((key, target_id, target_name, asset))
    if not candidates:
        raise ValueError("Could not match that player + coins/badge. Use the exact badge emoji/name if needed.")
    if len(candidates) > 1:
        raise ValueError("That donation is ambiguous. Mention the player or use the exact badge emoji.")
    _, target_id, target_name, asset = candidates[0]
    return target_id, target_name, asset


async def _parse_trade_args(message, arg_text):
    words = str(arg_text or "").split()
    if len(words) < 3:
        raise ValueError("Usage: `!trade <name> <give coins/badge> <receive coins/badge>`")
    sender_profile = await asyncio.to_thread(
        get_cosmetic_profile, message.author.id, message.author.display_name
    )
    target_candidates = []
    if message.mentions:
        target = message.mentions[0]
        mention_forms = {f"<@{target.id}>", f"<@!{target.id}>"}
        remaining = [word for word in words if word not in mention_forms]
        target_candidates.append((str(target.id), target.display_name, remaining))
    else:
        for target_split in range(1, len(words) - 1):
            typed_name = " ".join(words[:target_split])
            try:
                target_id, target_name = await _shop_target_identity(message, typed_name)
            except Exception:
                continue
            target_candidates.append((target_id, target_name, words[target_split:]))

    parsed = []
    for target_id, target_name, remaining in target_candidates:
        if str(target_id) == str(message.author.id) or len(remaining) < 2:
            continue
        target_profile = await asyncio.to_thread(get_cosmetic_profile, target_id, target_name)
        for split in range(1, len(remaining)):
            try:
                offer = _shop_asset_from_text(" ".join(remaining[:split]), sender_profile.get("badges", []))
                request = _shop_asset_from_text(" ".join(remaining[split:]), target_profile.get("badges", []))
            except Exception:
                continue
            key = (
                str(target_id),
                offer["type"], str(offer.get("amount", offer.get("badge", ""))),
                request["type"], str(request.get("amount", request.get("badge", ""))),
            )
            if key not in {item[0] for item in parsed}:
                parsed.append((key, target_id, target_name, offer, request))
    if not parsed:
        raise ValueError("Could not understand that trade. Only coins and badges can be traded.")
    if len(parsed) > 1:
        raise ValueError("That trade is ambiguous. Mention the player and/or use exact badge emojis.")
    _, target_id, target_name, offer, request = parsed[0]
    return target_id, target_name, offer, request


def pending_trade_message(profile):
    pending = profile.get("pending_trade") if isinstance(profile, dict) else None
    if not pending:
        return "🤝 **No pending trade.**"
    return (
        f"🤝 **Pending trade from {pending.get('from_name', 'Unknown')}**\n"
        f"They give you: **{shared_format_trade_asset(pending['offer'])}**\n"
        f"They want: **{shared_format_trade_asset(pending['request'])}**\n\n"
        "Use `!accepttrade` or `!declinetrade`."
    )


async def make_chess_game_file(game, filename="chess_game.png"):
    board = chess.Board(game.get("fen", chess.STARTING_FEN))
    owner_id = game.get("theme_owner_id")
    owner_name = game.get("theme_owner_name", "Player")
    board_theme = "classic"
    piece_theme = "classic"
    if owner_id:
        try:
            profile = await asyncio.to_thread(
                get_cosmetic_profile,
                owner_id,
                owner_name,
            )
            board_theme = profile.get("active_board", "classic")
            piece_theme = profile.get("active_piece", "classic")
        except Exception as error:
            print(f"Chess game cosmetics lookup failed: {error}", flush=True)

    if game.get("mode") == "bot":
        human_id = game.get("human_id")
        orientation = str(game.get("white_id")) == str(human_id)
    else:
        orientation = True

    svg = render_custom_board_svg(
        board,
        orientation=orientation,
        board_theme=board_theme,
        piece_theme=piece_theme,
        size=500,
    )
    png = await asyncio.to_thread(
        cairosvg.svg2png,
        bytestring=svg.encode("utf-8"),
    )
    return discord.File(fp=BytesIO(png), filename=filename), board


async def send_chess_game_position(channel, game, note=None):
    file, board = await make_chess_game_file(game)
    white_rating = game.get("white_rating")
    black_rating = game.get("black_rating")
    white_text = game.get("white_name", "White")
    black_text = game.get("black_name", "Black")
    if white_rating is not None:
        white_text += f" ({int(round(float(white_rating)))})"
    if black_rating is not None:
        black_text += f" ({int(round(float(black_rating)))})"

    if board.is_game_over(claim_draw=True):
        turn_line = "🏁 **Game over.**"
    else:
        turn_line = f"➡️ **Turn:** {_turn_display(game, board)}"

    description = (
        f"⚪ **White:** {white_text}\n"
        f"⚫ **Black:** {black_text}\n"
        f"{turn_line}"
    )
    if game.get("last_move"):
        description += f"\n♟️ **Last move:** {game['last_move']}"
    if note:
        description += f"\n\n{note}"

    embed = discord.Embed(
        title="♜ Rated Chess Game",
        description=description,
        color=0x2F3136,
    )
    embed.set_image(url="attachment://chess_game.png")
    await channel.send(embed=embed, file=file)


async def finish_chess_game(channel, game, result, reason="Game finished"):
    if game.get("status") != "active":
        return

    result = str(result)
    white_score = 1.0 if result == "1-0" else 0.0 if result == "0-1" else 0.5
    lines = [f"🏁 **{reason}** — **{result}**"]

    # External coin/point transactions happen before the local game is marked
    # finished. They use deterministic transaction IDs, so a retry cannot pay
    # the same game twice if a later local save fails.
    if game.get("mode") == "bot":
        human_id = str(game.get("human_id"))
        human_name = game.get("human_name", "Player")
        human_is_white = str(game.get("white_id")) == human_id
        human_score = white_score if human_is_white else 1.0 - white_score
        reward = 3.0 if human_score >= 0.999 else 2.0 if human_score >= 0.499 else 0.0
        if reward > 0:
            try:
                await asyncio.to_thread(
                    shared_add_points,
                    human_id,
                    human_name,
                    reward,
                    f"chess-bot-game-reward:{game.get('game_id')}",
                    "rated-chess-bot-game",
                )
            except Exception as error:
                await channel.send(
                    f"❌ Could not safely record the chess game reward yet: `{str(error)[:700]}`"
                )
                return
        game["game_reward_points"] = reward
        lines.append(
            f"🏆 **Game reward:** +{shared_format_points(reward)} shared points • "
            f"+{shared_format_points(reward)} coins"
        )
        lines.append(bot_result_reaction(human_name, human_score))

    wager_amount = round(float(game.get("wager_amount", 0) or 0), 3)
    if game.get("mode") == "pvp" and wager_amount > 0 and game.get("wager_reserved"):
        winner_user_id = None
        winner_name = None
        if result == "1-0":
            winner_user_id = str(game.get("white_id"))
            winner_name = game.get("white_name", "White")
        elif result == "0-1":
            winner_user_id = str(game.get("black_id"))
            winner_name = game.get("black_name", "Black")

        try:
            await asyncio.to_thread(
                shared_settle_chess_wager,
                game.get("white_id"),
                game.get("white_name", "White"),
                game.get("black_id"),
                game.get("black_name", "Black"),
                wager_amount,
                winner_user_id,
                f"chess-wager-settle:{game.get('game_id')}",
            )
        except Exception as error:
            await channel.send(
                f"❌ Could not safely settle the chess wager yet: `{str(error)[:700]}`"
            )
            return

        game["wager_settled"] = True
        if winner_user_id is None:
            lines.append(
                f"🪙 **Wager draw:** both players get their "
                f"{shared_format_points(wager_amount)} coins back."
            )
        else:
            lines.append(
                f"🪙 **Wager winner:** {winner_name} receives the "
                f"**{shared_format_points(wager_amount * 2)} coin pot**."
            )

    game["status"] = "finished"
    game["result"] = result
    game["finished_at"] = time.time()
    game["finish_reason"] = reason

    ratings = _chess_ratings_state()

    if game.get("mode") == "bot":
        human_id = str(game.get("human_id"))
        human_name = game.get("human_name", "Player")
        human_is_white = str(game.get("white_id")) == human_id
        human_score = white_score if human_is_white else 1.0 - white_score
        change = apply_chess_single_result(
            ratings,
            human_id,
            human_name,
            float(game.get("bot_rating", CHESS_START_ELO)),
            human_score,
        )
        game["human_rating_after"] = change["after"]
        lines.append(
            f"♜ **{human_name}:** {int(round(change['before']))} → "
            f"**{int(round(change['after']))}** ({_signed_elo(change['change'])})"
        )
    else:
        changes = apply_chess_head_to_head_result(
            ratings,
            game.get("white_id"),
            game.get("white_name", "White"),
            game.get("black_id"),
            game.get("black_name", "Black"),
            white_score,
        )
        game["white_rating_after"] = changes["white"]["after"]
        game["black_rating_after"] = changes["black"]["after"]
        lines.extend([
            f"⚪ **{game.get('white_name', 'White')}:** {int(round(changes['white']['before']))} → "
            f"**{int(round(changes['white']['after']))}** ({_signed_elo(changes['white']['change'])})",
            f"⚫ **{game.get('black_name', 'Black')}:** {int(round(changes['black']['before']))} → "
            f"**{int(round(changes['black']['after']))}** ({_signed_elo(changes['black']['change'])})",
        ])

    _prune_chess_game_history()
    sync_ok = await save_all_critical()
    if not sync_ok:
        lines.append(
            "⚠️ **Chess Elo was saved locally, but the GitHub state sync is still failing.** "
            "The bot will retry on the next state save."
        )
    await channel.send("\n".join(lines))


async def maybe_finish_board_game(channel, game, board, reason=None):
    if not board.is_game_over(claim_draw=True):
        return False
    result = board.result(claim_draw=True)
    await send_chess_game_position(channel, game)
    if reason is None:
        outcome = board.outcome(claim_draw=True)
        if outcome is not None and outcome.termination is not None:
            reason = str(outcome.termination.name).replace("_", " ").title()
        else:
            reason = "Game finished"
    await finish_chess_game(channel, game, result, reason)
    return True


async def perform_bot_turn(channel, game, opening=False):
    if game.get("status") != "active":
        return
    board = chess.Board(game["fen"])
    bot_color = chess.WHITE if str(game.get("white_id")) == "BOT" else chess.BLACK
    if board.turn != bot_color:
        return

    try:
        move = await asyncio.to_thread(
            choose_bot_move,
            board.copy(stack=False),
            int(game.get("bot_rating", 1500)),
        )
    except StockfishUnavailableError as error:
        await channel.send(
            "❌ **Stockfish is unavailable, so this bot game cannot continue right now.**\n"
            f"{error}"
        )
        return
    except Exception as error:
        print(f"Stockfish move error: {error}", flush=True)
        await channel.send(
            "❌ **Stockfish could not calculate a move.** Try again in a moment or use `!resign`."
        )
        return

    if move is None:
        if await maybe_finish_board_game(channel, game, board):
            return
        return

    san = board.san(move)
    board.push(move)
    game["fen"] = board.fen()
    game.setdefault("moves", []).append(san)
    game["last_move"] = san
    game["last_move_at"] = time.time()
    await save_all()

    if await maybe_finish_board_game(channel, game, board):
        return

    note = "🤖 **The bot made the first move. Your turn.**" if opening else "🤖 **Bot replied. Your turn.**"
    await send_chess_game_position(channel, game, note)


async def start_bot_game(message, requested_rating=None):
    if _active_chess_game_for_user(message.author.id):
        await message.channel.send("❌ You already have an active chess game. Use `!resign` first.")
        return
    if _active_rush_for_user(message.author.id):
        await message.channel.send("❌ Finish your Puzzle Rush before starting a chess game.")
        return

    entry = chess_rating_entry(
        _chess_ratings_state(),
        message.author.id,
        message.author.display_name,
    )
    player_elo = float(entry["elo"])
    if requested_rating is None:
        bot_elo = random_bot_rating(player_elo)
    else:
        bot_elo = clamp_bot_rating(requested_rating)

    try:
        engine_info = await asyncio.to_thread(stockfish_engine_info)
    except StockfishUnavailableError as error:
        await message.channel.send(
            "❌ **Stockfish is unavailable on this bot runner.**\n"
            f"{error}"
        )
        return
    except Exception as error:
        print(f"Stockfish startup error: {error}", flush=True)
        await message.channel.send(
            "❌ **Stockfish could not start.** The rated bot game was not created."
        )
        return

    supported_min = int(engine_info.get("min_elo", BOT_MIN_ELO))
    supported_max = int(engine_info.get("max_elo", BOT_MAX_ELO))
    if not supported_min <= bot_elo <= supported_max:
        await message.channel.send(
            f"❌ This Stockfish build supports bot Elo **{supported_min}-{supported_max}**."
        )
        return

    human_white = bool(random.getrandbits(1))
    game_id = f"bot-{message.id}-{message.author.id}"
    game = {
        "game_id": game_id,
        "status": "active",
        "mode": "bot",
        "white_id": str(message.author.id) if human_white else "BOT",
        "white_name": message.author.display_name if human_white else f"Chess Bot {bot_elo}",
        "black_id": "BOT" if human_white else str(message.author.id),
        "black_name": f"Chess Bot {bot_elo}" if human_white else message.author.display_name,
        "white_rating": player_elo if human_white else bot_elo,
        "black_rating": bot_elo if human_white else player_elo,
        "human_id": str(message.author.id),
        "human_name": message.author.display_name,
        "bot_rating": bot_elo,
        "bot_engine": str(engine_info.get("name") or "Stockfish"),
        "fen": chess.STARTING_FEN,
        "moves": [],
        "last_move": None,
        "started_at": time.time(),
        "theme_owner_id": str(message.author.id),
        "theme_owner_name": message.author.display_name,
    }
    _chess_games_state()[game_id] = game
    await save_all()

    win_after = chess_elo_after(player_elo, bot_elo, 1.0)
    draw_after = chess_elo_after(player_elo, bot_elo, 0.5)
    loss_after = chess_elo_after(player_elo, bot_elo, 0.0)

    await message.channel.send(
        f"♜ **Rated game started!** Your Chess Elo: **{int(round(player_elo))}** • "
        f"Stockfish: **{bot_elo} Elo**\n"
        f"🏆 **Win:** {_signed_elo(win_after - player_elo)} Elo → **{int(round(win_after))}**\n"
        f"🤝 **Draw:** {_signed_elo(draw_after - player_elo)} Elo → **{int(round(draw_after))}**\n"
        f"❌ **Loss:** {_signed_elo(loss_after - player_elo)} Elo → **{int(round(loss_after))}**\n"
        "Enter moves like `e4`, `Nf3`, `!e4`, or `!move e4`. Use `!resign` to resign."
    )
    if human_white:
        await send_chess_game_position(message.channel, game, "👤 **You are White. Your move.**")
    else:
        await perform_bot_turn(message.channel, game, opening=True)


async def create_player_challenge(message, target, wager_amount=0):
    if target is None:
        await message.channel.send("❌ Player not found. Mention them, for example `!play @Thice`.")
        return
    if target.bot:
        await message.channel.send("❌ Use `!playbot` to play the chess bot.")
        return
    if target.id == message.author.id:
        await message.channel.send("❌ You cannot challenge yourself.")
        return
    if _active_chess_game_for_user(message.author.id):
        await message.channel.send("❌ You already have an active chess game.")
        return
    if _active_rush_for_user(message.author.id):
        await message.channel.send("❌ Finish your Puzzle Rush before challenging someone to chess.")
        return
    if _active_chess_game_for_user(target.id):
        await message.channel.send(f"❌ **{target.display_name}** already has an active chess game.")
        return
    if _active_rush_for_user(target.id):
        await message.channel.send(f"❌ **{target.display_name}** is currently playing Puzzle Rush.")
        return

    try:
        wager_amount = round(float(wager_amount or 0), 3)
    except Exception:
        wager_amount = -1
    if wager_amount < 0:
        await message.channel.send("❌ Chess wager must be **0 coins or more**.")
        return

    challenge = {
        "challenge_id": f"pvp-challenge:{message.id}:{message.author.id}:{target.id}",
        "challenger_id": str(message.author.id),
        "challenger_name": message.author.display_name,
        "target_id": str(target.id),
        "target_name": target.display_name,
        "wager_amount": wager_amount,
        "created_at": time.time(),
    }
    _chess_challenges_state()[str(target.id)] = challenge
    await save_all()
    challenger_elo = chess_rating_profile(message.author.id, message.author.display_name)["elo"]
    target_elo = chess_rating_profile(target.id, target.display_name)["elo"]
    wager_line = (
        f"\n🪙 **Wager:** {shared_format_points(wager_amount)} coins each • "
        f"winner gets **{shared_format_points(wager_amount * 2)} coins** • draw = refund"
        if wager_amount > 0
        else "\n🪙 **Wager:** Free game (0 coins)"
    )
    await message.channel.send(
        f"♜ <@{target.id}> **{message.author.display_name} challenged you to a rated game!**\n"
        f"{message.author.display_name}: **{int(round(challenger_elo))} Elo** • "
        f"{target.display_name}: **{int(round(target_elo))} Elo**"
        f"{wager_line}\n"
        "Use `!accept` or `!decline` within 10 minutes."
    )


async def accept_player_challenge(message):
    challenge = _chess_challenges_state().get(str(message.author.id))
    if not challenge:
        await message.channel.send("❌ You do not have a pending chess challenge.")
        return
    if time.time() - float(challenge.get("created_at", 0)) > CHESS_CHALLENGE_SECONDS:
        _chess_challenges_state().pop(str(message.author.id), None)
        await save_all()
        await message.channel.send("⌛ That chess challenge expired. Ask them to send `!play` again.")
        return

    challenger_id = str(challenge["challenger_id"])
    if (
        _active_chess_game_for_user(challenger_id)
        or _active_chess_game_for_user(message.author.id)
        or _active_rush_for_user(challenger_id)
        or _active_rush_for_user(message.author.id)
    ):
        _chess_challenges_state().pop(str(message.author.id), None)
        await save_all()
        await message.channel.send("❌ One of you already has an active chess game.")
        return

    challenger_member = message.guild.get_member(int(challenger_id))
    if challenger_member is None:
        try:
            challenger_member = await message.guild.fetch_member(int(challenger_id))
        except Exception:
            challenger_member = None
    if challenger_member is None:
        await message.channel.send("❌ The challenger is no longer available in this server.")
        return

    wager_amount = round(float(challenge.get("wager_amount", 0) or 0), 3)
    if wager_amount > 0:
        try:
            wager_result = await asyncio.to_thread(
                shared_reserve_chess_wager,
                challenger_id,
                challenge.get("challenger_name", challenger_member.display_name),
                message.author.id,
                message.author.display_name,
                wager_amount,
                f"chess-wager-reserve:{challenge.get('challenge_id', challenger_id + ':' + str(message.author.id))}",
            )
        except ValueError as error:
            await message.channel.send(f"❌ **Wager could not start:** {error}")
            return
        except Exception as error:
            await message.channel.send(f"❌ Could not safely reserve the chess wager: `{str(error)[:700]}`")
            return
    else:
        wager_result = None

    challenger_white = bool(random.getrandbits(1))
    white_member = challenger_member if challenger_white else message.author
    black_member = message.author if challenger_white else challenger_member
    white_entry = chess_rating_entry(_chess_ratings_state(), white_member.id, white_member.display_name)
    black_entry = chess_rating_entry(_chess_ratings_state(), black_member.id, black_member.display_name)
    game_id = f"pvp-{message.id}-{white_member.id}-{black_member.id}"
    game = {
        "game_id": game_id,
        "status": "active",
        "mode": "pvp",
        "white_id": str(white_member.id),
        "white_name": white_member.display_name,
        "black_id": str(black_member.id),
        "black_name": black_member.display_name,
        "white_rating": float(white_entry["elo"]),
        "black_rating": float(black_entry["elo"]),
        "fen": chess.STARTING_FEN,
        "moves": [],
        "last_move": None,
        "started_at": time.time(),
        "theme_owner_id": challenger_id,
        "theme_owner_name": challenge.get("challenger_name", challenger_member.display_name),
        "wager_amount": wager_amount,
        "wager_reserved": bool(wager_amount > 0),
        "wager_settled": False,
    }
    _chess_games_state()[game_id] = game
    _chess_challenges_state().pop(str(message.author.id), None)
    await save_all()
    wager_start_line = (
        f"\n🪙 **{shared_format_points(wager_amount)} coins each are now in the pot** "
        f"(**{shared_format_points(wager_amount * 2)} total**)."
        if wager_amount > 0
        else ""
    )
    await message.channel.send(
        f"✅ **Challenge accepted!** {white_member.display_name} is White; {black_member.display_name} is Black."
        f"{wager_start_line}\n"
        "Enter moves normally (`e4`, `Nf3`, `!move e4`). `!resign` resigns the game."
    )
    await send_chess_game_position(message.channel, game, f"➡️ **{white_member.display_name} to move.**")


async def handle_chess_game_move(message, game, move_text):
    async with chess_game_lock:
        if game.get("status") != "active":
            return
        board = chess.Board(game["fen"])
        user_color = _game_side_for_user(game, message.author.id)
        if user_color is None:
            return
        if board.turn != user_color:
            await message.channel.send(f"⏳ **Not your turn, {message.author.display_name}.**")
            return
        try:
            move, san = parse_chess_game_move(board, move_text)
        except ValueError:
            await message.channel.send(f"❌ **Illegal move, {message.author.display_name}.**")
            return

        board.push(move)
        game["fen"] = board.fen()
        game.setdefault("moves", []).append(san)
        game["last_move"] = san
        game["last_move_at"] = time.time()
        await save_all()

        if await maybe_finish_board_game(message.channel, game, board):
            return

        if game.get("mode") == "bot":
            await perform_bot_turn(message.channel, game)
        else:
            await send_chess_game_position(
                message.channel,
                game,
                f"✅ **{message.author.display_name}: {san}**",
            )


async def resign_chess_game(message):
    game = _active_chess_game_for_user(message.author.id)
    if not game:
        await message.channel.send("❌ You do not have an active chess game.")
        return
    color = _game_side_for_user(game, message.author.id)
    result = "0-1" if color == chess.WHITE else "1-0"
    await finish_chess_game(
        message.channel,
        game,
        result,
        f"{message.author.display_name} resigned",
    )


# =========================================================
# FIVE-MINUTE PUZZLE RUSH
# =========================================================

def _rush_seconds_left(session):
    return max(0, int(math.ceil(float(session.get("end_at", 0)) - time.time())))


def _rush_clock_text(seconds):
    seconds = max(0, int(seconds))
    return f"{seconds // 60}:{seconds % 60:02d}"


def _rush_target_rating(session):
    base = int(round(float(session.get("base_elo", 1500))))
    solved = int(session.get("score", 0))
    return max(
        RP_BANDS[0][0],
        min(RP_BANDS[-1][1], base - 300 + solved * 45),
    )


async def load_next_rush_puzzle(session):
    target = _rush_target_rating(session)
    data = await asyncio.to_thread(fetch_practice_puzzle, target, PUZZLE_RUSH_WINDOW)
    puzzle = build_puzzle(data)
    puzzle["posted_at"] = datetime.now(timezone.utc).isoformat()
    puzzle["puzzle_id"] = (
        f"practice_rush_{session['user_id']}_{data.get('lichess_id', 'offline')}_"
        f"{int(time.time() * 1000)}"
    )
    puzzle["rating"] = data.get("rating")
    puzzle["next_solution_index"] = 0
    try:
        profile = await asyncio.to_thread(
            get_cosmetic_profile,
            session["user_id"],
            session.get("name", "Player"),
        )
        puzzle["board_theme"] = profile.get("active_board", "classic")
        puzzle["piece_theme"] = profile.get("active_piece", "classic")
    except Exception:
        puzzle["board_theme"] = "classic"
        puzzle["piece_theme"] = "classic"
    session["puzzle"] = puzzle


async def send_rush_puzzle(channel, session, note=None):
    puzzle = session.get("puzzle")
    if not puzzle:
        return
    file, _board = await make_board_file(puzzle, "puzzle_rush.png")
    description = (
        f"⏱️ **Time:** {_rush_clock_text(_rush_seconds_left(session))}\n"
        f"✅ **Solved:** {int(session.get('score', 0))} • "
        f"❌ **Misses:** {int(session.get('wrong', 0))}\n"
        f"🎯 **Puzzle rating:** {int(puzzle.get('rating') or 0)}"
    )
    if note:
        description += f"\n\n{note}"
    embed = discord.Embed(
        title="⚡ Puzzle Rush — 5 Minutes",
        description=description,
        color=0xF1C40F,
    )
    embed.set_image(url="attachment://puzzle_rush.png")
    await channel.send(embed=embed, file=file)


async def end_puzzle_rush(channel, user_id, reason="Time!"):
    session = _rush_state().get(str(user_id))
    if not session or not session.get("active"):
        return
    session["active"] = False
    session["ended_at"] = time.time()
    score = int(session.get("score", 0))
    wrong = int(session.get("wrong", 0))
    bests = _rush_bests_state()
    previous_best = int(bests.get(str(user_id), 0) or 0)
    best = max(previous_best, score)
    bests[str(user_id)] = best
    await save_all()
    new_best = score > previous_best
    await channel.send(
        f"⏱️ **Puzzle Rush finished — {session.get('name', 'Player')}!**\n"
        f"{reason}\n"
        f"✅ Solved: **{score}** • ❌ Misses: **{wrong}** • "
        f"🏆 Best: **{best}**{' — NEW BEST!' if new_best else ''}\n"
        "Puzzle Rush is unranked: no shared points, coins or Puzzle Elo changes."
    )


async def start_puzzle_rush(message):
    if _active_rush_for_user(message.author.id):
        session = _active_rush_for_user(message.author.id)
        await message.channel.send(
            f"⚡ Your Puzzle Rush is already active — **{_rush_clock_text(_rush_seconds_left(session))}** left."
        )
        return
    if _active_chess_game_for_user(message.author.id):
        await message.channel.send("❌ Finish your active chess game before starting Puzzle Rush.")
        return

    stats = await asyncio.to_thread(
        puzzle_stats_for_user,
        message.author.id,
        message.author.display_name,
    )
    session = {
        "active": True,
        "user_id": str(message.author.id),
        "name": message.author.display_name,
        "started_at": time.time(),
        "end_at": time.time() + PUZZLE_RUSH_SECONDS,
        "score": 0,
        "wrong": 0,
        "base_elo": float(stats.get("elo", 1500)),
        "puzzle": None,
    }
    _rush_state()[str(message.author.id)] = session
    try:
        await load_next_rush_puzzle(session)
    except Exception as error:
        session["active"] = False
        await save_all()
        await message.channel.send(f"❌ Could not start Puzzle Rush: `{str(error)[:800]}`")
        return
    await save_all()
    await message.channel.send(
        "⚡ **5-minute Puzzle Rush started!** Solve as many as possible. "
        "Wrong answers skip to the next puzzle; the clock is the only limit."
    )
    await send_rush_puzzle(message.channel, session)


async def handle_rush_move(message, session, submitted):
    async with rush_lock:
        if not session.get("active"):
            return
        if _rush_seconds_left(session) <= 0:
            await end_puzzle_rush(message.channel, message.author.id, "The 5-minute clock expired.")
            return
        puzzle = session.get("puzzle")
        if not puzzle:
            await load_next_rush_puzzle(session)
            puzzle = session["puzzle"]

        next_index = int(puzzle.get("next_solution_index", 0))
        all_moves = puzzle.get("all_moves", [])
        if next_index >= len(all_moves):
            await load_next_rush_puzzle(session)
            await send_rush_puzzle(message.channel, session)
            return

        board = board_from_fen_safe(puzzle.get("current_fen", puzzle["fen"]))
        expected = all_moves[next_index]
        if expected.get("color") != puzzle.get("player_color"):
            # Defensive repair after a restart: automatically consume opponent
            # moves until it is the player's turn again.
            while next_index < len(all_moves) and all_moves[next_index].get("color") != puzzle.get("player_color"):
                auto = chess.Move.from_uci(all_moves[next_index]["uci"])
                if auto not in board.legal_moves:
                    break
                board.push(auto)
                next_index += 1
            puzzle["current_fen"] = board.fen()
            puzzle["next_solution_index"] = next_index
            if next_index >= len(all_moves):
                await load_next_rush_puzzle(session)
                await send_rush_puzzle(message.channel, session)
                return
            expected = all_moves[next_index]

        if not san_matches_move(board, submitted, expected):
            session["wrong"] = int(session.get("wrong", 0)) + 1
            answer = expected.get("san", expected.get("uci", "?"))
            await load_next_rush_puzzle(session)
            await save_all()
            await message.channel.send(f"❌ **Miss.** The move was **{answer}**. Next puzzle!")
            await send_rush_puzzle(message.channel, session)
            return

        move = chess.Move.from_uci(expected["uci"])
        san = board.san(move)
        board.push(move)
        next_index += 1
        opponent_replies = []
        while next_index < len(all_moves) and all_moves[next_index].get("color") != puzzle.get("player_color"):
            reply = all_moves[next_index]
            reply_move = chess.Move.from_uci(reply["uci"])
            if reply_move not in board.legal_moves:
                break
            opponent_replies.append(reply.get("san", reply["uci"]))
            board.push(reply_move)
            next_index += 1

        puzzle["current_fen"] = board.fen()
        puzzle["next_solution_index"] = next_index

        if next_index >= len(all_moves):
            session["score"] = int(session.get("score", 0)) + 1
            if _rush_seconds_left(session) <= 0:
                await save_all()
                await end_puzzle_rush(message.channel, message.author.id, "The 5-minute clock expired.")
                return
            await load_next_rush_puzzle(session)
            await save_all()
            await message.channel.send(
                f"✅ **Solved! +1** — score **{session['score']}**."
                + (f" Opponent: **{' '.join(opponent_replies)}**." if opponent_replies else "")
            )
            await send_rush_puzzle(message.channel, session)
            return

        await save_all()
        note = f"✅ **{san}**"
        if opponent_replies:
            note += f" • Opponent: **{' '.join(opponent_replies)}**"
        await send_rush_puzzle(message.channel, session, note)


async def check_puzzle_rush_expiry(channel):
    expired = [
        uid
        for uid, session in list(_rush_state().items())
        if isinstance(session, dict)
        and session.get("active")
        and _rush_seconds_left(session) <= 0
    ]
    for uid in expired:
        await end_puzzle_rush(channel, uid, "The 5-minute clock expired.")



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
    channel,
    owner=None,
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
            if owner is not None:
                try:
                    cosmetic = await asyncio.to_thread(
                        get_cosmetic_profile,
                        owner.id,
                        owner.display_name,
                    )
                    puzzle["board_theme"] = cosmetic.get("active_board", "classic")
                    puzzle["piece_theme"] = cosmetic.get("active_piece", "classic")
                except Exception:
                    puzzle["board_theme"] = "classic"
                    puzzle["piece_theme"] = "classic"
            else:
                puzzle["board_theme"] = "classic"
                puzzle["piece_theme"] = "classic"

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



async def post_practice_puzzle(channel, owner):
    """Post one personal rated Practice puzzle close to the owner's Puzzle Elo."""
    survival_active, survival_team = remote_survival_status()
    if survival_active:
        team = survival_team or active_team() or "another team"
        await channel.send(
            f"⚠️ **Survival Mode is active for {team}.** "
            "Practice is unavailable until Survival is paused."
        )
        return

    if rp_command_lock.locked():
        await channel.send("⏳ **A puzzle is already loading.**")
        return

    async with rp_command_lock:
        try:
            stats = await asyncio.to_thread(
                puzzle_stats_for_user,
                owner.id,
                owner.display_name,
            )
            target_elo = int(round(float(stats.get("elo", 1500))))
            data = await asyncio.to_thread(
                fetch_practice_puzzle,
                target_elo,
                100,
            )
            puzzle = build_puzzle(data)
            puzzle["posted_at"] = datetime.now(timezone.utc).isoformat()
            puzzle["puzzle_id"] = (
                "practice_"
                + str(data.get("lichess_id", "offline"))
                + "_"
                + str(owner.id)
                + "_"
                + str(int(time.time() * 1000))
            )
            puzzle["rating"] = data.get("rating")
            puzzle["rp_band"] = data.get("rp_band")
            puzzle["boss"] = False
            puzzle["practice_only"] = True
            puzzle["rated_practice"] = True
            puzzle["practice_owner_id"] = str(owner.id)
            puzzle["practice_owner_name"] = owner.display_name

            try:
                cosmetic = await asyncio.to_thread(
                    get_cosmetic_profile,
                    owner.id,
                    owner.display_name,
                )
                puzzle["board_theme"] = cosmetic.get("active_board", "classic")
                puzzle["piece_theme"] = cosmetic.get("active_piece", "classic")
            except Exception:
                puzzle["board_theme"] = "classic"
                puzzle["piece_theme"] = "classic"

            puzzle["current_fen"] = sanitize_fen(puzzle["fen"])
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

            file, board = await make_board_file(puzzle, "practice_puzzle.png")
            side = "White" if board.turn else "Black"
            count = puzzle["player_move_count"]
            move_description = (
                "Find the best move."
                if count == 1
                else f"Find the best line in **{count} {move_word(count)}**."
            )
            embed = discord.Embed(
                title=f"🎯 Practice — {data.get('rating', '?')} Elo",
                description=(
                    f"**{side} to move.**\n"
                    f"{move_description}\n\n"
                    f"Personal Practice for **{owner.display_name}**. "
                    f"Target Elo: **{target_elo}**.\n"
                    "This changes your Puzzle Elo/stats/streak, but gives **no shared points**."
                ),
                color=0x8E44AD,
            )
            embed.set_image(url="attachment://practice_puzzle.png")
            posted = await channel.send(embed=embed, file=file)
            puzzle["message_id"] = posted.id
            save_json(STATE_FILE, state)
        except Exception as error:
            print("PRACTICE PUZZLE ERROR:", flush=True)
            traceback.print_exc()
            error_text = str(error).strip() or repr(error)
            await channel.send(
                "❌ **Practice Puzzle Error**\n"
                f"```{error_text[:1400]}```"
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

    if puzzle.get("rated_practice"):
        source = "practice"
    else:
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
    if result.get("streak_bonus") and source != "practice":
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
    if bool(puzzle.get("practice_only")) or str(
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
        "🏆 **Shared Points**"
    )



BADGE_RARITY_BY_VALUE = {
    badge: rarity
    for rarity, badges in BADGE_POOLS.items()
    for badge in badges
}


def _page_slice(items, page, page_size):
    total_pages = max(1, math.ceil(len(items) / page_size))
    try:
        page = int(page)
    except Exception:
        page = 1
    page = max(1, min(page, total_pages))
    start = (page - 1) * page_size
    return items[start:start + page_size], page, total_pages


def _badge_rows(badges, rarity=None):
    counts = Counter(badges)
    first_index = {}
    for index, badge in enumerate(badges, 1):
        first_index.setdefault(badge, index)
    rows = []
    for badge, count in counts.items():
        badge_rarity = BADGE_RARITY_BY_VALUE.get(badge, "unknown")
        if rarity and badge_rarity != rarity:
            continue
        rows.append((first_index[badge], badge, badge_rarity, count))
    return sorted(rows, key=lambda row: row[0])


def shop_message(user_id, display_name):
    profile = get_cosmetic_profile(user_id, display_name)
    coins = shared_format_points(profile.get("coins", 0))
    color_names = " / ".join(config["label"] for config in NAME_COLORS.values())
    return (
        "🛒 **Puzzle Shop**\n"
        f"🪙 **Coins:** {coins}  •  `!coins` / `!bank`\n"
        "💸 `!donate <name> <coins|badge>` — donate coins or one badge; points never move.\n🤝 `!trade <name> <give> <receive>` — trade coins/badges; `!pendingtrade` / `!accepttrade` / `!declinetrade`.\n\n"
        f"🎁 **Badge Box — {shared_format_points(BADGE_BOX_COST)} coins**\n"
        "`!box` or `!shop box` — open one random badge. Duplicates are possible.\n\n"
        f"🎨 **Boards — {shared_format_points(BOARD_COST)} coins each**\n"
        "`!customboard` — catalogue • `!customboard blue test` — preview • `!customboard blue buy` — buy • `!customboard blue` — equip.\n\n"
        f"♟️ **Piece Sets — {shared_format_points(PIECE_COST)} coins each**\n"
        "`!custompiece` — catalogue • `!custompiece figurine-gold test` — preview • `!custompiece figurine-gold buy` — buy/equip.\n\n"
        f"🖌️ **Name Colors — {shared_format_points(COLOR_COST)} coins each**\n"
        f"`!color` — {color_names}. Higher protected server roles still win (owner blue / subscriber pink).\n\n"
        f"❤️ **Survival Heart — {shared_format_points(SURVIVAL_HEART_COST)} coins**\n"
        "Captain-only `!heart` while the run is active and missing a heart; max one purchased heart per run.\n\n"
        "👤 `!me` / `!profile` — inventory, active badge, board, pieces and color."
    )


def cosmetic_profile_dashboard(user_id, display_name):
    profile = get_cosmetic_profile(user_id, display_name)
    active_badge = profile.get("active_badge") or "—"
    active_board_key = profile.get("active_board", "classic")
    active_piece_key = profile.get("active_piece", "classic")
    active_color_key = profile.get("active_color", "")
    active_color = (
        NAME_COLORS.get(active_color_key, {}).get("label", active_color_key.title())
        if active_color_key else "Default"
    )
    badges = list(profile.get("badges", []))
    unique_badges = set(badges)
    rarity_counts = {
        rarity: len({badge for badge in unique_badges if BADGE_RARITY_BY_VALUE.get(badge) == rarity})
        for rarity in RARITY_LABELS
    }
    rarity_lines = " • ".join(
        f"{RARITY_LABELS[rarity]} {rarity_counts[rarity]}"
        for rarity in ("legendary", "epic", "rare", "uncommon", "common", "basic")
    )
    return (
        f"👤 **Profile — {active_badge + ' ' if active_badge != '—' else ''}{profile.get('name', display_name)}**\n"
        f"🪙 **Coins:** {shared_format_points(profile.get('coins', 0))}\n"
        f"🏅 **Active badge:** {active_badge}\n"
        f"🎨 **Active board:** {BOARD_DISPLAY_NAMES.get(active_board_key, str(active_board_key).title())}\n"
        f"♟️ **Active pieces:** {PIECE_DISPLAY_NAMES.get(active_piece_key, str(active_piece_key).title())}\n"
        f"🖌️ **Active color:** {active_color}\n\n"
        f"🏅 **Badges:** {len(unique_badges)} unique / {len(badges)} total\n"
        f"{rarity_lines}\n"
        f"🎨 **Boards owned:** {len(profile.get('boards', [])) + 1}/{len(BOARD_THEMES)}\n"
        f"♟️ **Piece sets owned:** {len(profile.get('pieces', [])) + 1}/{len(PIECE_SETS)}\n"
        f"🖌️ **Colors owned:** {len(profile.get('colors', []))}/{len(NAME_COLORS)}\n\n"
        "**Collection**\n"
        "Use the buttons below to browse badges, boards, pieces and colors.\n"
        "On your own profile, click an owned cosmetic to equip it. `!profile badge 0` still unequips your badge."
    )


def cosmetic_badge_overview(user_id, display_name):
    profile = get_cosmetic_profile(user_id, display_name)
    badges = list(profile.get("badges", []))
    unique = set(badges)
    lines = [
        f"🏅 **{profile.get('name', display_name)} — Badge Collection**",
        f"**{len(unique)} unique / {len(badges)} total**",
        "",
    ]
    for rarity in ("legendary", "epic", "rare", "uncommon", "common", "basic"):
        owned = len({badge for badge in unique if BADGE_RARITY_BY_VALUE.get(badge) == rarity})
        total = len(BADGE_POOLS[rarity])
        lines.append(
            f"**{RARITY_LABELS[rarity]}:** {owned}/{total}"
        )
    lines.extend(["", "Use the rarity buttons to open a collection. Pages show up to 20 unique badges; duplicates appear as `×2`, `×3`, etc."])
    return "\n".join(lines)


def cosmetic_badge_page(user_id, display_name, rarity, page=1):
    rarity = str(rarity).casefold()
    if rarity not in BADGE_POOLS:
        raise ValueError("Unknown rarity. Use Legendary, Epic, Rare, Uncommon, Common or Basic.")
    profile = get_cosmetic_profile(user_id, display_name)
    rows = _badge_rows(list(profile.get("badges", [])), rarity)
    page_rows, page, total_pages = _page_slice(rows, page, 20)
    lines = [
        f"🏅 **{profile.get('name', display_name)} — {RARITY_LABELS[rarity]} Badges**",
        f"Page **{page}/{total_pages}** • {len(rows)} unique owned",
        "",
    ]
    if not page_rows:
        lines.append("None owned in this rarity yet.")
    else:
        for index, badge, _badge_rarity, count in page_rows:
            suffix = f" ×{count}" if count > 1 else ""
            lines.append(f"`#{index}` {badge}{suffix}")
    lines.extend(["", "Use the buttons below to browse. On your own profile, click a badge button to equip it."])
    return "\n".join(lines)


def cosmetic_board_page(user_id, display_name, page=1):
    profile = get_cosmetic_profile(user_id, display_name)
    owned = ["classic"] + list(profile.get("boards", []))
    page_items, page, total_pages = _page_slice(owned, page, 20)
    lines = [
        f"🎨 **{profile.get('name', display_name)} — Owned Boards**",
        f"Page **{page}/{total_pages}** • {len(owned)}/{len(BOARD_THEMES)} owned",
        "",
    ]
    for name in page_items:
        marker = " ✅" if name == profile.get("active_board", "classic") else ""
        lines.append(f"• **{BOARD_DISPLAY_NAMES.get(name, name.title())}** (`{name}`){marker}")
    lines.extend(["", "Use the buttons below to browse/equip owned boards. `!customboard` opens the shop catalogue."])
    return "\n".join(lines)


def cosmetic_piece_page(user_id, display_name, page=1):
    profile = get_cosmetic_profile(user_id, display_name)
    owned = ["classic"] + list(profile.get("pieces", []))
    page_items, page, total_pages = _page_slice(owned, page, 20)
    lines = [
        f"♟️ **{profile.get('name', display_name)} — Owned Piece Sets**",
        f"Page **{page}/{total_pages}** • {len(owned)}/{len(PIECE_SETS)} owned",
        "",
    ]
    for name in page_items:
        marker = " ✅" if name == profile.get("active_piece", "classic") else ""
        lines.append(f"• **{PIECE_DISPLAY_NAMES.get(name, name.title())}** (`{name}`){marker}")
    lines.extend(["", "Use the buttons below to browse/equip owned piece sets. `!custompiece` opens the shop catalogue."])
    return "\n".join(lines)


def cosmetic_color_page(user_id, display_name):
    profile = get_cosmetic_profile(user_id, display_name)
    active = profile.get("active_color", "")
    lines = [f"🖌️ **{profile.get('name', display_name)} — Owned Colors**", ""]
    if not profile.get("colors", []):
        lines.append("None yet. Default/server role color is active.")
    for name in profile.get("colors", []):
        marker = " ✅" if name == active else ""
        lines.append(f"• **{NAME_COLORS[name]['label']}** (`{name}`){marker}")
    lines.extend(["", "Use `!color <name>` to equip an owned color, or `!color default` to return to your normal server color."])
    return "\n".join(lines)


def board_catalog_message(page=1):
    names = list(BOARD_THEMES)
    page_names, page, total_pages = _page_slice(names, page, 25)
    lines = [
        "🎨 **Custom Boards**",
        f"Price: **{shared_format_points(BOARD_COST)} coins** each. Classic is free.",
        f"Page **{page}/{total_pages}** • {len(BOARD_THEMES)} themes",
        "",
    ]
    for start in range(0, len(page_names), 5):
        lines.append(" • ".join(BOARD_DISPLAY_NAMES[name] for name in page_names[start:start + 5]))
    lines.extend([
        "",
        "Use **Previous / Next** below to browse pages.",
        "`!customboard blue test` — preview",
        "`!customboard blue buy` — buy",
        "`!customboard blue` — equip if owned",
        "`!customboard default` — equip Classic",
    ])
    return "\n".join(lines)


def piece_catalog_message(page=1):
    names = list(PIECE_SETS)
    page_names, page, total_pages = _page_slice(names, page, 20)
    lines = [
        "♟️ **Custom Piece Sets**",
        f"Price: **{shared_format_points(PIECE_COST)} coins** each. Classic is free.",
        f"Page **{page}/{total_pages}** • {len(PIECE_SETS)} sets",
        "",
    ]
    for name in page_names:
        lines.append(f"• **{PIECE_DISPLAY_NAMES[name]}** (`{name}`)")
    lines.extend([
        "",
        "Use **Previous / Next** below to browse pages.",
        "`!custompiece figurine-gold test` — preview",
        "`!custompiece figurine-gold buy` — buy",
        "`!custompiece figurine-gold` — equip if owned",
        "`!custompiece default` — equip Classic",
    ])
    return "\n".join(lines)


def color_catalog_message():
    color_line = " • ".join(
        f"**{config['label']}** (`{name}`)"
        for name, config in NAME_COLORS.items()
    )
    return (
        "🖌️ **Name Colors**\n"
        f"Each color costs **{shared_format_points(COLOR_COST)} coins**.\n\n"
        f"{color_line}\n\n"
        "`!color red buy` — buy a color\n"
        "`!color red` — equip a color you own\n"
        "`!color default` — remove your shop color and return to your normal server color\n\n"
        "Higher existing server color roles still win, so Sharkmeister can stay blue and subscribers can stay pink."
    )



PROFILE_RARITY_ORDER = ("legendary", "epic", "rare", "uncommon", "common", "basic")


def _button_emoji(value):
    try:
        text = str(value or "")
        if text.startswith("<:") or text.startswith("<a:"):
            return discord.PartialEmoji.from_str(text)
        return text or None
    except Exception:
        return None


class CosmeticCatalogPager(discord.ui.View):
    """Clickable board/piece shop browser with instant previews."""

    def __init__(self, viewer_id, kind, page=1, selected_name=None):
        super().__init__(timeout=300)
        self.viewer_id = int(viewer_id)
        self.kind = "board" if str(kind) == "board" else "piece"
        self.names = list(BOARD_THEMES) if self.kind == "board" else list(PIECE_SETS)
        self.page_size = 5
        self.total_pages = max(1, math.ceil(len(self.names) / self.page_size))
        self.page = max(1, min(int(page or 1), self.total_pages))
        page_names = self._page_names()
        wanted = str(selected_name or "").casefold()
        self.selected_name = wanted if wanted in page_names else page_names[0]
        self._rebuild()

    async def interaction_check(self, interaction):
        if interaction.user.id != self.viewer_id:
            await interaction.response.send_message("This catalogue belongs to another user.", ephemeral=True)
            return False
        return True

    def _page_names(self):
        start = (self.page - 1) * self.page_size
        return self.names[start:start + self.page_size]

    def _display_name(self, name):
        if self.kind == "board":
            return BOARD_DISPLAY_NAMES.get(name, name.title())
        return PIECE_DISPLAY_NAMES.get(name, name.title())

    def render(self, profile=None):
        selected = self.selected_name
        display = self._display_name(selected)
        price = BOARD_COST if self.kind == "board" else PIECE_COST
        item_word = "board" if self.kind == "board" else "piece set"
        owned = False
        active = False
        if profile is not None:
            if self.kind == "board":
                owned = selected == "classic" or selected in profile.get("boards", [])
                active = selected == profile.get("active_board", "classic")
            else:
                owned = selected == "classic" or selected in profile.get("pieces", [])
                active = selected == profile.get("active_piece", "classic")
        status = "Classic / free" if selected == "classic" else ("Owned" if owned else f"{shared_format_points(price)} coins")
        if active:
            status += " • Equipped"
        return (
            f"{'🎨' if self.kind == 'board' else '♟️'} **Custom {'Boards' if self.kind == 'board' else 'Piece Sets'}**\n"
            f"Page **{self.page}/{self.total_pages}** • choose one of the 5 buttons below.\n\n"
            f"**Preview:** {display}\n"
            f"**Status:** {status}\n\n"
            f"Click an option to instantly preview that {item_word}. Use **Buy selected** or **Equip selected** when ready."
        )

    async def preview_file(self, display_name):
        profile = await asyncio.to_thread(get_cosmetic_profile, self.viewer_id, display_name)
        if self.kind == "board":
            board_name = self.selected_name
            piece_name = profile.get("active_piece", "classic")
            filename = "board_shop_preview.png"
        else:
            board_name = profile.get("active_board", "classic")
            piece_name = self.selected_name
            filename = "piece_shop_preview.png"
        file = await asyncio.to_thread(
            make_cosmetic_preview_file,
            board_name,
            piece_name,
            filename,
        )
        return profile, file

    async def _show_selected(self, interaction):
        await interaction.response.defer()
        try:
            profile, file = await self.preview_file(interaction.user.display_name)
            self._rebuild(profile)
            await interaction.message.edit(
                content=self.render(profile),
                attachments=[file],
                view=self,
            )
        except Exception as error:
            await interaction.followup.send(f"❌ Could not render preview: `{str(error)[:800]}`", ephemeral=True)

    def _rebuild(self, profile=None):
        self.clear_items()
        page_names = self._page_names()
        if self.selected_name not in page_names:
            self.selected_name = page_names[0]

        for name in page_names:
            button = discord.ui.Button(
                label=self._display_name(name)[:80],
                style=discord.ButtonStyle.primary if name == self.selected_name else discord.ButtonStyle.secondary,
                row=0,
            )

            async def select_callback(interaction, selected=name):
                self.selected_name = selected
                await self._show_selected(interaction)

            button.callback = select_callback
            self.add_item(button)

        previous = discord.ui.Button(label="◀", style=discord.ButtonStyle.secondary, disabled=self.page <= 1, row=1)
        indicator = discord.ui.Button(label=f"{self.page}/{self.total_pages}", style=discord.ButtonStyle.secondary, disabled=True, row=1)
        next_button = discord.ui.Button(label="▶", style=discord.ButtonStyle.secondary, disabled=self.page >= self.total_pages, row=1)

        async def previous_callback(interaction):
            self.page = max(1, self.page - 1)
            self.selected_name = self._page_names()[0]
            await self._show_selected(interaction)

        async def next_callback(interaction):
            self.page = min(self.total_pages, self.page + 1)
            self.selected_name = self._page_names()[0]
            await self._show_selected(interaction)

        previous.callback = previous_callback
        next_button.callback = next_callback
        self.add_item(previous)
        self.add_item(indicator)
        self.add_item(next_button)

        buy = discord.ui.Button(
            label="Buy selected",
            style=discord.ButtonStyle.success,
            disabled=self.selected_name == "classic",
            row=2,
        )
        equip = discord.ui.Button(label="Equip selected", style=discord.ButtonStyle.primary, row=2)

        async def buy_callback(interaction):
            name = self.selected_name
            await interaction.response.defer()
            try:
                if self.kind == "board":
                    updated = await asyncio.to_thread(
                        buy_board,
                        interaction.user.id,
                        interaction.user.display_name,
                        name,
                        f"catalog-buy-board:{interaction.id}:{interaction.user.id}:{name}",
                    )
                    label = BOARD_DISPLAY_NAMES.get(name, name.title())
                else:
                    updated = await asyncio.to_thread(
                        buy_piece,
                        interaction.user.id,
                        interaction.user.display_name,
                        name,
                        f"catalog-buy-piece:{interaction.id}:{interaction.user.id}:{name}",
                    )
                    label = PIECE_DISPLAY_NAMES.get(name, name.title())
                self._rebuild(updated)
                await interaction.message.edit(content=self.render(updated), view=self)
                await interaction.followup.send(
                    f"✅ Bought **{label}**. 🪙 Coins left: **{shared_format_points(updated['coins'])}**",
                    ephemeral=True,
                )
            except Exception as error:
                await interaction.followup.send(f"❌ Could not buy it: `{str(error)[:800]}`", ephemeral=True)

        async def equip_callback(interaction):
            name = self.selected_name
            await interaction.response.defer()
            try:
                if self.kind == "board":
                    updated = await asyncio.to_thread(
                        equip_board,
                        interaction.user.id,
                        interaction.user.display_name,
                        name,
                        f"catalog-equip-board:{interaction.id}:{interaction.user.id}:{name}",
                    )
                    label = BOARD_DISPLAY_NAMES.get(updated.get("active_board", name), name.title())
                else:
                    updated = await asyncio.to_thread(
                        equip_piece,
                        interaction.user.id,
                        interaction.user.display_name,
                        name,
                        f"catalog-equip-piece:{interaction.id}:{interaction.user.id}:{name}",
                    )
                    label = PIECE_DISPLAY_NAMES.get(updated.get("active_piece", name), name.title())
                self._rebuild(updated)
                await interaction.message.edit(content=self.render(updated), view=self)
                await interaction.followup.send(f"✅ Equipped **{label}**.", ephemeral=True)
            except Exception as error:
                await interaction.followup.send(f"❌ Could not equip it: `{str(error)[:800]}`", ephemeral=True)

        buy.callback = buy_callback
        equip.callback = equip_callback
        self.add_item(buy)
        self.add_item(equip)


async def send_cosmetic_catalog_preview(message, kind, page=1):
    view = CosmeticCatalogPager(message.author.id, kind, page)
    profile, file = await view.preview_file(message.author.display_name)
    view._rebuild(profile)
    await message.channel.send(view.render(profile), file=file, view=view)

class CosmeticProfileView(discord.ui.View):
    def __init__(self, viewer_id, target_user_id, target_name, editable=False):
        super().__init__(timeout=300)
        self.viewer_id = int(viewer_id)
        self.target_user_id = str(target_user_id)
        self.target_name = str(target_name)
        self.editable = bool(editable and str(viewer_id) == str(target_user_id))
        self.mode = "dashboard"
        self.rarity = None
        self.page = 1
        self._build_dashboard()

    async def interaction_check(self, interaction):
        if interaction.user.id != self.viewer_id:
            await interaction.response.send_message("Open your own `!profile` to use these buttons.", ephemeral=True)
            return False
        return True

    async def _profile(self):
        return await asyncio.to_thread(get_cosmetic_profile, self.target_user_id, self.target_name)

    def render(self, profile=None):
        if self.mode == "dashboard":
            if profile is None:
                return cosmetic_profile_dashboard(self.target_user_id, self.target_name)
            active_badge = profile.get("active_badge") or "—"
            active_board_key = profile.get("active_board", "classic")
            active_piece_key = profile.get("active_piece", "classic")
            active_color_key = profile.get("active_color", "")
            active_color = NAME_COLORS.get(active_color_key, {}).get("label", active_color_key.title()) if active_color_key else "Default"
            badges = list(profile.get("badges", []))
            unique_badges = set(badges)
            rarity_lines = " • ".join(
                f"{RARITY_LABELS[rarity]} {len({badge for badge in unique_badges if BADGE_RARITY_BY_VALUE.get(badge) == rarity})}"
                for rarity in PROFILE_RARITY_ORDER
            )
            return (
                f"👤 **Profile — {active_badge + ' ' if active_badge != '—' else ''}{profile.get('name', self.target_name)}**\n"
                f"🪙 **Coins:** {shared_format_points(profile.get('coins', 0))}\n"
                f"🏅 **Active badge:** {active_badge}\n"
                f"🎨 **Active board:** {BOARD_DISPLAY_NAMES.get(active_board_key, str(active_board_key).title())}\n"
                f"♟️ **Active pieces:** {PIECE_DISPLAY_NAMES.get(active_piece_key, str(active_piece_key).title())}\n"
                f"🖌️ **Active color:** {active_color}\n\n"
                f"🏅 **Badges:** {len(unique_badges)} unique / {len(badges)} total\n{rarity_lines}\n"
                f"🎨 **Boards owned:** {len(profile.get('boards', [])) + 1}/{len(BOARD_THEMES)}\n"
                f"♟️ **Piece sets owned:** {len(profile.get('pieces', [])) + 1}/{len(PIECE_SETS)}\n"
                f"🖌️ **Colors owned:** {len(profile.get('colors', []))}/{len(NAME_COLORS)}\n\n"
                "Use the buttons below to browse the collection."
            )
        if self.mode == "badges":
            if profile is None:
                return cosmetic_badge_page(self.target_user_id, self.target_name, self.rarity, self.page)
            rows = _badge_rows(list(profile.get("badges", [])), self.rarity)
            page_rows, current_page, total_pages = _page_slice(rows, self.page, 20)
            self.page = current_page
            lines = [
                f"🏅 **{profile.get('name', self.target_name)} — {RARITY_LABELS[self.rarity]} Badges**",
                f"Page **{self.page}/{total_pages}** • {len(rows)} unique owned",
                "",
            ]
            if not page_rows:
                lines.append("None owned in this rarity yet.")
            else:
                for index, badge, _badge_rarity, count in page_rows:
                    suffix = f" ×{count}" if count > 1 else ""
                    active = " ✅" if badge == profile.get("active_badge", "") else ""
                    lines.append(f"`#{index}` {badge}{suffix}{active}")
            lines.extend(["", "Use the buttons below to browse. On your own profile, click a badge button to equip it."])
            return "\n".join(lines)
        if self.mode == "boards":
            if profile is None:
                return cosmetic_board_page(self.target_user_id, self.target_name, self.page)
            owned = ["classic"] + list(profile.get("boards", []))
            page_items, self.page, total_pages = _page_slice(owned, self.page, 20)
            lines = [f"🎨 **{profile.get('name', self.target_name)} — Owned Boards**", f"Page **{self.page}/{total_pages}** • {len(owned)}/{len(BOARD_THEMES)} owned", ""]
            for name in page_items:
                marker = " ✅" if name == profile.get("active_board", "classic") else ""
                lines.append(f"• **{BOARD_DISPLAY_NAMES.get(name, name.title())}** (`{name}`){marker}")
            lines.extend(["", "Use the buttons below to browse/equip owned boards. `!customboard` opens the shop catalogue."])
            return "\n".join(lines)
        if self.mode == "pieces":
            if profile is None:
                return cosmetic_piece_page(self.target_user_id, self.target_name, self.page)
            owned = ["classic"] + list(profile.get("pieces", []))
            page_items, self.page, total_pages = _page_slice(owned, self.page, 20)
            lines = [f"♟️ **{profile.get('name', self.target_name)} — Owned Piece Sets**", f"Page **{self.page}/{total_pages}** • {len(owned)}/{len(PIECE_SETS)} owned", ""]
            for name in page_items:
                marker = " ✅" if name == profile.get("active_piece", "classic") else ""
                lines.append(f"• **{PIECE_DISPLAY_NAMES.get(name, name.title())}** (`{name}`){marker}")
            lines.extend(["", "Use the buttons below to browse/equip owned piece sets. `!custompiece` opens the shop catalogue."])
            return "\n".join(lines)
        if self.mode == "colors":
            if profile is None:
                return cosmetic_color_page(self.target_user_id, self.target_name)
            active = profile.get("active_color", "")
            lines = [f"🖌️ **{profile.get('name', self.target_name)} — Owned Colors**", ""]
            if not profile.get("colors", []):
                lines.append("None yet. Default/server role color is active.")
            for name in profile.get("colors", []):
                if name in NAME_COLORS:
                    marker = " ✅" if name == active else ""
                    lines.append(f"• **{NAME_COLORS[name]['label']}** (`{name}`){marker}")
            lines.extend(["", "Use `!color <name>` to equip an owned color, or `!color default` to return to your normal server color."])
            return "\n".join(lines)
        return cosmetic_profile_dashboard(self.target_user_id, self.target_name)

    def _build_dashboard(self):
        self.clear_items()
        self.mode = "dashboard"
        self.rarity = None
        self.page = 1
        for idx, rarity in enumerate(PROFILE_RARITY_ORDER):
            button = discord.ui.Button(
                label=RARITY_LABELS[rarity],
                style=discord.ButtonStyle.primary if rarity in {"legendary", "epic", "rare"} else discord.ButtonStyle.secondary,
                row=idx // 3,
            )
            async def open_rarity(interaction, rarity=rarity):
                profile = await self._profile()
                self.mode = "badges"
                self.rarity = rarity
                self.page = 1
                self._build_badges(profile)
                await interaction.response.edit_message(content=self.render(profile), view=self)
            button.callback = open_rarity
            self.add_item(button)

        for label, mode, emoji in (("Boards", "boards", "🎨"), ("Pieces", "pieces", "♟️"), ("Colors", "colors", "🖌️")):
            button = discord.ui.Button(label=label, emoji=emoji, style=discord.ButtonStyle.secondary, row=2)
            async def open_mode(interaction, mode=mode):
                profile = await self._profile()
                self.mode = mode
                self.page = 1
                if mode in {"boards", "pieces"}:
                    self._build_assets(profile)
                else:
                    self._build_colors()
                await interaction.response.edit_message(content=self.render(profile), view=self)
            button.callback = open_mode
            self.add_item(button)

    def _build_badges(self, profile):
        self.clear_items()
        rows = _badge_rows(list(profile.get("badges", [])), self.rarity)
        page_rows, self.page, total_pages = _page_slice(rows, self.page, 20)
        if self.editable:
            active = profile.get("active_badge", "")
            for pos, (index, badge, _rarity, _count) in enumerate(page_rows):
                button = discord.ui.Button(
                    label=f"#{index}", emoji=_button_emoji(badge),
                    style=discord.ButtonStyle.success if badge == active else discord.ButtonStyle.secondary,
                    row=pos // 5,
                )
                async def equip_callback(interaction, badge=badge):
                    updated = await asyncio.to_thread(
                        equip_badge, self.target_user_id, self.target_name, badge,
                        f"profile-button-badge:{interaction.id}:{self.target_user_id}",
                    )
                    self._build_badges(updated)
                    await interaction.response.edit_message(content=self.render(updated), view=self)
                button.callback = equip_callback
                self.add_item(button)
        self._add_nav(total_pages, include_none=self.editable)

    def _build_assets(self, profile):
        self.clear_items()
        if self.mode == "boards":
            owned = ["classic"] + list(profile.get("boards", []))
            active = profile.get("active_board", "classic")
            display = BOARD_DISPLAY_NAMES
            equip_func = equip_board
        else:
            owned = ["classic"] + list(profile.get("pieces", []))
            active = profile.get("active_piece", "classic")
            display = PIECE_DISPLAY_NAMES
            equip_func = equip_piece
        page_items, self.page, total_pages = _page_slice(owned, self.page, 20)
        if self.editable:
            for pos, name in enumerate(page_items):
                button = discord.ui.Button(
                    label=display.get(name, name.title())[:80],
                    style=discord.ButtonStyle.success if name == active else discord.ButtonStyle.secondary,
                    row=pos // 5,
                )
                async def equip_callback(interaction, name=name, equip_func=equip_func):
                    updated = await asyncio.to_thread(
                        equip_func, self.target_user_id, self.target_name, name,
                        f"profile-button-{self.mode}:{interaction.id}:{self.target_user_id}:{name}",
                    )
                    self._build_assets(updated)
                    await interaction.response.edit_message(content=self.render(updated), view=self)
                button.callback = equip_callback
                self.add_item(button)
        self._add_nav(total_pages)

    def _build_colors(self):
        self.clear_items()
        back = discord.ui.Button(label="← Profile", style=discord.ButtonStyle.primary)
        async def back_callback(interaction):
            profile = await self._profile()
            self._build_dashboard()
            await interaction.response.edit_message(content=self.render(profile), view=self)
        back.callback = back_callback
        self.add_item(back)

    def _add_nav(self, total_pages, include_none=False):
        back = discord.ui.Button(label="← Profile", style=discord.ButtonStyle.primary, row=4)
        previous = discord.ui.Button(label="◀", style=discord.ButtonStyle.secondary, row=4, disabled=self.page <= 1)
        indicator = discord.ui.Button(label=f"{self.page}/{max(1, total_pages)}", style=discord.ButtonStyle.secondary, row=4, disabled=True)
        next_button = discord.ui.Button(label="▶", style=discord.ButtonStyle.secondary, row=4, disabled=self.page >= max(1, total_pages))

        async def back_callback(interaction):
            profile = await self._profile()
            self._build_dashboard()
            await interaction.response.edit_message(content=self.render(profile), view=self)

        async def previous_callback(interaction):
            profile = await self._profile()
            self.page = max(1, self.page - 1)
            if self.mode == "badges":
                self._build_badges(profile)
            else:
                self._build_assets(profile)
            await interaction.response.edit_message(content=self.render(profile), view=self)

        async def next_callback(interaction):
            profile = await self._profile()
            self.page = min(max(1, total_pages), self.page + 1)
            if self.mode == "badges":
                self._build_badges(profile)
            else:
                self._build_assets(profile)
            await interaction.response.edit_message(content=self.render(profile), view=self)

        back.callback = back_callback
        previous.callback = previous_callback
        next_button.callback = next_callback
        self.add_item(back)
        self.add_item(previous)
        self.add_item(indicator)
        self.add_item(next_button)

        if include_none:
            none_button = discord.ui.Button(label="No badge", style=discord.ButtonStyle.danger, row=4)
            async def none_callback(interaction):
                updated = await asyncio.to_thread(
                    equip_badge, self.target_user_id, self.target_name, "",
                    f"profile-button-badge:{interaction.id}:{self.target_user_id}:none",
                )
                self._build_badges(updated)
                await interaction.response.edit_message(content=self.render(updated), view=self)
            none_button.callback = none_callback
            self.add_item(none_button)


async def resolve_cosmetic_profile_target(message, typed_name):
    query = str(typed_name or "").strip()
    if message.mentions:
        target = message.mentions[0]
        return str(target.id), target.display_name
    if not query:
        return str(message.author.id), message.author.display_name
    target = await asyncio.to_thread(shared_resolve_cosmetic_profile, query)
    return str(target["user_id"]), target.get("name", query)


def make_cosmetic_preview_file(board_theme="classic", piece_theme="classic", filename="cosmetic_preview.png"):
    board_theme = str(board_theme or "classic").casefold()
    piece_theme = str(piece_theme or "classic").casefold()
    board = chess.Board()
    svg = render_custom_board_svg(
        board,
        orientation=True,
        board_theme=board_theme,
        piece_theme=piece_theme,
        size=500,
    )
    png = cairosvg.svg2png(bytestring=svg.encode("utf-8"))
    return discord.File(BytesIO(png), filename=filename)



def cosmetic_profile_messages(user_id, display_name):
    """Backward-compatible wrapper: the profile is now intentionally compact."""
    return [cosmetic_profile_dashboard(user_id, display_name)]


def make_board_preview_file(theme_name):
    """Backward-compatible board preview using Classic pieces."""
    return make_cosmetic_preview_file(theme_name, "classic", "board_theme_preview.png")

def _highest_nonshop_colored_role(member):
    roles = []
    for role in getattr(member, "roles", []):
        if getattr(role, "is_default", lambda: False)():
            continue
        if str(getattr(role, "name", "")).startswith(SHOP_COLOR_ROLE_PREFIX):
            continue
        colour = getattr(role, "colour", getattr(role, "color", None))
        if getattr(colour, "value", 0):
            roles.append(role)
    return max(roles, key=lambda role: role.position, default=None)


async def _shop_color_ceiling(guild, bot_member):
    """Highest allowed shop-role position; keep owner/Sharkmeister blue above it."""
    ceiling = bot_member.top_role.position - 1
    shark_id = os.getenv(
        "SHARKMEISTER_USER_ID", SHARKMEISTER_DEFAULT_USER_ID
    ).strip() or SHARKMEISTER_DEFAULT_USER_ID

    shark_member = None
    try:
        shark_member = guild.get_member(int(shark_id))
    except Exception:
        shark_member = None

    if shark_member is None and str(getattr(guild, "owner_id", "")) == str(shark_id):
        shark_member = getattr(guild, "owner", None)

    if shark_member is not None:
        shark_color_role = _highest_nonshop_colored_role(shark_member)
        if shark_color_role is not None and shark_color_role < bot_member.top_role:
            ceiling = min(ceiling, shark_color_role.position - 1)

    return max(1, ceiling)


async def _position_shop_color_role(guild, bot_member, role, member):
    """Move the chosen shop color above the member's normal color, below owner blue."""
    base_role = _highest_nonshop_colored_role(member)
    ceiling = await _shop_color_ceiling(guild, bot_member)

    desired = role.position
    if base_role is not None:
        desired = max(desired, base_role.position + 1)

    if desired > ceiling:
        if base_role is not None and base_role.position >= ceiling:
            raise RuntimeError(
                "The bot cannot place this shop color above the member's current colored role "
                "without overriding a protected owner/bot role."
            )
        desired = ceiling

    # Also repair a shop role that somehow ended up above the protected ceiling.
    if role.position != desired:
        roles = await guild.edit_role_positions(
            positions={role: desired},
            reason="Puzzle Shop color display priority",
        )
        role = next((item for item in roles if item.id == role.id), role)

    return role


async def apply_shop_color_role(member, color_name):
    guild = getattr(member, "guild", None)
    if guild is None:
        raise RuntimeError("Name colors can only be equipped inside the Discord server.")

    bot_member = guild.me
    if bot_member is None or not bot_member.guild_permissions.manage_roles:
        raise RuntimeError("The bot needs Manage Roles to equip shop colors.")

    shop_roles = [role for role in member.roles if role.name.startswith(SHOP_COLOR_ROLE_PREFIX)]
    if shop_roles:
        blocked = [role for role in shop_roles if not role < bot_member.top_role]
        if blocked:
            raise RuntimeError(
                "A shop-color role is at or above the bot role. Move the bot role above all Shop Color roles first."
            )
        await member.remove_roles(*shop_roles, reason="Puzzle Shop color change")

    color_name = str(color_name or "").casefold()
    if not color_name:
        return None

    config = NAME_COLORS[color_name]
    role_name = SHOP_COLOR_ROLE_PREFIX + config["label"]
    role = discord.utils.get(guild.roles, name=role_name)

    if role is None:
        role = await guild.create_role(
            name=role_name,
            color=discord.Color(config["discord_color"]),
            reason="Puzzle Shop cosmetic color",
        )

    if role >= bot_member.top_role:
        raise RuntimeError("The shop color role is above the bot role in the role hierarchy.")

    role = await _position_shop_color_role(guild, bot_member, role, member)
    await member.add_roles(role, reason="Puzzle Shop color equipped")
    return role



async def equip_user_color(message, color_name):
    color_name = str(color_name or "").casefold()
    profile = await asyncio.to_thread(
        get_cosmetic_profile,
        message.author.id,
        message.author.display_name,
    )
    if color_name and color_name not in profile.get("colors", []):
        raise ValueError("You do not own that color.")

    await apply_shop_color_role(message.author, color_name)
    return await asyncio.to_thread(
        equip_color,
        message.author.id,
        message.author.display_name,
        color_name,
        f"equip-color:{message.id}:{message.author.id}:{color_name or 'default'}",
    )


# =========================================================
# HELP
# =========================================================

def help_message():

    return """🧠 **Chess Puzzle Bot**

**Puzzles**
`rp` / `r` — Random Puzzle. `p` / `!practice` — rated Practice near your Puzzle Elo.
`!daily <move>` — Daily Puzzle. `!400`, `!2500`, etc. — exact-rating practice.
`!rush` — **5-minute Puzzle Rush**; score as many as possible (unranked).

**Rated Chess**
`!playbot [elo]` — play Stockfish at calibrated Elo (1320-3190); no Elo picks within ±200 of yours. Win **+3**, draw **+2**, loss **+0** shared points + coins.
`!play @name` — free player challenge. `!play @name 10` — both stake 10 coins; winner gets 20. `!accept` / `!decline`.
Play moves normally or use `!move e4`. `!resign` resigns. `!chessboard` shows the position.
`!stats` shows both **Puzzle Elo** and your separate **Chess Elo**.

**Points / Coins / Shop**
`!l` — Chess Elo, Puzzle Elo, then shared points. `!puzzlestreak` — best Puzzle streaks. `!coins` / `!bank` — points + shared coins.
`!donate <name> <coins|badge>` — donate coins or a badge. `!trade <name> <give> <receive>` — trade coins/badges.
`!me` / `!profile` — clickable inventory. `!profile <name>` — view another player. `!shop` — shop info.
`!box` — badge box (50). `!customboard` — 100 boards (100 each).
`!custompiece` — 97 piece sets (100 each). `!color` — name colors (500 each).

🔥 **Survival**
`!survival` / `!stopsurvival` — start/resume or pause. `!solo <team>` / `!coop <team>` — captain mode.
`!heart` — captain buys one lost heart (100 coins; max one bought heart per run; no revive).
Survival uses the captain's board/pieces. First solver **+1 coin**, helpers **+0.5 coin**; first attempts update Puzzle Elo.
`!slb` — Survival leaderboard. `!<team>` — run info.

`!info` / `!help` / `!i` — show this info.
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

            await check_puzzle_rush_expiry(
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
        ).startswith(("random_", "practice_"))
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
    practice_only = bool(puzzle.get("practice_only")) or puzzle_id.startswith("random_lichess_")
    rated_practice = bool(puzzle.get("rated_practice"))
    is_boss = bool(puzzle.get("boss", False))

    if rated_practice and str(message.author.id) != str(puzzle.get("practice_owner_id", "")):
        await message.channel.send(
            f"🔒 **This is {puzzle.get('practice_owner_name', 'someone else')}'s personal Practice puzzle.**"
        )
        return

    puzzle_label = (
        "♟️ Daily Puzzle"
        if is_daily
        else "🎯 Practice"
        if rated_practice
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

    # Serialize state changes so two people cannot both advance the shared
    # position at exactly the same time. A short recent-move guard mirrors the
    # Survival duplicate protection: when somebody submits the correct move just
    # after another user already advanced the position, it is ignored instead of
    # being counted as a wrong answer or an anti-spam penalty.
    late_correct_duplicate = False
    wrong_attempt_count = 0
    wrong_penalty_due = False

    async with data_lock:

        # Re-read the live index AFTER taking the lock. The value captured before
        # the lock may already be stale because another solver just moved.
        next_index = puzzle.get(
            "next_solution_index",
            0
        )

        if next_index >= len(all_moves):
            return

        move_was_first = (
            next_index == 0
        )

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

        # If the move is wrong for the CURRENT position, check whether it was the
        # exact correct move for a position another solver advanced in the last
        # few seconds. This is the race that used to punish a legitimate answer.
        if not correct:
            now_epoch = time.time()
            recent_moves = puzzle.setdefault(
                "recent_accepted_moves",
                []
            )

            kept_recent = []
            for item in recent_moves:
                if not isinstance(item, dict):
                    continue
                try:
                    age = now_epoch - float(item.get("accepted_at", 0))
                except Exception:
                    continue
                if 0 <= age <= 8.0:
                    kept_recent.append(item)

            puzzle["recent_accepted_moves"] = kept_recent[-4:]

            for item in reversed(puzzle["recent_accepted_moves"]):
                previous_fen = item.get("fen_before")
                previous_expected = item.get("expected")
                if not previous_fen or not isinstance(previous_expected, dict):
                    continue
                try:
                    previous_board = board_from_fen_safe(previous_fen)
                    if san_matches_move(
                        previous_board,
                        submitted,
                        previous_expected,
                    ):
                        late_correct_duplicate = True
                        break
                except Exception:
                    continue

        if late_correct_duplicate:
            # Do not record an attempt, reset a streak, or increment the spam
            # counter. The user really supplied the just-played correct move.
            pass
        else:
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
                wrong_counts = puzzle.setdefault(
                    "wrong_attempt_counts",
                    {}
                )
                try:
                    previous_wrong_count = int(
                        wrong_counts.get(user_id, 0)
                    )
                except Exception:
                    previous_wrong_count = 0

                wrong_attempt_count = previous_wrong_count + 1
                wrong_counts[user_id] = wrong_attempt_count

                # Practice-only exact-rating puzzles never affect shared points.
                wrong_penalty_due = (
                    not practice_only
                    and wrong_attempt_count % 2 == 0
                )
            else:
                # Remember enough of the pre-move position to recognise a second
                # user's same correct SAN/UCI after the shared board advances.
                recent_moves = puzzle.setdefault(
                    "recent_accepted_moves",
                    []
                )
                now_epoch = time.time()
                recent_moves.append(
                    {
                        "accepted_at": now_epoch,
                        "fen_before": board.fen(),
                        "expected": dict(expected),
                    }
                )
                puzzle["recent_accepted_moves"] = [
                    item
                    for item in recent_moves
                    if isinstance(item, dict)
                    and 0 <= now_epoch - float(item.get("accepted_at", 0)) <= 8.0
                ][-4:]

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

    if late_correct_duplicate:
        await save_all()
        await message.channel.send(
            f"⏱️ **Correct move, {message.author.display_name} — "
            "someone else played it just before you. No wrong answer or penalty.**"
        )
        return

    personal_result = await record_official_puzzle_result(
        puzzle,
        message.author,
        correct,
    )

    if personal_result and personal_result.get("recorded"):
        if personal_result.get("streak_bonus") and not rated_practice:
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
        penalty_text = ""

        if wrong_penalty_due:
            penalty_number = wrong_attempt_count // 2
            try:
                new_total = await asyncio.to_thread(
                    shared_adjust_points,
                    message.author.id,
                    message.author.display_name,
                    -1.0,
                    (
                        f"puzzle-wrong-penalty:{puzzle_id}:"
                        f"{message.author.id}:{penalty_number}"
                    ),
                    source="puzzle-wrong-penalty",
                )
                penalty_text = (
                    "\n⚠️ **2 wrong attempts on this puzzle: -1 point.** "
                    f"You now have **{shared_format_points(new_total)} points**."
                )
            except Exception as error:
                print(
                    f"Puzzle wrong-answer penalty error for "
                    f"{message.author.display_name}: {error}",
                    flush=True,
                )

        await save_all()
        await message.channel.send(
            wrong_message(message.author) + penalty_text
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

        practice_only = bool(puzzle.get("practice_only")) or str(
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
            if rated_practice:
                updated_stats = (personal_result or {}).get("stats", {})
                elo_now = int(round(float(updated_stats.get("elo", 1500))))
                score_message = (
                    f"✅ **Correct, {message.author.display_name}!**\n"
                    f"🎉 **Practice solved!**\n"
                    f"Puzzle Elo: **{elo_now}** — **no shared leaderboard points**."
                )
            else:
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
        "practice_",
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
            note_survival_stop_requested()
            return

        if command_lower in {"!coins", "!bank"}:
            try:
                points, coins = await asyncio.gather(
                    asyncio.to_thread(shared_get_score, message.author.id),
                    asyncio.to_thread(shared_get_coins, message.author.id),
                )
                await message.channel.send(
                    f"🏆 Points: **{shared_format_points(points)}**\n"
                    f"🪙 Coins: **{shared_format_points(coins)}**"
                )
            except Exception as error:
                await message.channel.send(
                    f"❌ **Could not read your bank:** `{str(error)[:700]}`"
                )
            return

        if command_lower == "!donate" or command_lower.startswith("!donate "):
            arg_text = content[len("!donate"):].strip()
            try:
                target_user_id, target_name, asset = await _parse_donation_args(message, arg_text)
            except ValueError as error:
                await message.channel.send(f"❌ **{error}**")
                return
            if str(target_user_id) == str(message.author.id):
                await message.channel.send("❌ You cannot donate to yourself.")
                return
            try:
                if asset["type"] == "coins":
                    result = await asyncio.to_thread(
                        transfer_coins,
                        message.author.id, message.author.display_name,
                        target_user_id, target_name, asset["amount"],
                        f"coin-donate:{message.id}:{message.author.id}:{target_user_id}",
                        source="puzzle-donation",
                    )
                    await message.channel.send(
                        f"🪙 **{message.author.display_name} donated "
                        f"{shared_format_points(asset['amount'])} coins to {target_name}.**\n"
                        f"Your coins: **{shared_format_points(result['sender_coins'])}**"
                    )
                else:
                    result = await asyncio.to_thread(
                        transfer_badge,
                        message.author.id, message.author.display_name,
                        target_user_id, target_name, asset["badge"],
                        f"badge-donate:{message.id}:{message.author.id}:{target_user_id}",
                        source="puzzle-badge-donation",
                    )
                    await message.channel.send(
                        f"🎁 **{message.author.display_name} donated {asset['badge']} to {target_name}.**"
                    )
            except ValueError as error:
                await message.channel.send(f"❌ **{error}**")
            except Exception as error:
                await message.channel.send(f"❌ Could not safely donate: `{str(error)[:700]}`")
            return

        if command_lower == "!trade" or command_lower.startswith("!trade "):
            arg_text = content[len("!trade"):].strip()
            try:
                target_user_id, target_name, offer, request = await _parse_trade_args(message, arg_text)
                pending = await asyncio.to_thread(
                    shared_propose_trade,
                    message.author.id, message.author.display_name,
                    target_user_id, target_name, offer, request,
                    f"trade-propose:{message.id}:{message.author.id}:{target_user_id}",
                )
            except ValueError as error:
                await message.channel.send(f"❌ **{error}**")
                return
            except Exception as error:
                await message.channel.send(f"❌ Could not safely create trade: `{str(error)[:700]}`")
                return
            await message.channel.send(
                f"🤝 **Trade offer for {target_name}**\n"
                f"{message.author.display_name} gives: **{shared_format_trade_asset(offer)}**\n"
                f"{message.author.display_name} receives: **{shared_format_trade_asset(request)}**\n"
                f"{target_name}: use `!accepttrade` or `!declinetrade`."
            )
            return

        if command_lower in {"!pendingtrade", "!pending trade"}:
            try:
                profile = await asyncio.to_thread(
                    get_cosmetic_profile, message.author.id, message.author.display_name
                )
                await message.channel.send(pending_trade_message(profile))
            except Exception as error:
                await message.channel.send(f"❌ Could not read pending trade: `{str(error)[:700]}`")
            return

        if command_lower in {"!accepttrade", "!accept trade"}:
            try:
                details = await asyncio.to_thread(
                    shared_accept_trade, message.author.id, message.author.display_name,
                    f"trade-accept:{message.id}:{message.author.id}",
                )
            except ValueError as error:
                await message.channel.send(f"❌ **{error}**")
                return
            except Exception as error:
                await message.channel.send(f"❌ Could not safely accept trade: `{str(error)[:700]}`")
                return
            await message.channel.send(
                f"✅ **Trade accepted!**\n"
                f"{message.author.display_name} received **{shared_format_trade_asset(details['offer'])}**.\n"
                f"{details.get('from_name', 'Other player')} received **{shared_format_trade_asset(details['request'])}**."
            )
            return

        if command_lower in {"!declinetrade", "!decline trade"}:
            try:
                pending = await asyncio.to_thread(
                    shared_decline_trade, message.author.id, message.author.display_name,
                    f"trade-decline:{message.id}:{message.author.id}",
                )
            except ValueError as error:
                await message.channel.send(f"❌ **{error}**")
                return
            except Exception as error:
                await message.channel.send(f"❌ Could not safely decline trade: `{str(error)[:700]}`")
                return
            await message.channel.send(
                f"❌ **Trade declined.** Offer from {pending.get('from_name', 'Unknown')} was removed."
            )
            return

        # -----------------------------------------------------
        # RATED NORMAL CHESS
        # -----------------------------------------------------
        if (
            command_lower == "!playbot"
            or command_lower.startswith("!playbot ")
            or command_lower == "!play bot"
            or command_lower.startswith("!play bot ")
        ):
            await settle_recent_survival_stop()
            survival_active, survival_team = remote_survival_status()
            if survival_guard_active() or survival_active:
                await message.channel.send(
                    f"⚠️ **Survival Mode is active{f' for {survival_team}' if survival_team else ''}.** "
                    "Pause it before starting a rated chess game."
                )
                return

            if command_lower.startswith("!playbot"):
                rest = content[len("!playbot"):].strip()
            else:
                rest = content[len("!play bot"):].strip()

            requested_rating = None
            if rest:
                try:
                    requested_rating = clamp_bot_rating(float(rest))
                except Exception:
                    await message.channel.send(
                        f"❌ Bot Elo must be between **{BOT_MIN_ELO}** and **{BOT_MAX_ELO}**. "
                        "Example: `!playbot 1500`."
                    )
                    return
            await start_bot_game(message, requested_rating)
            return

        if command_lower == "!play" or (
            command_lower.startswith("!play ")
            and not (
                command_lower == "!play bot"
                or command_lower.startswith("!play bot ")
            )
        ):
            target_text = content[len("!play"):].strip()
            if not target_text:
                await message.channel.send(
                    "❌ Usage: `!play @name` for a free game, `!play @name 10` for a 10-coin wager, or `!playbot` to play the bot."
                )
                return
            await settle_recent_survival_stop()
            survival_active, survival_team = remote_survival_status()
            if survival_guard_active() or survival_active:
                await message.channel.send(
                    "⚠️ Pause Survival before starting a rated player-vs-player game."
                )
                return
            try:
                target, wager_amount = await resolve_chess_challenge_target_and_wager(message, target_text)
            except ValueError as error:
                await message.channel.send(f"❌ **{error}**")
                return
            if target is None:
                await message.channel.send(
                    "❌ Player not found. Use `!play @name` for a free game or "
                    "`!play @name 10` to wager 10 coins each."
                )
                return
            await create_player_challenge(message, target, wager_amount)
            return

        if command_lower == "!accept":
            await settle_recent_survival_stop()
            survival_active, _survival_team = remote_survival_status()
            if survival_guard_active() or survival_active:
                await message.channel.send("⚠️ Pause Survival before accepting a chess challenge.")
                return
            await accept_player_challenge(message)
            return

        if command_lower == "!decline":
            challenge = _chess_challenges_state().pop(str(message.author.id), None)
            if challenge is None:
                await message.channel.send("❌ You do not have a pending chess challenge.")
            else:
                await save_all()
                await message.channel.send(
                    f"❌ **{message.author.display_name} declined the chess challenge.**"
                )
            return

        if command_lower == "!resign":
            await resign_chess_game(message)
            return

        if command_lower in {"!chessboard", "!board"}:
            game = _active_chess_game_for_user(message.author.id)
            if not game:
                await message.channel.send("❌ You do not have an active rated chess game.")
            else:
                await send_chess_game_position(message.channel, game)
            return

        if command_lower == "!chessstats" or command_lower.startswith("!chessstats "):
            if message.mentions:
                target = message.mentions[0]
                target_id = target.id
                target_name = target.display_name
            else:
                typed = content[len("!chessstats"):].strip()
                if typed:
                    target = await resolve_server_member(message, typed)
                    if target is None:
                        await message.channel.send("❌ Player not found. Mention them or use their server name.")
                        return
                    target_id = target.id
                    target_name = target.display_name
                else:
                    target_id = message.author.id
                    target_name = message.author.display_name
            await message.channel.send(
                f"♜ **Chess Profile — {target_name}**\n"
                + format_chess_profile_line(target_id, target_name)
            )
            return

        # -----------------------------------------------------
        # FIVE-MINUTE PUZZLE RUSH
        # -----------------------------------------------------
        if command_lower in {"!rush", "!puzzlerush", "!puzzle rush"}:
            await settle_recent_survival_stop()
            survival_active, _survival_team = remote_survival_status()
            if survival_guard_active() or survival_active:
                await message.channel.send("⚠️ Pause Survival before starting Puzzle Rush.")
                return
            await start_puzzle_rush(message)
            return

        if command_lower in {"!rush stop", "!puzzlerush stop", "!puzzle rush stop"}:
            if not _active_rush_for_user(message.author.id):
                await message.channel.send("❌ You do not have an active Puzzle Rush.")
            else:
                await end_puzzle_rush(message.channel, message.author.id, "Stopped early.")
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

        # Sharkmeister-only coin wallet repair:
        # !editcoins <name> <new amount>
        if command_lower == "!editcoins" or command_lower.startswith("!editcoins "):
            sharkmeister_user_id = os.getenv(
                "SHARKMEISTER_USER_ID", SHARKMEISTER_DEFAULT_USER_ID
            ).strip() or SHARKMEISTER_DEFAULT_USER_ID

            if str(message.author.id) != sharkmeister_user_id:
                await message.channel.send(
                    "❌ Only **Sharkmeister** can edit coin balances."
                )
                return

            parts = content.split()
            if len(parts) < 3:
                await message.channel.send(
                    "❌ Usage: `!editcoins <name> <coins>`"
                )
                return

            coins_text = parts[-1]
            typed_name = " ".join(parts[1:-1]).strip()
            try:
                target_coins = float(coins_text)
                if target_coins < 0:
                    raise ValueError("negative")
                if target_coins.is_integer():
                    target_coins = int(target_coins)
            except Exception:
                await message.channel.send(
                    "❌ Coins must be a non-negative number, for example `200` or `57.5`."
                )
                return

            if message.mentions:
                target_member = message.mentions[0]
                name = target_member.display_name
                target_user_id = target_member.id
            else:
                name = typed_name
                target_user_id = (
                    SHARKMEISTER_DEFAULT_USER_ID
                    if name.casefold() == "sharkmeister"
                    else None
                )

            try:
                new_coins = await asyncio.to_thread(
                    shared_admin_set_coins,
                    name,
                    target_coins,
                    f"admin-editcoins:{message.id}:{str(target_user_id or name).casefold()}:{target_coins}",
                    target_user_id=target_user_id,
                )
            except Exception as error:
                await message.channel.send(
                    f"❌ Could not edit coins: `{str(error)[:900]}`"
                )
                return

            await message.channel.send(
                f"✅ **{name}** now has **{shared_format_points(new_coins)} coins**. "
                "Their leaderboard points were not changed."
            )
            return

        # Sharkmeister-only active color repair:
        # !editcolor <name> <default|shop color>
        if command_lower == "!editcolor" or command_lower.startswith("!editcolor "):
            sharkmeister_user_id = os.getenv(
                "SHARKMEISTER_USER_ID", SHARKMEISTER_DEFAULT_USER_ID
            ).strip() or SHARKMEISTER_DEFAULT_USER_ID

            if str(message.author.id) != sharkmeister_user_id:
                await message.channel.send(
                    "❌ Only **Sharkmeister** can edit name colors."
                )
                return

            parts = content.split()
            if len(parts) < 3:
                await message.channel.send(
                    "❌ Usage: `!editcolor <name> <default|red|yellow|orange|green|purple|cyan|gold|gray>`"
                )
                return

            requested_color = parts[-1].casefold()
            if requested_color == "default":
                color_name = ""
            elif requested_color in NAME_COLORS:
                color_name = requested_color
            else:
                await message.channel.send(
                    "❌ Color must be `default` or one of: `" + "`, `".join(NAME_COLORS) + "`."
                )
                return

            typed_name = " ".join(parts[1:-1]).strip()
            if message.mentions:
                target_member = message.mentions[0]
                name = target_member.display_name
                target_user_id = target_member.id
            else:
                name = typed_name
                target_user_id = (
                    SHARKMEISTER_DEFAULT_USER_ID
                    if name.casefold() == "sharkmeister"
                    else None
                )
                try:
                    target_profile = await asyncio.to_thread(
                        shared_resolve_cosmetic_profile,
                        name,
                        target_user_id=target_user_id,
                    )
                except Exception as error:
                    await message.channel.send(
                        f"❌ Could not find that player: `{str(error)[:800]}`"
                    )
                    return

                target_user_id = target_profile["user_id"]
                target_member = message.guild.get_member(int(target_user_id))
                if target_member is None:
                    try:
                        target_member = await message.guild.fetch_member(int(target_user_id))
                    except Exception:
                        target_member = None

            if target_member is None:
                await message.channel.send(
                    "❌ That player exists in the wallet, but I could not find them as a current server member."
                )
                return

            # Apply the Discord role first. If the ledger write fails, restore the old visible role.
            previous_profile = await asyncio.to_thread(
                get_cosmetic_profile, target_member.id, target_member.display_name
            )
            previous_color = str(previous_profile.get("active_color", "") or "")

            try:
                await apply_shop_color_role(target_member, color_name)
                try:
                    profile = await asyncio.to_thread(
                        shared_admin_set_color,
                        target_member.display_name,
                        color_name,
                        f"admin-editcolor:{message.id}:{target_member.id}:{color_name or 'default'}",
                        target_user_id=target_member.id,
                    )
                except Exception:
                    try:
                        await apply_shop_color_role(target_member, previous_color)
                    except Exception:
                        pass
                    raise
            except Exception as error:
                await message.channel.send(
                    f"❌ Could not edit color: `{str(error)[:900]}`"
                )
                return

            label = NAME_COLORS[color_name]["label"] if color_name else "Default"
            grant_note = ""
            if color_name and color_name not in previous_profile.get("colors", []):
                grant_note = " The color was also added to their owned colors."
            await message.channel.send(
                f"✅ **{profile.get('name', target_member.display_name)}** is now using **{label}**.{grant_note}"
            )
            return

        # -----------------------------------------------------
        # SHOP / COSMETICS
        # -----------------------------------------------------
        if command_lower in {"!shop", "!shop box", "!box"}:
            if command_lower in {"!shop box", "!box"}:
                try:
                    result = await asyncio.to_thread(
                        buy_badge_box,
                        message.author.id,
                        message.author.display_name,
                        f"badge-box:{message.id}:{message.author.id}",
                    )
                    await message.channel.send(
                        f"🎁 **Mystery Badge Box opened!**\n"
                        f"You got {result['badge']} — **{result['rarity_label']}**.\n"
                        f"🪙 Coins left: **{shared_format_points(result['coins'])}**\n"
                        "Use `!profile` to see/equip your badges."
                    )
                except Exception as error:
                    await message.channel.send(f"❌ **Could not open box:** {str(error)[:800]}")
                return

            try:
                text = await asyncio.to_thread(
                    shop_message,
                    message.author.id,
                    message.author.display_name,
                )
            except Exception as error:
                text = f"❌ **Shop unavailable:** `{str(error)[:800]}`"
            await message.channel.send(text)
            return

        if command_lower == "!customboard" or command_lower.startswith("!customboard "):
            args = content.split()[1:]
            if not args:
                try:
                    await send_cosmetic_catalog_preview(message, "board", 1)
                except Exception as error:
                    await message.channel.send(f"❌ Could not open board previews: `{str(error)[:800]}`")
                return

            if len(args) == 1 and args[0].isdigit():
                page = int(args[0])
                try:
                    await send_cosmetic_catalog_preview(message, "board", page)
                except Exception as error:
                    await message.channel.send(f"❌ Could not open board previews: `{str(error)[:800]}`")
                return

            board_name = args[0].casefold()
            if board_name == "default":
                board_name = "classic"

            if board_name not in BOARD_THEMES:
                await message.channel.send("❌ Unknown board theme. Use `!customboard` for the catalogue.")
                return

            action = args[1].casefold() if len(args) > 1 else "equip"

            if action == "test":
                try:
                    profile = await asyncio.to_thread(
                        get_cosmetic_profile, message.author.id, message.author.display_name
                    )
                    preview = await asyncio.to_thread(
                        make_cosmetic_preview_file,
                        board_name,
                        profile.get("active_piece", "classic"),
                        "board_theme_preview.png",
                    )
                    await message.channel.send(
                        f"🎨 **{BOARD_DISPLAY_NAMES[board_name]} preview** • Pieces: "
                        f"**{PIECE_DISPLAY_NAMES.get(profile.get('active_piece', 'classic'), 'Classic')}**",
                        file=preview,
                    )
                except Exception as error:
                    await message.channel.send(f"❌ Could not render preview: `{str(error)[:800]}`")
                return

            if action == "buy":
                if board_name == "classic":
                    await message.channel.send("✅ **Classic is the free default board.**")
                    return
                try:
                    profile = await asyncio.to_thread(
                        buy_board,
                        message.author.id,
                        message.author.display_name,
                        board_name,
                        f"buy-board:{message.id}:{message.author.id}:{board_name}",
                    )
                    await message.channel.send(
                        f"✅ Bought **{BOARD_DISPLAY_NAMES[board_name]}** for "
                        f"**{shared_format_points(BOARD_COST)} coins**.\n"
                        f"🪙 Coins left: **{shared_format_points(profile['coins'])}**\n"
                        f"Equip it with `!customboard {board_name}`."
                    )
                except Exception as error:
                    await message.channel.send(f"❌ **Could not buy board:** {str(error)[:800]}")
                return

            try:
                profile = await asyncio.to_thread(
                    equip_board,
                    message.author.id,
                    message.author.display_name,
                    board_name,
                    f"equip-board:{message.id}:{message.author.id}:{board_name}",
                )
                await message.channel.send(
                    f"🎨 **Board equipped:** {BOARD_DISPLAY_NAMES[profile['active_board']]}"
                )
            except Exception as error:
                await message.channel.send(f"❌ **Could not equip board:** {str(error)[:800]}")
            return

        if command_lower == "!custompiece" or command_lower.startswith("!custompiece "):
            args = content.split()[1:]
            if not args:
                try:
                    await send_cosmetic_catalog_preview(message, "piece", 1)
                except Exception as error:
                    await message.channel.send(f"❌ Could not open piece previews: `{str(error)[:800]}`")
                return

            if len(args) == 1 and args[0].isdigit():
                page = int(args[0])
                try:
                    await send_cosmetic_catalog_preview(message, "piece", page)
                except Exception as error:
                    await message.channel.send(f"❌ Could not open piece previews: `{str(error)[:800]}`")
                return

            piece_name = args[0].casefold()
            if piece_name == "default":
                piece_name = "classic"

            if piece_name not in PIECE_SETS:
                await message.channel.send("❌ Unknown piece set. Use `!custompiece` for the catalogue.")
                return

            action = args[1].casefold() if len(args) > 1 else "equip"

            if action == "test":
                try:
                    profile = await asyncio.to_thread(
                        get_cosmetic_profile, message.author.id, message.author.display_name
                    )
                    preview = await asyncio.to_thread(
                        make_cosmetic_preview_file,
                        profile.get("active_board", "classic"),
                        piece_name,
                        "piece_set_preview.png",
                    )
                    await message.channel.send(
                        f"♟️ **{PIECE_DISPLAY_NAMES[piece_name]} preview** • Board: "
                        f"**{BOARD_DISPLAY_NAMES.get(profile.get('active_board', 'classic'), 'Classic')}**",
                        file=preview,
                    )
                except Exception as error:
                    await message.channel.send(f"❌ Could not render piece preview: `{str(error)[:800]}`")
                return

            if action == "buy":
                if piece_name == "classic":
                    await message.channel.send("✅ **Classic pieces are free by default.**")
                    return
                try:
                    profile = await asyncio.to_thread(
                        buy_piece,
                        message.author.id,
                        message.author.display_name,
                        piece_name,
                        f"buy-piece:{message.id}:{message.author.id}:{piece_name}",
                    )
                    await message.channel.send(
                        f"✅ Bought **{PIECE_DISPLAY_NAMES[piece_name]}** for "
                        f"**{shared_format_points(PIECE_COST)} coins**.\n"
                        f"🪙 Coins left: **{shared_format_points(profile['coins'])}**\n"
                        f"Equip it with `!custompiece {piece_name}`."
                    )
                except Exception as error:
                    await message.channel.send(f"❌ **Could not buy piece set:** {str(error)[:800]}")
                return

            try:
                profile = await asyncio.to_thread(
                    equip_piece,
                    message.author.id,
                    message.author.display_name,
                    piece_name,
                    f"equip-piece:{message.id}:{message.author.id}:{piece_name}",
                )
                await message.channel.send(
                    f"♟️ **Piece set equipped:** {PIECE_DISPLAY_NAMES[profile['active_piece']]}"
                )
            except Exception as error:
                await message.channel.send(f"❌ **Could not equip piece set:** {str(error)[:800]}")
            return

        if command_lower == "!color" or command_lower.startswith("!color "):
            args = content.split()[1:]
            if not args:
                await message.channel.send(color_catalog_message())
                return

            color_name = args[0].casefold()
            if color_name == "default":
                color_name = ""
            elif color_name not in NAME_COLORS:
                await message.channel.send("❌ Unknown color. Use `!color` to see all available colors.")
                return

            action = args[1].casefold() if len(args) > 1 else "equip"
            if action == "buy":
                if not color_name:
                    await message.channel.send("✅ Default color is free.")
                    return
                try:
                    profile = await asyncio.to_thread(
                        buy_color,
                        message.author.id,
                        message.author.display_name,
                        color_name,
                        f"buy-color:{message.id}:{message.author.id}:{color_name}",
                    )
                    await message.channel.send(
                        f"✅ Bought **{NAME_COLORS[color_name]['label']}** for "
                        f"**{shared_format_points(COLOR_COST)} coins**.\n"
                        f"🪙 Coins left: **{shared_format_points(profile['coins'])}**\n"
                        f"Equip it with `!color {color_name}`."
                    )
                except Exception as error:
                    await message.channel.send(f"❌ **Could not buy color:** {str(error)[:800]}")
                return

            try:
                await equip_user_color(message, color_name)
                label = NAME_COLORS[color_name]["label"] if color_name else "Default"
                await message.channel.send(f"🖌️ **Name color equipped:** {label}")
            except Exception as error:
                await message.channel.send(f"❌ **Could not equip color:** {str(error)[:800]}")
            return

        # !me / !profile are a compact customization dashboard. Large inventories
        # are opened by category/page so they never flood Discord's message limit.
        if command_lower in {"!me", "!profile"}:
            try:
                text = await asyncio.to_thread(
                    cosmetic_profile_dashboard,
                    message.author.id,
                    message.author.display_name,
                )
                view = CosmeticProfileView(
                    message.author.id, message.author.id, message.author.display_name, editable=True
                )
                await message.channel.send(text, view=view)
            except Exception as error:
                await message.channel.send(f"❌ **Profile unavailable:** `{str(error)[:800]}`")
            return

        if command_lower.startswith("!profile ") or command_lower.startswith("!me "):
            prefix = "!profile" if command_lower.startswith("!profile ") else "!me"
            args = content[len(prefix):].strip().split()
            if not args:
                return
            kind = args[0].casefold()

            # Collection browsing: no giant owned-badge wall.
            try:
                if kind == "badges":
                    if len(args) == 1:
                        text = await asyncio.to_thread(
                            cosmetic_badge_overview,
                            message.author.id,
                            message.author.display_name,
                        )
                    else:
                        rarity = args[1].casefold()
                        page = int(args[2]) if len(args) > 2 and args[2].isdigit() else 1
                        text = await asyncio.to_thread(
                            cosmetic_badge_page,
                            message.author.id,
                            message.author.display_name,
                            rarity,
                            page,
                        )
                    view = CosmeticProfileView(message.author.id, message.author.id, message.author.display_name, editable=True)
                    await message.channel.send(text, view=view)
                    return

                if kind == "boards":
                    page = int(args[1]) if len(args) > 1 and args[1].isdigit() else 1
                    text = await asyncio.to_thread(
                        cosmetic_board_page,
                        message.author.id,
                        message.author.display_name,
                        page,
                    )
                    await message.channel.send(text, view=CosmeticProfileView(message.author.id, message.author.id, message.author.display_name, editable=True))
                    return

                if kind in {"pieces", "piecesets"}:
                    page = int(args[1]) if len(args) > 1 and args[1].isdigit() else 1
                    text = await asyncio.to_thread(
                        cosmetic_piece_page,
                        message.author.id,
                        message.author.display_name,
                        page,
                    )
                    await message.channel.send(text, view=CosmeticProfileView(message.author.id, message.author.id, message.author.display_name, editable=True))
                    return

                if kind == "colors":
                    text = await asyncio.to_thread(
                        cosmetic_color_page,
                        message.author.id,
                        message.author.display_name,
                    )
                    view = CosmeticProfileView(message.author.id, message.author.id, message.author.display_name, editable=True)
                    await message.channel.send(text, view=view)
                    return

                # `!profile <name>` opens another player's read-only cosmetic profile.
                if prefix == "!profile" and len(args) >= 1 and kind not in {"badge", "board", "piece", "pieceset", "badges", "boards", "pieces", "piecesets", "colors", "color"}:
                    requested_name = content[len("!profile"):].strip()
                    target_id, target_name = await resolve_cosmetic_profile_target(message, requested_name)
                    text = await asyncio.to_thread(cosmetic_profile_dashboard, target_id, target_name)
                    view = CosmeticProfileView(message.author.id, target_id, target_name, editable=(str(target_id) == str(message.author.id)))
                    await message.channel.send(text, view=view)
                    return

                if len(args) < 2:
                    raise ValueError(
                        "Use `!me badges`, `!me boards`, `!me pieces`, or an equip command."
                    )

                value = args[1]
                if kind == "badge":
                    profile = await asyncio.to_thread(
                        get_cosmetic_profile,
                        message.author.id,
                        message.author.display_name,
                    )
                    badges = list(profile.get("badges", []))
                    try:
                        index = int(value)
                    except ValueError:
                        index = -1
                    if index == 0:
                        await asyncio.to_thread(
                            equip_badge,
                            message.author.id,
                            message.author.display_name,
                            "",
                            f"equip-badge:{message.id}:{message.author.id}:0",
                        )
                        await message.channel.send("🏅 **Badge unequipped.**")
                        return
                    if not (1 <= index <= len(badges)):
                        raise ValueError("Badge number not found. Use `!me badges <rarity>` to see badge numbers, or `!profile badge 0` for no badge.")
                    badge = badges[index - 1]
                    await asyncio.to_thread(
                        equip_badge,
                        message.author.id,
                        message.author.display_name,
                        badge,
                        f"equip-badge:{message.id}:{message.author.id}:{index}",
                    )
                    await message.channel.send(f"🏅 **Badge equipped:** {badge}")
                    return

                if kind == "board":
                    board_name = value.casefold()
                    if board_name == "default":
                        board_name = "classic"
                    profile = await asyncio.to_thread(
                        equip_board,
                        message.author.id,
                        message.author.display_name,
                        board_name,
                        f"equip-board:{message.id}:{message.author.id}:{board_name}",
                    )
                    await message.channel.send(
                        f"🎨 **Board equipped:** {BOARD_DISPLAY_NAMES[profile['active_board']]}"
                    )
                    return

                if kind in {"piece", "pieceset"}:
                    piece_name = value.casefold()
                    if piece_name == "default":
                        piece_name = "classic"
                    profile = await asyncio.to_thread(
                        equip_piece,
                        message.author.id,
                        message.author.display_name,
                        piece_name,
                        f"equip-piece:{message.id}:{message.author.id}:{piece_name}",
                    )
                    await message.channel.send(
                        f"♟️ **Piece set equipped:** {PIECE_DISPLAY_NAMES[profile['active_piece']]}"
                    )
                    return

                if kind == "color":
                    color_name = value.casefold()
                    if color_name == "default":
                        color_name = ""
                    await equip_user_color(message, color_name)
                    label = NAME_COLORS[color_name]["label"] if color_name else "Default"
                    await message.channel.send(f"🖌️ **Name color equipped:** {label}")
                    return

                raise ValueError("Unknown profile setting.")
            except Exception as error:
                await message.channel.send(f"❌ **Could not update profile:** {str(error)[:800]}")
            return

        # !stats stays purely statistical; !stats <name> inspects another user.
        if command_lower == "!stats" or command_lower.startswith("!stats "):
            if command_lower == "!stats":
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

            try:
                decorated = dict(puzzle_profile)
                decorated["name"] = (
                    await asyncio.to_thread(badge_prefix, puzzle_profile.get("user_id"))
                ) + str(puzzle_profile.get("name", "Unknown"))
            except Exception:
                decorated = puzzle_profile

            chess_line = format_chess_profile_line(
                puzzle_profile.get("user_id"),
                puzzle_profile.get("name", "Unknown"),
            )
            await message.channel.send(
                format_puzzle_stats(decorated)
                + "\n\n"
                + chess_line
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
                message.author,
            )
            return

        # Runtime build check. Safe for everyone; it changes no data.
        if command_lower in ("!v", "!version"):
            await message.channel.send(
                f"**Bot:** `{RP_BUILD}`\n"
                f"**Ledger:** `{SHARED_LEDGER_BUILD}`\n"
                f"**Puzzle Stats:** `{PUZZLE_STATS_BUILD}`\n"
                f"**Chess Play:** `{CHESS_PLAY_BUILD}`"
            )
            return

        # Fast exact aliases. Handle these before any puzzle logic.
        if command_lower in (
            "!leaderboard",
            "!lb",
            "!l"
        ):
            await message.channel.send(
                format_chess_elo_leaderboard(10)
            )
            try:
                puzzle_elo, _streaks = await asyncio.to_thread(
                    split_puzzle_leaderboards,
                    10,
                )
                await message.channel.send(
                    puzzle_elo
                    + "\n\n🔥 Use `!puzzlestreak` to see the **Best Puzzle Streaks** leaderboard."
                )
            except Exception as error:
                print(
                    f"Puzzle Elo leaderboard error: {error}",
                    flush=True,
                )
            await message.channel.send(
                make_leaderboard()
            )
            return

        if command_lower == "!puzzlestreak":
            try:
                _puzzle_elo, streaks = await asyncio.to_thread(
                    split_puzzle_leaderboards,
                    10,
                )
                await message.channel.send(streaks)
            except Exception as error:
                print(
                    f"Puzzle streak leaderboard error: {error}",
                    flush=True,
                )
                await message.channel.send(
                    "❌ Could not load the Puzzle streak leaderboard right now."
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
        # PERSONAL PRACTICE
        # =====================================================

        if command_lower in {"p", "!p", "!practice"}:
            await settle_recent_survival_stop()

            if survival_guard_active():
                await message.channel.send(
                    "⏳ **Survival is starting.** Practice is unavailable right now."
                )
                return

            if is_survival_active():
                team = active_team() or "another team"
                await message.channel.send(
                    f"⚠️ **Survival Mode is active for {team}.** "
                    "Practice is unavailable until Survival is paused."
                )
                return

            previous_random = state.get("latest_random_puzzle")
            if (
                previous_random
                and not previous_random.get("answer_posted", False)
                and not previous_random.get("solved", False)
            ):
                await finalize_expired_puzzle(
                    message.channel,
                    previous_random,
                    "random",
                )

            await post_practice_puzzle(message.channel, message.author)
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
            "r",
        ):

            await settle_recent_survival_stop()

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
                message.channel,
                message.author,
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

        # Active rated chess / Puzzle Rush owns this user's chess-like moves
        # before the shared Daily/Random puzzle parser sees them. Other users
        # in the channel are unaffected.
        active_game = _active_chess_game_for_user(message.author.id)
        if active_game is not None and chess_game_move_like(content):
            await handle_chess_game_move(message, active_game, content)
            return

        active_rush = _active_rush_for_user(message.author.id)
        if active_rush is not None and chess_game_move_like(content):
            await handle_rush_move(message, active_rush, content)
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
    state.setdefault("chess_ratings", {})
    state.setdefault("chess_games", {})
    state.setdefault("chess_challenges", {})
    state.setdefault("puzzle_rush", {})
    state.setdefault("puzzle_rush_bests", {})

    if recover_chess_ratings_from_game_history():
        print("Recovered missing Chess Elo entries from finished game history.", flush=True)
        await save_all_critical()

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
