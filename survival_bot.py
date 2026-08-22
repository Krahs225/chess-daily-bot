import asyncio
import io
import json
import os
import random
import re
import subprocess
import time
import traceback
import copy
from datetime import datetime, timezone
from pathlib import Path

import chess
import chess.svg
import requests
import discord
from discord.ext import commands

from puzzle_mode_lock import (
    active_team,
    clear_lock,
    get_lock,
    is_survival_active,
    write_lock,
)

# Shared individual leaderboard. This is intentionally NOT the
# Survival team leaderboard.
from shared_leaderboard import (
    add_points,
    format_points,
    get_score,
    personal_ranking,
)


TOKEN = os.getenv(
    "DISCORD_TOKEN"
)

CHANNEL_ID = 1468320170891022417

SURVIVAL_STATE_FILE = "survival_runs.json"

# The official Lichess puzzle collection is published as the
# CC0 Lichess/chess-puzzles dataset. The Hugging Face Dataset Viewer
# can filter that dataset server-side by the exact puzzle Rating, so
# Survival does not need an 871 MB download on every GitHub Action run.
HF_FILTER_URL = (
    "https://datasets-server.huggingface.co/filter"
)

HF_DATASET = "Lichess/chess-puzzles"
HF_CONFIG = "default"
HF_SPLIT = "train"

REQUEST_TIMEOUT = 20
BATCH_SIZE = 25

INACTIVITY_SECONDS = 10 * 60
PENDING_TEAM_SECONDS = 60

RUN_TIME = 5 * 60 * 60 + 50 * 60

THREE_STRIKES = 3
SHARK_ADMIN_NAME = "sharkmeister"


# Puzzle difficulty progression.
# Puzzle #81+ is 2600+ forever.
DIFFICULTY_BANDS = [
    (1, 10, 1200, 1400),
    (11, 20, 1400, 1550),
    (21, 30, 1550, 1700),
    (31, 40, 1700, 1850),
    (41, 50, 1850, 2050),
    (51, 60, 2050, 2250),
    (61, 70, 2250, 2400),
    (71, 80, 2400, 2600),
]


def normalize_team_name(
    value
):
    value = " ".join(
        str(value).strip().split()
    )

    return value.casefold()


def valid_team_name(
    value
):
    value = " ".join(
        str(value).strip().split()
    )

    if not (
        2 <= len(value) <= 32
    ):
        return False

    if "\n" in value or "\r" in value:
        return False

    if value.startswith("!"):
        return False

    return True


def utc_now():
    return datetime.now(
        timezone.utc
    )


def epoch_now():
    return time.time()


def load_json(
    filename,
    default,
):
    path = Path(filename)

    if not path.exists():
        return default

    try:
        data = json.loads(
            path.read_text(
                encoding="utf-8"
            )
        )
        return data
    except Exception:
        return default


def save_json(
    filename,
    data,
):
    path = Path(filename)
    temp = path.with_suffix(
        ".tmp"
    )

    temp.write_text(
        json.dumps(
            data,
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    temp.replace(
        path
    )


def git_run(
    args
):
    return subprocess.run(
        args,
        capture_output=True,
        text=True,
    )


def git_branch():
    return os.getenv(
        "GITHUB_REF_NAME",
        "main",
    )


def push_state_files():
    """
    Commit Survival state only.

    Important: individual point rewards are written by the shared
    leaderboard event ledger separately. Survival state is therefore
    committed before a point transaction is sent to the shared ledger.
    """
    branch = git_branch()

    for _ in range(8):

        git_run(
            [
                "git",
                "config",
                "user.name",
                "Survival Mode Bot",
            ]
        )

        git_run(
            [
                "git",
                "config",
                "user.email",
                "survival-mode-bot@users.noreply.github.com",
            ]
        )

        git_run(
            [
                "git",
                "add",
                SURVIVAL_STATE_FILE,
            ]
        )

        commit = git_run(
            [
                "git",
                "commit",
                "-m",
                "Update Survival Mode state",
            ]
        )

        if commit.returncode != 0:
            status = git_run(
                [
                    "git",
                    "status",
                    "--porcelain",
                    SURVIVAL_STATE_FILE,
                ]
            )

            if not status.stdout.strip():
                return True

            # The state file was changed but could not be committed.
            # Try again after rebasing onto the latest main.
            git_run(
                [
                    "git",
                    "pull",
                    "--rebase",
                    "origin",
                    branch,
                ]
            )

            time.sleep(0.5)
            continue

        push = git_run(
            [
                "git",
                "push",
                "origin",
                f"HEAD:{branch}",
            ]
        )

        if push.returncode == 0:
            return True

        git_run(
            [
                "git",
                "fetch",
                "origin",
                branch,
            ]
        )

        git_run(
            [
                "git",
                "reset",
                "--hard",
                f"origin/{branch}",
            ]
        )

        time.sleep(0.5)

    return False


def load_runs():
    data = load_json(
        SURVIVAL_STATE_FILE,
        {},
    )

    if not isinstance(data, dict):
        data = {}

    data.setdefault(
        "teams",
        {},
    )

    return data


def team_record(
    state,
    team_key,
    display_name,
):
    teams = state[
        "teams"
    ]

    team = teams.setdefault(
        team_key,
        {
            "name":
                display_name,
            "best_puzzle":
                0,
            "best_difficulty":
                0,
            "best_completed_at":
                None,
            "current":
                None,
            "history":
                [],
        },
    )

    team["name"] = display_name
    team.setdefault(
        "history",
        [],
    )

    return team


def update_best(
    team,
    run,
):
    if not run:
        return

    puzzle_number = int(
        run.get(
            "puzzle_number",
            0,
        )
    )

    difficulty = int(
        run.get(
            "best_difficulty",
            0,
        )
    )

    old_best = int(
        team.get(
            "best_puzzle",
            0,
        )
    )

    if (
        puzzle_number
        > old_best
    ):
        team[
            "best_puzzle"
        ] = puzzle_number

        team[
            "best_completed_at"
        ] = (
            utc_now().isoformat()
        )

    team[
        "best_difficulty"
    ] = max(
        int(
            team.get(
                "best_difficulty",
                0,
            )
        ),
        difficulty,
    )


def save_state(
    state,
    push=True,
):
    save_json(
        SURVIVAL_STATE_FILE,
        state,
    )

    if push:
        ok = push_state_files()

        if not ok:
            raise RuntimeError(
                "Could not save Survival state to GitHub."
            )


def difficulty_target(
    puzzle_number
):
    for (
        first,
        last,
        minimum,
        maximum,
    ) in DIFFICULTY_BANDS:

        if (
            first
            <= puzzle_number
            <= last
        ):
            return (
                minimum,
                maximum,
            )

    return (
        2600,
        9999,
    )


def sanitize_puzzle(
    item
):
    """
    Accept both the documented current batch response shape:
    { "puzzle": {...}, "game": {...} }
    and a direct puzzle object.
    """
    if not isinstance(
        item,
        dict,
    ):
        return None

    puzzle = item.get(
        "puzzle",
        item,
    )

    if not isinstance(
        puzzle,
        dict,
    ):
        return None

    puzzle_id = puzzle.get(
        "id"
    )

    fen = puzzle.get(
        "fen"
    )

    moves = puzzle.get(
        "line"
    ) or puzzle.get(
        "moves"
    )

    rating = puzzle.get(
        "rating"
    )

    if not puzzle_id:
        return None

    if not fen:
        return None

    if not moves:
        return None

    if rating is None:
        return None

    if isinstance(
        moves,
        str,
    ):
        moves = moves.split()

    if not isinstance(
        moves,
        list,
    ) or not moves:
        return None

    try:
        rating = int(
            rating
        )
    except Exception:
        return None

    return {
        "id":
            str(puzzle_id),
        "fen":
            str(fen),
        "moves":
            [
                str(move)
                for move in moves
            ],
        "rating":
            rating,
        "themes":
            puzzle.get(
                "themes",
                "",
            ),
        "url":
            puzzle.get(
                "url",
            ),
    }


def fetch_lichess_batch(
    minimum_rating,
    maximum_rating,
):
    """
    Fetch a random slice of Lichess puzzles from the official
    Lichess/chess-puzzles dataset through the public Dataset Viewer.

    The Viewer supports server-side comparison predicates on Rating,
    which lets Survival request exactly the desired difficulty band.
    """
    where = (
        f'"Rating">={int(minimum_rating)} '
        f'AND "Rating"<={int(maximum_rating)}'
    )

    # Randomize the offset over the known 6.05M-row train split.
    # Hugging Face caps /filter length at 100.
    total_rows = 6_057_356

    max_offset = max(
        0,
        total_rows - BATCH_SIZE,
    )

    offset = random.randint(
        0,
        max_offset,
    )

    params = {
        "dataset":
            HF_DATASET,
        "config":
            HF_CONFIG,
        "split":
            HF_SPLIT,
        "where":
            where,
        "offset":
            offset,
        "length":
            min(BATCH_SIZE, 100),
    }

    try:
        response = requests.get(
            HF_FILTER_URL,
            params=params,
            headers={
                "Accept":
                    "application/json",
                "User-Agent":
                    "Discord-Survival-Mode/2.1",
            },
            timeout=REQUEST_TIMEOUT,
        )

        response.raise_for_status()

        payload = response.json()

        rows = payload.get(
            "rows",
            [],
        )

        result = []

        for item in rows:

            row = item.get(
                "row",
                item,
            )

            if not isinstance(
                row,
                dict,
            ):
                continue

            result.append(
                {
                    "PuzzleId":
                        row.get(
                            "PuzzleId",
                        ),
                    "FEN":
                        row.get(
                            "FEN",
                        ),
                    "Moves":
                        row.get(
                            "Moves",
                        ),
                    "Rating":
                        row.get(
                            "Rating",
                        ),
                    "Themes":
                        row.get(
                            "Themes",
                        ),
                }
            )

        return result

    except Exception as error:
        print(
            f"Lichess dataset filter error: {error}",
            flush=True,
        )

        # Fallback: use the Dataset Viewer /rows endpoint and filter
        # locally. This keeps Survival usable if /filter is temporarily
        # unavailable.
        rows_url = (
            "https://datasets-server.huggingface.co/rows"
        )

        rows_params = {
            "dataset":
                HF_DATASET,
            "config":
                HF_CONFIG,
            "split":
                HF_SPLIT,
            "offset":
                offset,
            "length":
                100,
        }

        fallback = requests.get(
            rows_url,
            params=rows_params,
            headers={
                "Accept":
                    "application/json",
                "User-Agent":
                    "Discord-Survival-Mode/2.1",
            },
            timeout=REQUEST_TIMEOUT,
        )

        fallback.raise_for_status()

        payload = fallback.json()

        result = []

        for item in payload.get(
            "rows",
            [],
        ):

            row = item.get(
                "row",
                item,
            )

            if not isinstance(
                row,
                dict,
            ):
                continue

            rating = row.get(
                "Rating"
            )

            try:
                rating = int(
                    rating
                )
            except Exception:
                continue

            if not (
                minimum_rating
                <= rating
                <= maximum_rating
            ):
                continue

            result.append(
                {
                    "PuzzleId":
                        row.get(
                            "PuzzleId",
                        ),
                    "FEN":
                        row.get(
                            "FEN",
                        ),
                    "Moves":
                        row.get(
                            "Moves",
                        ),
                    "Rating":
                        rating,
                    "Themes":
                        row.get(
                            "Themes",
                        ),
                }
            )

        return result


def sanitize_puzzle(
    item
):
    if not isinstance(
        item,
        dict,
    ):
        return None

    puzzle_id = item.get(
        "PuzzleId",
    )

    fen = item.get(
        "FEN",
    )

    moves = item.get(
        "Moves",
    )

    rating = item.get(
        "Rating",
    )

    if not puzzle_id or not fen or not moves:
        return None

    try:
        rating = int(
            rating
        )
    except Exception:
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
        "id":
            str(puzzle_id),
        "fen":
            str(fen),
        "moves":
            [
                str(move)
                for move in moves
            ],
        "rating":
            rating,
        "themes":
            (
                " ".join(item["Themes"])
                if isinstance(
                    item.get("Themes"),
                    list,
                )
                else str(
                    item.get(
                        "Themes",
                        "",
                    )
                )
            ),
        "url":
            (
                f"https://lichess.org/training/"
                f"{puzzle_id}"
            ),
    }


def choose_puzzle_for_number(
    puzzle_number,
    used_ids,
):
    minimum, maximum = (
        difficulty_target(
            puzzle_number
        )
    )

    for _attempt in range(8):

        items = fetch_lichess_batch(
            minimum,
            maximum,
        )

        candidates = []

        for raw in items:

            puzzle = sanitize_puzzle(
                raw
            )

            if not puzzle:
                continue

            if puzzle["id"] in used_ids:
                continue

            if (
                minimum
                <= puzzle["rating"]
                <= maximum
            ):
                candidates.append(
                    puzzle
                )

        if candidates:
            return random.choice(
                candidates
            )

    raise RuntimeError(
        "Could not find a fresh Lichess puzzle "
        f"in the required {minimum}-{maximum} rating band."
    )


def build_runtime_puzzle(
    puzzle
):
    """
    Lichess database FEN is the position BEFORE the opponent's first
    move. Apply the first move automatically. The remaining line starts
    with the player's move.
    """
    board = chess.Board(
        puzzle["fen"]
    )

    if len(
        puzzle["moves"]
    ) < 2:
        raise RuntimeError(
            "Lichess puzzle has no player solution move."
        )

    first_move = chess.Move.from_uci(
        puzzle["moves"][0]
    )

    if first_move not in board.legal_moves:
        raise RuntimeError(
            "Invalid Lichess first puzzle move."
        )

    board.push(
        first_move
    )

    solution = []

    for index, uci in enumerate(
        puzzle["moves"][1:],
        start=1,
    ):
        move = chess.Move.from_uci(
            uci
        )

        if move not in board.legal_moves:
            raise RuntimeError(
                "Invalid Lichess solution line."
            )

        solution.append(
            {
                "uci":
                    uci,
                "san":
                    board.san(
                        move
                    ),
                "color":
                    (
                        "white"
                        if board.turn
                        else "black"
                    ),
            }
        )

        board.push(
            move
        )

    player_color = solution[0][
        "color"
    ]

    return {
        "id":
            puzzle["id"],
        "rating":
            puzzle["rating"],
        "themes":
            puzzle.get(
                "themes",
                "",
            ),
        "url":
            puzzle.get(
                "url",
            ),
        "start_fen":
            board_fen_before_solution(
                puzzle
            ),
        "current_fen":
            board_fen_after_first_move(
                puzzle
            ),
        "solution":
            solution,
        "player_color":
            player_color,
        "next_solution_index":
            0,
        "first_solver_id":
            None,
        "first_solver_name":
            None,
        "helper_candidates":
            {},
        "helper_awarded":
            [],
        "wrong_users":
            [],
        "last_move_san":
            None,
        "accepted_moves":
            [],
    }


def board_fen_before_solution(
    puzzle
):
    board = chess.Board(
        puzzle["fen"]
    )

    move = chess.Move.from_uci(
        puzzle["moves"][0]
    )

    board.push(
        move
    )

    return board.fen()


def board_fen_after_first_move(
    puzzle
):
    return board_fen_before_solution(
        puzzle
    )


def render_board(
    puzzle,
):
    board = chess.Board(
        puzzle["current_fen"]
    )

    orientation = (
        chess.WHITE
        if puzzle["player_color"]
        == "white"
        else chess.BLACK
    )

    svg = chess.svg.board(
        board=board,
        orientation=orientation,
        size=520,
        coordinates=True,
    )

    png = awaitable_svg_to_png(
        svg
    )

    file = discord.File(
        fp=io.BytesIO(png),
        filename="survival.png",
    )

    return file, board


def awaitable_svg_to_png(
    svg
):
    import cairosvg

    return cairosvg.svg2png(
        bytestring=svg.encode(
            "utf-8"
        )
    )


async def send_puzzle_embed(
    channel,
    team,
    run,
):
    puzzle = run[
        "puzzle"
    ]

    file, board = render_board(
        puzzle
    )

    number = run[
        "puzzle_number"
    ]

    strikes = run[
        "strikes"
    ]

    lives = max(
        0,
        THREE_STRIKES - strikes,
    )

    heart_text = (
        "❤️" * lives
        + "🖤" * (
            THREE_STRIKES
            - lives
        )
    )

    minimum, maximum = (
        difficulty_target(
            number
        )
    )

    if maximum >= 9999:
        difficulty_text = (
            f"{minimum}+"
        )
    else:
        difficulty_text = (
            f"{minimum}–{maximum}"
        )

    embed = discord.Embed(
        title=(
            f"🔥 **SURVIVAL — {team}**"
        ),
        description=(
            f"**Puzzle #{number}**\n"
            f"Difficulty: **{puzzle['rating']}** "
            f"(target {difficulty_text})\n"
            f"Strikes: {heart_text}\n\n"
            f"Everyone can answer. First correct move wins "
            f"the position."
        ),
    )

    embed.set_image(
        url="attachment://survival.png"
    )

    await channel.send(
        embed=embed,
        file=file,
    )


def current_run_for_team(
    state,
    team_key,
):
    team = state["teams"].get(
        team_key
    )

    if not team:
        return None

    current = team.get(
        "current"
    )

    return current


def active_current_run(
    state
):
    for team_key, team in (
        state["teams"].items()
    ):
        current = team.get(
            "current"
        )

        if not current:
            continue

        if current.get(
            "status"
        ) == "active":
            return (
                team_key,
                team,
            )

    return None


def ensure_member(
    run,
    user,
):
    members = run.setdefault(
        "members",
        {}
    )

    uid = str(
        user.id
    )

    member = members.setdefault(
        uid,
        {
            "name":
                user.display_name,
            "correct":
                0,
            "wrong":
                0,
        },
    )

    member["name"] = (
        user.display_name
    )

    return member


def run_is_dead(
    run
):
    return (
        isinstance(run, dict)
        and int(run.get("strikes", 0)) >= THREE_STRIKES
        and run.get("paused_reason") == "three strikes"
    )


def run_status_text(
    team,
    run,
):
    if not run:
        return (
            f"👥 **{team}** has no saved "
            f"Survival run."
        )

    best_difficulty = run.get(
        "best_difficulty",
        0,
    )

    status = run.get(
        "status",
        "paused",
    )

    captain = (
        run.get(
            "captain_name"
        )
        or (
            f"<@{run.get('captain_id')}>"
            if run.get("captain_id")
            else "Unknown"
        )
    )

    return (
        f"🔥 **{team} — Survival**\n"
        f"Status: **{status}**\n"
        f"Captain: **{captain}**\n"
        f"Puzzle: **#{run.get('puzzle_number', 0)}**\n"
        f"Strikes: **{run.get('strikes', 0)}/{THREE_STRIKES}**\n"
        f"Best difficulty: **{best_difficulty}**"
    )


def team_saved_runs(
    team
):
    runs = []

    history = team.get(
        "history",
        []
    )

    for run in history:
        if isinstance(run, dict):
            runs.append(
                run
            )

    current = team.get(
        "current"
    )

    if isinstance(current, dict):
        runs.append(
            current
        )

    return runs


def survival_leaderboard(
    state
):
    rows = []

    for team_key, team in (
        state["teams"].items()
    ):
        for run in team_saved_runs(
            team
        ):
            rows.append(
                {
                    "team":
                        team.get(
                            "name",
                            team_key,
                        ),
                    "run_id":
                        run.get(
                            "run_id",
                            f"{team_key}:{len(rows)}",
                        ),
                    "puzzle":
                        int(
                            run.get(
                                "puzzle_number",
                                0,
                            )
                        ),
                    "difficulty":
                        int(
                            run.get(
                                "best_difficulty",
                                0,
                            )
                        ),
                    "status":
                        run.get(
                            "status",
                            "paused",
                        ),
                    "strikes":
                        int(
                            run.get(
                                "strikes",
                                0,
                            )
                        ),
                }
            )

    rows.sort(
        key=lambda row: (
            -row["puzzle"],
            -row["difficulty"],
            row["team"].casefold(),
            row["run_id"],
        )
    )

    if not rows:
        return (
            "🏆 **SURVIVAL LEADERBOARD**\n\n"
            "No runs yet."
        )

    lines = [
        "🏆 **SURVIVAL LEADERBOARD**",
        "",
    ]

    for rank, row in enumerate(
        rows,
        start=1,
    ):
        if rank == 1:
            prefix = "🥇"
        elif rank == 2:
            prefix = "🥈"
        elif rank == 3:
            prefix = "🥉"
        else:
            prefix = f"**{rank}.**"

        if row["status"] == "active":
            status = "🟢 ACTIVE"
        elif row["strikes"] >= THREE_STRIKES:
            status = "💀 DEAD"
        else:
            status = "⏸️ PAUSED"

        lines.append(
            f"{prefix} **{row['team']}** — "
            f"Puzzle **#{row['puzzle']}** — "
            f"{status} — "
            f"best difficulty **{row['difficulty']}**"
        )

    return "\n".join(
        lines
    )



class TeamRunSelectView(
    discord.ui.View
):
    def __init__(
        self,
        bot,
        requester_id,
        team_key,
        runs,
    ):
        super().__init__(
            timeout=None
        )
        self.bot = bot
        self.requester_id = requester_id
        self.team_key = team_key
        self.runs = runs

        options = []

        for index, run in enumerate(
            runs[:25]
        ):
            status = run.get(
                "status",
                "paused",
            )

            if run.get("strikes", 0) >= THREE_STRIKES:
                status_text = "DEAD"
            elif status == "active":
                status_text = "ACTIVE"
            else:
                status_text = "PAUSED"

            options.append(
                discord.SelectOption(
                    label=(
                        f"#{run.get('puzzle_number', 0)} — "
                        f"{status_text}"
                    )[:100],
                    description=(
                        f"Best difficulty "
                        f"{run.get('best_difficulty', 0)}"
                    )[:100],
                    value=str(index),
                )
            )

        select = discord.ui.Select(
            placeholder="Choose a Survival run...",
            options=options,
            min_values=1,
            max_values=1,
        )

        async def callback(
            interaction,
        ):
            if interaction.user.id != self.requester_id:
                await interaction.response.send_message(
                    "Only the person who requested the team info can choose.",
                    ephemeral=True,
                )
                return

            index = int(
                select.values[0]
            )

            run = self.runs[
                index
            ]

            await self.bot.show_run_details(
                interaction,
                self.team_key,
                run,
            )

        select.callback = callback

        self.add_item(
            select
        )


class ContinueOrRestartView(
    discord.ui.View
):
    def __init__(
        self,
        bot,
        requester_id,
        team_key,
    ):
        super().__init__(
            timeout=None
        )
        self.bot = bot
        self.requester_id = (
            requester_id
        )
        self.team_key = team_key

    async def interaction_check(
        self,
        interaction,
    ):
        # Persistent buttons survive Action restarts. Anyone may choose
        # Continue or Start New for a saved team run; the actual run rules
        # are enforced by resume_team/restart_team.
        return True

    @discord.ui.button(
        label="Continue",
        style=discord.ButtonStyle.success,
        custom_id="survival_continue",
    )
    async def continue_run(
        self,
        interaction,
        button,
    ):
        await self.bot.resume_team(
            interaction,
            self.team_key,
        )

    @discord.ui.button(
        label="Start New",
        style=discord.ButtonStyle.danger,
        custom_id="survival_start_new",
    )
    async def restart_run(
        self,
        interaction,
        button,
    ):
        await self.bot.restart_team(
            interaction,
            self.team_key,
        )


class SurvivalBot(
    commands.Bot
):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True

        super().__init__(
            command_prefix="!",
            intents=intents,
        )

        self.state = load_runs()
        self.pending_team = None
        self.game_lock = asyncio.Lock()

    def set_pending_team(
        self,
        pending,
    ):
        self.pending_team = pending
        self.state[
            "pending_team"
        ] = pending

        try:
            save_state(
                self.state,
                push=True,
            )
        except Exception as error:
            print(
                f"Could not persist pending team prompt: {error}",
                flush=True,
            )

    def clear_pending_team(
        self,
    ):
        self.pending_team = None
        self.state.pop(
            "pending_team",
            None,
        )

        try:
            save_state(
                self.state,
                push=True,
            )
        except Exception as error:
            print(
                f"Could not clear pending team prompt: {error}",
                flush=True,
            )

    async def setup_hook(
        self
    ):
        # Register persistent Continue / Start New buttons for every
        # saved current run. This makes old Discord buttons keep working
        # even after the GitHub Action restarts.
        for team_key, team in self.state.get(
            "teams",
            {}
        ).items():
            current = team.get(
                "current"
            )

            if (
                isinstance(current, dict)
                and current.get("status") != "active"
                and current.get("strikes", 0) < THREE_STRIKES
            ):
                self.add_view(
                    ContinueOrRestartView(
                        self,
                        0,
                        team_key,
                    )
                )

        self.bg_task = asyncio.create_task(
            self.maintenance_loop()
        )
        self.action_task = asyncio.create_task(
            self.action_limit_timer()
        )

    async def on_ready(
        self
    ):
        print(
            f"Survival Bot ready as {self.user}",
            flush=True,
        )

        # Recover stale lock from a dead Action.
        if (
            self.state
            and get_lock()
            is None
        ):
            active = active_current_run(
                self.state
            )

            if active:
                _, team = active
                current = team.get(
                    "current"
                )

                if current:
                    current[
                        "status"
                    ] = "paused"

                    save_state(
                        self.state,
                        push=True,
                    )

        active = active_current_run(
            self.state
        )

        if active:
            team_key, team = active
            run = team.get(
                "current"
            )

            if run:
                try:
                    write_lock(
                        team.get(
                            "name",
                            team_key,
                        ),
                        run.get(
                            "run_id",
                        ),
                        run.get(
                            "last_activity",
                            epoch_now(),
                        ),
                    )
                except Exception as error:
                    print(
                        f"Could not restore Survival lock: {error}",
                        flush=True,
                    )

    async def maintenance_loop(
        self
    ):
        await self.wait_until_ready()

        while not self.is_closed():

            try:
                await self.check_timeout()

            except Exception as error:
                print(
                    f"Survival maintenance error: {error}",
                    flush=True,
                )

            await asyncio.sleep(30)

    async def check_timeout(
        self
    ):
        active = active_current_run(
            self.state
        )

        if not active:
            return

        team_key, team = active
        run = team.get(
            "current"
        )

        if not run:
            return

        elapsed = (
            epoch_now()
            - float(
                run.get(
                    "last_activity",
                    epoch_now(),
                )
            )
        )

        if elapsed < INACTIVITY_SECONDS:
            return

        run[
            "status"
        ] = "paused"

        run[
            "paused_reason"
        ] = "10-minute inactivity"

        update_best(
            team,
            run,
        )

        save_state(
            self.state,
            push=True,
        )

        clear_lock()

        channel = self.get_channel(
            CHANNEL_ID
        )

        if channel:
            await channel.send(
                f"⏸️ **{team.get('name', team_key)} Survival paused.**\n"
                f"Puzzle **#{run.get('puzzle_number', 0)}**\n"
                f"Strikes **{run.get('strikes', 0)}/{THREE_STRIKES}**\n"
                f"Best difficulty **{run.get('best_difficulty', 0)}**\n\n"
                f"Run saved. Come back later with `!survival`."
            )

    def archive_current_run(
        self,
        team,
    ):
        current = team.get(
            "current"
        )

        if not isinstance(
            current,
            dict,
        ):
            return

        snapshot = copy.deepcopy(
            current
        )

        team.setdefault(
            "history",
            []
        ).append(
            snapshot
        )

        team[
            "current"
        ] = None

    async def start_new_run(
        self,
        team_key,
        display_name,
        requester,
        force=False,
    ):
        state = self.state

        active = active_current_run(
            state
        )

        if active:
            active_key, active_team_record = (
                active
            )

            if active_key != team_key:
                active_name = (
                    active_team_record.get(
                        "name",
                        active_key,
                    )
                )

                await requester.channel.send(
                    f"⚠️ **{active_name} Survival is currently active.**\n"
                    f"Finish or stop that run before starting another team."
                )

                return

        team = team_record(
            state,
            team_key,
            display_name,
        )

        existing = team.get(
            "current"
        )

        if existing and not force:
            if existing.get(
                "status"
            ) == "active":
                await requester.channel.send(
                    run_status_text(
                        display_name,
                        existing,
                    )
                )
                return

            view = ContinueOrRestartView(
                self,
                requester.id,
                team_key,
            )

            await requester.channel.send(
                f"♻️ **{display_name} has a saved Survival run.**\n\n"
                f"{run_status_text(display_name, existing)}\n\n"
                f"Continue it or start a new run?",
                view=view,
            )

            return

        if existing and force:
            self.archive_current_run(
                team
            )

        run = {
            "run_id":
                f"{team_key}:{int(time.time())}",
            "started_by_id":
                str(requester.id),
            "captain_id":
                str(requester.id),
            "captain_name":
                requester.display_name,
            "mode":
                "coop",
            "status":
                "active",
            "started_at":
                utc_now().isoformat(),
            "last_activity":
                epoch_now(),
            "paused_reason":
                None,
            "puzzle_number":
                1,
            "strikes":
                0,
            "best_difficulty":
                0,
            "used_puzzle_ids":
                [],
            "members":
                {},
            "puzzle":
                None,
            "first_solver_id":
                None,
            "first_solver_name":
                None,
            "helper_candidates":
                {},
            "helper_awarded":
                [],
        }

        team[
            "current"
        ] = run

        update_best(
            team,
            run,
        )

        save_state(
            state,
            push=True,
        )

        write_lock(
            display_name,
            run["run_id"],
            run["last_activity"],
        )

        await self.post_next_puzzle(
            requester.channel,
            team_key,
        )

    async def resume_team(
        self,
        interaction,
        team_key,
    ):
        team = self.state["teams"][
            team_key
        ]

        run = team.get(
            "current"
        )

        if not run:
            await interaction.response.send_message(
                "No saved run.",
                ephemeral=True,
            )
            return

        if run_is_dead(run):
            await interaction.response.send_message(
                f"💀 This run is dead at Puzzle #{run.get('puzzle_number', 0)}. "
                "Add a heart first or start a new run.",
                ephemeral=True,
            )
            return

        if run.get("status") == "active":
            await interaction.response.send_message(
                f"✅ **{team.get('name', team_key)}** is already active.",
                ephemeral=True,
            )
            return

        active = active_current_run(
            self.state
        )

        if active and active[0] != team_key:
            await interaction.response.send_message(
                "Another Survival team is already active.",
                ephemeral=True,
            )
            return

        run[
            "status"
        ] = "active"
        run[
            "last_activity"
        ] = epoch_now()

        save_state(
            self.state,
            push=True,
        )

        write_lock(
            team.get(
                "name",
                team_key,
            ),
            run.get(
                "run_id",
            ),
            run["last_activity"],
        )

        await interaction.response.send_message(
            f"▶️ **{team.get('name', team_key)} resumed.**"
        )

        puzzle = run.get(
            "puzzle"
        )

        # Some older saved runs have the position number but no serialized
        # puzzle object. In that case, recover the run by loading a puzzle
        # for the current number instead of silently showing nothing.
        if isinstance(
            puzzle,
            dict,
        ) and puzzle.get(
            "current_fen"
        ):
            await self.send_current_puzzle(
                interaction.channel,
                team.get(
                    "name",
                    team_key,
                ),
                run,
            )
        else:
            await self.post_next_puzzle(
                interaction.channel,
                team_key,
            )

    async def restart_team(
        self,
        interaction,
        team_key,
    ):
        team = self.state["teams"][
            team_key
        ]

        run = team.get(
            "current"
        )

        if run:
            update_best(
                team,
                run,
            )

        team[
            "current"
        ] = None

        save_state(
            self.state,
            push=True,
        )

        await interaction.response.send_message(
            f"🔄 **{team.get('name', team_key)}** will start a new Survival run."
        )

        await self.start_new_run(
            team_key,
            team.get(
                "name",
                team_key,
            ),
            interaction,
            force=True,
        )

    async def post_next_puzzle(
        self,
        channel,
        team_key,
    ):
        team = self.state["teams"][
            team_key
        ]

        run = team[
            "current"
        ]

        number = int(
            run["puzzle_number"]
        )

        used_ids = set(
            run.get(
                "used_puzzle_ids",
                [],
            )
        )

        try:
            puzzle_source = choose_puzzle_for_number(
                number,
                used_ids,
            )

            puzzle = build_runtime_puzzle(
                puzzle_source
            )

        except Exception as error:
            print(
                f"Survival puzzle fetch error: {error}",
                flush=True,
            )

            await channel.send(
                "❌ **Survival could not load the next Lichess puzzle.** "
                "The run is paused so no progress is lost."
            )

            run[
                "status"
            ] = "paused"

            run[
                "paused_reason"
            ] = "puzzle fetch error"

            save_state(
                self.state,
                push=True,
            )

            clear_lock()
            return

        run[
            "puzzle"
        ] = puzzle

        run.setdefault(
            "used_puzzle_ids",
            []
        ).append(
            puzzle["id"]
        )

        run[
            "first_solver_id"
        ] = None

        run[
            "first_solver_name"
        ] = None

        run[
            "helper_candidates"
        ] = {}

        run[
            "helper_awarded"
        ] = []

        update_best(
            team,
            run,
        )

        run[
            "best_difficulty"
        ] = max(
            int(
                run.get(
                    "best_difficulty",
                    0,
                )
            ),
            int(
                puzzle["rating"]
            ),
        )

        save_state(
            self.state,
            push=True,
        )

        await send_puzzle_embed(
            channel,
            team.get(
                "name",
                team_key,
            ),
            run,
        )

    async def send_current_puzzle(
        self,
        channel,
        team,
        run,
    ):
        if not run.get(
            "puzzle"
        ):
            return

        await send_puzzle_embed(
            channel,
            team,
            run,
        )

    async def score_completed_puzzle(
        self,
        run,
    ):
        """
        Score only after the whole Lichess puzzle is solved.
        First solver +1.
        Each unique helper who was correct later +0.5.

        Both rewards use unique transaction IDs in the same shared
        transaction ledger as Guess Chatter / Guess Chess Chatter.
        """
        first_id = run.get(
            "first_solver_id"
        )

        if first_id:
            first_name = run.get(
                "first_solver_name",
                "Unknown",
            )

            tx_id = (
                f"survival:"
                f"{run['run_id']}:"
                f"{run['puzzle_number']}:"
                f"first:{first_id}"
            )

            await asyncio.to_thread(
                add_points,
                first_id,
                first_name,
                1.0,
                tx_id,
                source="survival-first",
            )

        for helper_id, helper_name in (
            run.get(
                "helper_candidates",
                {}
            ).items()
        ):
            if str(helper_id) == str(
                first_id
            ):
                continue

            tx_id = (
                f"survival:"
                f"{run['run_id']}:"
                f"{run['puzzle_number']}:"
                f"helper:{helper_id}"
            )

            await asyncio.to_thread(
                add_points,
                helper_id,
                helper_name,
                0.5,
                tx_id,
                source="survival-helper",
            )

        run[
            "first_solver_id"
        ] = None

        run[
            "first_solver_name"
        ] = None

        run[
            "helper_candidates"
        ] = {}

        run[
            "helper_awarded"
        ] = []

    async def get_captain_display(
        self,
        run,
    ):
        captain_id = self.get_run_captain_id(
            run
        )

        stored_name = run.get(
            "captain_name"
        )

        if stored_name:
            return stored_name

        if captain_id:
            try:
                user = self.get_user(
                    int(captain_id)
                )

                if user:
                    return user.display_name

                user = await self.fetch_user(
                    int(captain_id)
                )

                if user:
                    return user.display_name

            except Exception:
                pass

            return f"<@{captain_id}>"

        return "Unknown"


    def get_run_captain_id(
        self,
        run,
    ):
        return str(
            run.get(
                "captain_id",
                run.get("started_by_id", ""),
            )
        )

    def is_run_captain(
        self,
        user,
        run,
    ):
        captain_id = self.get_run_captain_id(
            run
        )
        return (
            captain_id
            and str(user.id) == captain_id
        )

    async def set_run_mode(
        self,
        message,
        team_key,
        mode,
    ):
        team = self.state["teams"].get(
            team_key
        )

        if not team:
            await message.channel.send(
                f"❌ Team **{team_key}** does not exist."
            )
            return

        run = team.get("current")

        if not run or run.get("status") != "active":
            await message.channel.send(
                f"❌ **{team.get('name', team_key)}** does not have an active run."
            )
            return

        if run_is_dead(run):
            await message.channel.send(
                f"💀 **{team.get('name', team_key)}** is a dead run."
            )
            return

        if not self.is_run_captain(
            message.author,
            run,
        ):
            await message.channel.send(
                f"❌ Only captain **{run.get('captain_name', 'the captain')}** "
                "can change the run mode."
            )
            return

        run["mode"] = mode
        run["last_activity"] = epoch_now()

        save_state(
            self.state,
            push=True,
        )

        if mode == "solo":
            await message.channel.send(
                f"🔒 **{team.get('name', team_key)} is now SOLO.** "
                f"Only captain **{run.get('captain_name', message.author.display_name)}** "
                "can answer."
            )
        else:
            await message.channel.send(
                f"🤝 **{team.get('name', team_key)} is now CO-OP.** "
                "Everyone can answer again."
            )

    async def handle_survival_move(
        self,
        message,
        move_text,
        run,
    ):
        if not run:
            return

        user = message.author

        async with self.game_lock:

            active = active_current_run(
                self.state
            )

            if not active:
                return

            team_key, team = active
            current_run = team.get(
                "current"
            )

            if current_run is not run:
                return

            if run.get(
                "status"
            ) != "active":
                return

            if (
                run.get("mode", "coop") == "solo"
                and not self.is_run_captain(
                    message.author,
                    run,
                )
            ):
                await message.channel.send(
                    f"🔒 **Solo mode is active.** "
                    f"Only captain **{run.get('captain_name', 'the captain')}** "
                    "can answer."
                )
                return

            puzzle = run.get(
                "puzzle"
            )

            if not puzzle:
                return

            run[
                "last_activity"
            ] = epoch_now()

            ensure_member(
                run,
                user,
            )

            submitted = move_text.strip()

            if len(
                submitted.split()
            ) != 1:
                await message.channel.send(
                    f"❌ **One move at a time, "
                    f"{user.display_name}.**"
                )
                return

            next_index = int(
                puzzle.get(
                    "next_solution_index",
                    0,
                )
            )

            solution = puzzle[
                "solution"
            ]

            if next_index >= len(
                solution
            ):
                return

            # Duplicate-safe handling:
            # a move that was already accepted earlier in THIS puzzle is
            # never a new strike when it arrives late from another player.
            #
            # This also survives automatic opponent replies and is safer
            # than relying on next_solution_index, because the bot can jump
            # several plies after a correct player move.
            accepted_moves = puzzle.setdefault(
                "accepted_moves",
                []
            )

            normalized_submitted = (
                submitted.casefold().rstrip("+#")
            )

            # First, test the move against the CURRENT position normally.
            board = chess.Board(
                puzzle["current_fen"]
            )

            expected = solution[
                next_index
            ]

            correct, accepted_move = parse_survival_move(
                board,
                submitted,
                expected,
            )

            if not correct:
                for accepted in reversed(
                    accepted_moves[-10:]
                ):
                    accepted_san = str(
                        accepted.get(
                            "san",
                            ""
                        )
                    ).casefold().rstrip("+#")

                    if (
                        accepted_san
                        == normalized_submitted
                    ):
                        await message.channel.send(
                            f"✅ **That move was already accepted, "
                            f"{user.display_name}.**"
                        )
                        return


            if not correct:

                wrong_users = puzzle.setdefault(
                    "wrong_users",
                    []
                )

                user_id = str(
                    user.id
                )

                if user_id in wrong_users:
                    await message.channel.send(
                        f"❌ **That miss is already counted, "
                        f"{user.display_name}.**"
                    )
                    return

                wrong_users.append(
                    user_id
                )

                member = ensure_member(
                    run,
                    user,
                )

                member[
                    "wrong"
                ] += 1

                run[
                    "strikes"
                ] += 1

                save_state(
                    self.state,
                    push=True,
                )

                if run[
                    "strikes"
                ] >= THREE_STRIKES:

                    run[
                        "status"
                    ] = "paused"

                    run[
                        "paused_reason"
                    ] = "three strikes"

                    update_best(
                        team,
                        run,
                    )

                    save_state(
                        self.state,
                        push=True,
                    )

                    clear_lock()

                    await message.channel.send(
                        f"💀 **SURVIVAL OVER — "
                        f"{team.get('name', team_key)}**\n"
                        f"Reached **Puzzle "
                        f"#{run.get('puzzle_number', 0)}**\n"
                        f"Three strikes.\n"
                        f"Best difficulty: "
                        f"**{run.get('best_difficulty', 0)}**\n\n"
                        f"Run saved."
                    )

                    return

                await message.channel.send(
                    f"❌ **Wrong! "
                    f"Strike {run['strikes']}/"
                    f"{THREE_STRIKES}.**\n"
                    f"❤️ "
                    f"{max(0, THREE_STRIKES - run['strikes'])}"
                )

                write_lock(
                    team.get(
                        "name",
                        team_key,
                    ),
                    run.get(
                        "run_id",
                    ),
                    run[
                        "last_activity"
                    ],
                )

                return

            # Correct move.
            member = ensure_member(
                run,
                user,
            )

            member[
                "correct"
            ] += 1

            user_id = str(
                user.id
            )

            if next_index == 0:
                run[
                    "first_solver_id"
                ] = user_id

                run[
                    "first_solver_name"
                ] = user.display_name

            elif (
                user_id
                != str(
                    run.get(
                        "first_solver_id"
                    )
                )
            ):
                run.setdefault(
                    "helper_candidates",
                    {}
                )[user_id] = (
                    user.display_name
                )

            # Use the actual move the user entered. This matters for
            # alternative valid checkmates that are not the single move
            # stored in the Lichess principal variation.
            move = accepted_move

            if move is None:
                return

            accepted_san = board.san(
                move
            )

            # Store the exact accepted move BEFORE advancing the position.
            puzzle[
                "last_accepted_move_uci"
            ] = move.uci()

            puzzle[
                "last_accepted_move_san"
            ] = accepted_san

            puzzle[
                "last_accepted_move_index"
            ] = next_index

            puzzle[
                "last_accepted_at"
            ] = epoch_now()

            puzzle.setdefault(
                "accepted_moves",
                []
            ).append(
                {
                    "san":
                        accepted_san,
                    "uci":
                        move.uci(),
                    "accepted_at":
                        epoch_now(),
                    "solver_id":
                        user_id,
                }
            )

            # Keep only the most recent accepted player moves.
            puzzle[
                "accepted_moves"
            ] = puzzle[
                "accepted_moves"
            ][-10:]

            puzzle[
                "position_before_last_move"
            ] = board.fen()

            board.push(
                move
            )

            # If the submitted move itself checkmates, the puzzle is
            # solved even when Lichess stored a different mate line.
            alternative_checkmate = board.is_checkmate()

            next_index += 1
            opponent_replies = []

            if alternative_checkmate:
                next_index = len(
                    solution
                )

            while next_index < len(
                solution
            ):

                reply = solution[
                    next_index
                ]

                if reply["color"] == puzzle[
                    "player_color"
                ]:
                    break

                reply_move = chess.Move.from_uci(
                    reply["uci"]
                )

                if reply_move not in board.legal_moves:
                    break

                board.push(
                    reply_move
                )

                opponent_replies.append(
                    reply["san"]
                )

                next_index += 1

            puzzle[
                "current_fen"
            ] = board.fen()

            puzzle[
                "next_solution_index"
            ] = next_index

            puzzle[
                "last_move_san"
            ] = expected["san"]

            run[
                "last_activity"
            ] = epoch_now()

            save_state(
                self.state,
                push=True,
            )

            if next_index >= len(
                solution
            ):
                team_key = None

                for key, team_record_value in (
                    self.state["teams"].items()
                ):
                    if team_record_value.get(
                        "current"
                    ) is run:
                        team_key = key
                        team = team_record_value
                        break

                if team_key is None:
                    return

                # Survival deliberately has NO shared leaderboard points.
                puzzle_number_completed = int(
                    run["puzzle_number"]
                )

                run[
                    "puzzle"
                ] = None

                run[
                    "puzzle_number"
                ] = (
                    puzzle_number_completed
                    + 1
                )

                run[
                    "first_solver_id"
                ] = None

                run[
                    "first_solver_name"
                ] = None

                run[
                    "helper_candidates"
                ] = {}

                save_state(
                    self.state,
                    push=True,
                )

                member_summary = (
                    f"✅ **Puzzle "
                    f"#{puzzle_number_completed} "
                    f"solved!**"
                )

                if opponent_replies:
                    member_summary += (
                        "\n"
                        f"↩️ **Opponent:** "
                        f"{' '.join(opponent_replies)}"
                    )

                await message.channel.send(
                    member_summary
                    + "\n"
                    + f"Next up: **Puzzle "
                    f"#{run['puzzle_number']}**."
                )

                await self.post_next_puzzle(
                    message.channel,
                    team_key,
                )

                return

            file, display_board = render_board(
                puzzle
            )

            remaining = (
                len(
                    solution
                )
                - next_index
            )

            embed = discord.Embed(
                title=(
                    f"🔥 **SURVIVAL — "
                    f"{team.get('name', team_key)}**"
                ),
                description=(
                    f"✅ **{accepted_san}**\n"
                    + (
                        f"↩️ Opponent: "
                        f"{' '.join(opponent_replies)}\n"
                        if opponent_replies
                        else ""
                    )
                    + f"**{remaining} move"
                    + (
                        "s"
                        if remaining != 1
                        else ""
                    )
                    + " remaining.**\n"
                    + f"Strikes: "
                    f"{'❤️' * max(0, THREE_STRIKES - run['strikes'])}"
                    f"{'🖤' * run['strikes']}"
                ),
            )

            embed.set_image(
                url="attachment://survival.png"
            )

            await message.channel.send(
                embed=embed,
                file=file,
            )

            write_lock(
                team.get(
                    "name",
                    team_key,
                ),
                run.get(
                    "run_id",
                ),
                run[
                    "last_activity"
                ],
            )


    def _team_name_for_run(
        self,
        run,
    ):
        for team in (
            self.state["teams"].values()
        ):
            if team.get(
                "current"
            ) is run:
                return team.get(
                    "name",
                    "Survival",
                )

        return "Survival"

    async def on_message(
        self,
        message,
    ):
        if message.author.bot:
            return

        if message.channel.id != CHANNEL_ID:
            return

        # Every human message keeps an active Survival alive for another
        # 10 minutes, even if it is ordinary chat.
        active = active_current_run(
            self.state
        )

        if active:
            team_key, team = active
            run = team.get(
                "current"
            )

            if run:
                run[
                    "last_activity"
                ] = epoch_now()

        content = message.content.strip()

        if not content:
            return

        lower = content.casefold()

        if await self.handle_admin_command(
            message,
            lower,
            content,
        ):
            return

        # Team information command:
        # !THE SQUAD
        if (
            content.startswith("!")
            and lower not in {
                "!survival",
                "!stopsurvival",
                "!survivallb",
                "!survivalboard",
                "!slb",
            }
        ):
            possible_team = (
                " ".join(
                    content[1:].split()
                )
            )

            key = normalize_team_name(
                possible_team
            )

            if key in self.state["teams"]:
                await self.show_team(
                    message,
                    key,
                )
                return

        if lower in {
            "!survivallb",
            "!survivalboard",
            "!slb",
        }:
            await message.channel.send(
                survival_leaderboard(
                    self.state
                )
            )
            return

        if lower in {
            "!repeat",
            "repeat",
        }:
            active = active_current_run(
                self.state
            )

            if active:
                team_key, team = active
                run = team.get(
                    "current"
                )

                if run:
                    if isinstance(
                        run.get("puzzle"),
                        dict,
                    ) and run["puzzle"].get(
                        "current_fen"
                    ):
                        await self.send_current_puzzle(
                            message.channel,
                            team.get(
                                "name",
                                team_key,
                            ),
                            run,
                        )
                    else:
                        await self.post_next_puzzle(
                            message.channel,
                            team_key,
                        )

                    return

            # No active run: allow repeat of a saved, non-dead current run.
            candidates = []

            for team_key, team in self.state["teams"].items():
                run = team.get(
                    "current"
                )

                if not isinstance(
                    run,
                    dict,
                ):
                    continue

                if run_is_dead(
                    run
                ):
                    continue

                if isinstance(
                    run.get("puzzle"),
                    dict,
                ) and run["puzzle"].get(
                    "current_fen"
                ):
                    candidates.append(
                        (
                            int(
                                run.get(
                                    "puzzle_number",
                                    0,
                                )
                            ),
                            team_key,
                            team,
                            run,
                        )
                    )

            if candidates:
                candidates.sort(
                    reverse=True
                )

                _, team_key, team, run = (
                    candidates[0]
                )

                await message.channel.send(
                    f"⏸️ **{team.get('name', team_key)}** is paused. "
                    "Showing the saved puzzle."
                )

                await self.send_current_puzzle(
                    message.channel,
                    team.get(
                        "name",
                        team_key,
                    ),
                    run,
                )

                return

            await message.channel.send(
                "❌ **No saved Survival puzzle is available to repeat.**"
            )
            return

        if lower == "!stopsurvival":
            await self.stop_survival(
                message
            )
            return

        if lower.startswith(
            "!survival "
        ):
            direct_team = content.split(
                None,
                1,
            )[1].strip()

            if valid_team_name(
                direct_team
            ):
                active = active_current_run(
                    self.state
                )

                if active:
                    await message.channel.send(
                        f"⚠️ **{active[1].get('name', active[0])}** "
                        "already has an active Survival run."
                    )
                    return

                self.clear_pending_team()

                key = normalize_team_name(
                    direct_team
                )

                team = self.state["teams"].get(
                    key
                )

                if (
                    team
                    and team.get(
                        "current"
                    )
                ):
                    view = ContinueOrRestartView(
                        self,
                        message.author.id,
                        key,
                    )

                    captain = await self.get_captain_display(
                        team["current"]
                    )

                    await message.channel.send(
                        f"♻️ **{team.get('name', direct_team)}** "
                        "has a saved Survival run.\n"
                        f"👑 **Captain:** {captain}\n\n"
                        + run_status_text(
                            team.get(
                                "name",
                                direct_team,
                            ),
                            team["current"],
                        )
                        + "\n\n"
                        "Continue it or start a new run?",
                        view=view,
                    )
                else:
                    await self.start_new_run(
                        key,
                        direct_team,
                        message,
                    )

                return

        if lower == "!survival":
            self.set_pending_team(
                {
                    "user_id":
                        message.author.id,
                    "channel_id":
                        message.channel.id,
                    "expires":
                        epoch_now()
                        + PENDING_TEAM_SECONDS,
                }
            )

            active = active_current_run(
                self.state
            )

            if active:
                active_name = (
                    active[1].get(
                        "name",
                        active[0],
                    )
                )

                await message.channel.send(
                    f"🔥 **Survival is currently active for "
                    f"{active_name}.**\n"
                    f"Use `!stopsurvival` to pause it first."
                )
            else:
                await message.channel.send(
                    "🔥 **Survival Mode**\n"
                    "Enter a team name in your next message."
                )

            return

        # If someone was asked for a team name, consume only the next
        # non-command message from the requester.
        # Prefer this bot instance's fresh prompt over any stale
        # persisted prompt left by an older Action run.
        pending = (
            self.pending_team
            or self.state.get(
                "pending_team"
            )
        )

        if (
            pending
            and epoch_now()
            <= float(
                pending.get(
                    "expires",
                    0,
                )
            )
            and str(message.author.id)
            == str(
                pending.get(
                    "user_id"
                )
            )
            and str(message.channel.id)
            == str(
                pending.get(
                    "channel_id"
                )
            )
            and not content.startswith("!")
        ):

            self.clear_pending_team()

            team_name = " ".join(
                content.split()
            )

            if not valid_team_name(
                team_name
            ):
                await message.channel.send(
                    "❌ Team name must be 2–32 characters."
                )
                return

            key = normalize_team_name(
                team_name
            )

            active = active_current_run(
                self.state
            )

            if active:
                await message.channel.send(
                    f"⚠️ **{active[1].get('name', active[0])}** "
                    "already has an active Survival run."
                )
                return

            team = self.state["teams"].get(
                key
            )

            if (
                team
                and team.get(
                    "current"
                )
            ):
                view = ContinueOrRestartView(
                    self,
                    message.author.id,
                    key,
                )

                captain = await self.get_captain_display(
                    team["current"]
                )

                await message.channel.send(
                    f"♻️ **{team.get('name', team_name)}** "
                    "has a saved Survival run.\n"
                    f"👑 **Captain:** {captain}\n\n"
                    + run_status_text(
                        team.get(
                            "name",
                            team_name,
                        ),
                        team["current"],
                    )
                    + "\n\n"
                    "Continue it or start a new run?",
                    view=view,
                )
                return

            await self.start_new_run(
                key,
                team_name,
                message,
            )

            return

        if pending and epoch_now() > float(
            pending.get(
                "expires",
                0,
            )
        ):
            self.clear_pending_team()

        # While Survival is active, route chess-like messages to Survival.
        active = active_current_run(
            self.state
        )

        if not active:
            return

        team_key, team = active
        run = team.get(
            "current"
        )

        if not run:
            return

        # Commands used by other modes are ignored by Survival.
        if content.startswith("!"):
            return

        candidate = (
            content[1:].strip()
            if content.startswith("!")
            else content
        )

        if not chess_move_like(
            candidate
        ):
            return

        await self.handle_survival_move(
            message,
            candidate,
            run,
        )

    async def show_team(
        self,
        message,
        team_key,
    ):
        team = self.state["teams"].get(
            team_key
        )

        if not team:
            await message.channel.send(
                f"❌ Team **{team_key}** does not exist."
            )
            return

        runs = team_saved_runs(
            team
        )

        # Newest/highest run first.
        runs.sort(
            key=lambda run: (
                -int(
                    run.get(
                        "puzzle_number",
                        0,
                    )
                ),
                str(
                    run.get(
                        "started_at",
                        "",
                    )
                ),
            )
        )

        if len(runs) == 0:
            await message.channel.send(
                f"👥 **{team.get('name', team_key)}** has no Survival runs."
            )
            return

        if len(runs) == 1:
            await self.send_run_details_message(
                message.channel,
                team_key,
                runs[0],
            )
            return

        lines = [
            f"🔎 **Which {team.get('name', team_key)} run do you want to view?**",
            "",
        ]

        for index, run in enumerate(
            runs[:25],
            start=1,
        ):
            status = run.get(
                "status",
                "paused",
            )

            if int(
                run.get(
                    "strikes",
                    0,
                )
            ) >= THREE_STRIKES:
                status_text = "💀 DEAD"
            elif status == "active":
                status_text = "🟢 ACTIVE"
            else:
                status_text = "⏸️ PAUSED"

            lines.append(
                f"**{index}.** Puzzle **#{run.get('puzzle_number', 0)}** "
                f"— {status_text} "
                f"— best difficulty **{run.get('best_difficulty', 0)}**"
            )

        view = TeamRunSelectView(
            self,
            message.author.id,
            team_key,
            runs[:25],
        )

        await message.channel.send(
            "\n".join(lines),
            view=view,
        )

    async def show_run_details(
        self,
        interaction,
        team_key,
        run,
    ):
        await interaction.response.send_message(
            self.format_run_details(
                team_key,
                run,
            )
        )

    def format_run_details(
        self,
        team_key,
        run,
    ):
        team = self.state["teams"].get(
            team_key,
            {},
        )

        members = list(
            run.get(
                "members",
                {}
            ).values()
        )

        members.sort(
            key=lambda item: (
                -int(
                    item.get(
                        "correct",
                        0,
                    )
                ),
                int(
                    item.get(
                        "wrong",
                        0,
                    )
                ),
                str(
                    item.get(
                        "name",
                        "",
                    )
                ).casefold(),
            )
        )

        if int(
            run.get(
                "strikes",
                0,
            )
        ) >= THREE_STRIKES:
            status = "💀 DEAD"
        elif run.get(
            "status"
        ) == "active":
            status = "🟢 ACTIVE"
        else:
            status = "⏸️ PAUSED"

        if members:
            member_lines = []

            for index, member in enumerate(
                members,
                start=1,
            ):
                member_lines.append(
                    f"**{index}.** "
                    f"{member.get('name', 'Unknown')} — "
                    f"**{member.get('correct', 0)} correct** "
                    f"/ {member.get('wrong', 0)} wrong"
                )

            members_text = "\n".join(
                member_lines
            )
        else:
            members_text = (
                "No contributors recorded."
            )

        captain_id = self.get_run_captain_id(
            run
        )

        captain_display = (
            run.get(
                "captain_name"
            )
            or (
                f"<@{captain_id}>"
                if captain_id
                else "Unknown"
            )
        )

        return (
            f"👥 **{team.get('name', team_key)} — Run**\n"
            f"**Status:** {status}\n"
            f"**Captain:** {captain_display}\n"
            f"**Mode:** {str(run.get('mode', 'coop')).upper()}\n"
            f"**Puzzle:** #{run.get('puzzle_number', 0)}\n"
            f"**Best difficulty:** {run.get('best_difficulty', 0)}\n"
            f"**Strikes:** {run.get('strikes', 0)}/3\n\n"
            f"**Contributors:**\n"
            f"{members_text}"
        )

    async def send_run_details_message(
        self,
        channel,
        team_key,
        run,
    ):
        await channel.send(
            self.format_run_details(
                team_key,
                run,
            )
        )


    def is_shark_admin(
        self,
        user,
    ):
        return (
            user.display_name.casefold()
            == SHARK_ADMIN_NAME
        )

    async def delete_team(
        self,
        message,
        team_key,
    ):
        if not self.is_shark_admin(
            message.author
        ):
            await message.channel.send(
                "❌ Only **Sharkmeister** can delete Survival teams."
            )
            return

        team = self.state[
            "teams"
        ].get(
            team_key
        )

        if not team:
            await message.channel.send(
                f"❌ Team **{team_key}** does not exist."
            )
            return

        active = active_current_run(
            self.state
        )

        was_active = (
            active is not None
            and active[0] == team_key
        )

        del self.state[
            "teams"
        ][
            team_key
        ]

        save_state(
            self.state,
            push=True,
        )

        if was_active:
            clear_lock()

        await message.channel.send(
            f"🗑️ **{team.get('name', team_key)}** "
            "has been removed from the Survival team list."
        )

    async def add_heart(
        self,
        message,
        team_key,
    ):
        if not self.is_shark_admin(
            message.author
        ):
            await message.channel.send(
                "❌ Only **Sharkmeister** can add Survival hearts."
            )
            return

        team = self.state[
            "teams"
        ].get(
            team_key
        )

        if not team:
            await message.channel.send(
                f"❌ Team **{team_key}** does not exist."
            )
            return

        run = team.get(
            "current"
        )

        if not run:
            await message.channel.send(
                f"❌ **{team.get('name', team_key)}** "
                "has no saved Survival run."
            )
            return

        old_strikes = int(
            run.get(
                "strikes",
                0,
            )
        )

        if old_strikes <= 0:
            await message.channel.send(
                f"❤️ **{team.get('name', team_key)}** "
                "already has all 3 hearts."
            )
            return

        run[
            "strikes"
        ] = max(
            0,
            old_strikes - 1,
        )

        # If the run ended specifically because of three strikes,
        # adding a heart makes it resumable again.
        if run.get(
            "status"
        ) != "active":
            run[
                "status"
            ] = "paused"

        run[
            "paused_reason"
        ] = None

        run[
            "last_activity"
        ] = epoch_now()

        save_state(
            self.state,
            push=True,
        )

        await message.channel.send(
            f"❤️ **Added 1 heart to "
            f"{team.get('name', team_key)}.**\n"
            f"Hearts now: "
            f"{'❤️' * (THREE_STRIKES - run['strikes'])}"
            f"{'🖤' * run['strikes']}"
        )

    async def handle_admin_command(
        self,
        message,
        lower,
        content,
    ):
        if lower.startswith(
            "!delete "
        ):
            team_name = content[
                len("!delete "):
            ].strip()

            if not team_name:
                await message.channel.send(
                    "❌ Usage: `!delete <team name>`"
                )
                return True

            await self.delete_team(
                message,
                normalize_team_name(
                    team_name
                ),
            )
            return True

        if lower.startswith(
            "!addheart "
        ):
            team_name = content[
                len("!addheart "):
            ].strip()

            if not team_name:
                await message.channel.send(
                    "❌ Usage: `!addheart <team name>`"
                )
                return True

            await self.add_heart(
                message,
                normalize_team_name(
                    team_name
                ),
            )
            return True

        return False

    async def stop_survival(
        self,
        message,
    ):
        async with self.game_lock:
            return await self._stop_survival_locked(
                message
            )

    async def _stop_survival_locked(
        self,
        message,
    ):
        active = active_current_run(
            self.state
        )

        if not active:
            await message.channel.send(
                "There is no active Survival run."
            )
            return

        team_key, team = active
        run = team.get(
            "current"
        )

        if not run:
            return

        # Anyone in the channel may pause an active Survival run.
        # This is intentionally not captain-only.
        run[
            "status"
        ] = "paused"

        run[
            "paused_reason"
        ] = "manually stopped"
        run[
            "last_activity"
        ] = epoch_now()

        update_best(
            team,
            run,
        )

        try:
            save_state(
                self.state,
                push=True,
            )
            clear_lock()
        except Exception as error:
            # Revert local status if persistence failed; otherwise
            # Daily/Random could see an inconsistent run state.
            run[
                "status"
            ] = "active"
            run[
                "paused_reason"
            ] = None

            await message.channel.send(
                f"❌ Could not save the pause to GitHub: `{str(error)[:500]}`"
            )
            return

        await message.channel.send(
            f"⏸️ **{team.get('name', team_key)} Survival paused.**\n"
            f"Puzzle **#{run.get('puzzle_number', 0)}**\n"
            f"Strikes **{run.get('strikes', 0)}/3**\n"
            f"Best difficulty **{run.get('best_difficulty', 0)}**\n\n"
            f"Run saved."
        )


def chess_move_like(
    text
):
    text = text.strip()

    if not text:
        return False

    if len(text) > 12:
        return False

    pattern = re.compile(
        r"^(?:"
        # Normal SAN/UCI-like moves.
        r"[KQRBN]?[a-h]?[1-8]?x?[a-h][1-8][+#]?"
        # Pawn promotions, including captures and check/mate.
        r"|[a-h](?:x[a-h])?[18]=[QRBN][+#]?"
        # Castling.
        r"|O-O-O[+#]?"
        r"|O-O[+#]?"
        r"|0-0-0[+#]?"
        r"|0-0[+#]?"
        r")$",
        re.IGNORECASE,
    )

    return bool(
        pattern.fullmatch(
            text
        )
    )


def parse_survival_move(
    board,
    submitted,
    expected,
):
    """
    Return (accepted, legal_move).

    Normally the move must match the Lichess solution move.
    Exception: if the submitted move is a legal move that immediately
    checkmates, accept it even when Lichess supplied a different mating
    move. This handles puzzles with multiple mate-in-one solutions.
    """
    submitted = submitted.strip()

    if not submitted:
        return False, None

    normalized = (
        submitted.casefold()
    )

    while normalized.endswith(
        ("+", "#")
    ):
        normalized = normalized[:-1]

    for legal_move in board.legal_moves:

        san = board.san(
            legal_move
        )

        san_normalized = san.casefold()

        while san_normalized.endswith(
            ("+", "#")
        ):
            san_normalized = san_normalized[:-1]

        if san_normalized != normalized:
            continue

        # Official solution move.
        if legal_move.uci() == expected["uci"]:
            return True, legal_move

        # Alternative legal move that immediately checkmates.
        test_board = board.copy()
        test_board.push(
            legal_move
        )

        if test_board.is_checkmate():
            return True, legal_move

        return False, None

    # UCI input.
    try:
        move = board.parse_uci(
            normalized
        )
    except Exception:
        return False, None

    if move.uci() == expected["uci"]:
        return True, move

    test_board = board.copy()
    test_board.push(move)

    if test_board.is_checkmate():
        return True, move

    return False, None


def san_matches_move(
    board,
    submitted,
    expected,
):
    submitted = (
        submitted.strip()
    )

    if not submitted:
        return False

    normalized = (
        submitted.casefold()
    )

    while normalized.endswith(
        ("+", "#")
    ):
        normalized = (
            normalized[:-1]
        )

    for legal_move in board.legal_moves:
        san = board.san(
            legal_move
        )

        san_normalized = (
            san.casefold()
        )

        while san_normalized.endswith(
            ("+", "#")
        ):
            san_normalized = (
                san_normalized[:-1]
            )

        if (
            san_normalized
            == normalized
        ):
            return (
                legal_move.uci()
                == expected["uci"]
            )

    try:
        return (
            board.parse_uci(
                normalized
            ).uci()
            == expected["uci"]
        )
    except Exception:
        return False


async def action_limit_timer(
    self
):
    """
    Same runtime model as the working Daily/Random bot:
    stay connected for 5h50m, then close cleanly. The next scheduled
    GitHub Action run restores the saved Survival state and continues
    listening.

    We intentionally do NOT mark an active run as paused here.
    The current run stays active in survival_runs.json.
    """
    await asyncio.sleep(
        RUN_TIME
    )

    print(
        "Ending Survival run cleanly.",
        flush=True,
    )

    await self.close()


SurvivalBot.action_limit_timer = (
    action_limit_timer
)

bot = SurvivalBot()

print(
    "Starting Survival Mode bot...",
    flush=True,
)

bot.run(
    TOKEN
)
