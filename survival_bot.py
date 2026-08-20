import asyncio
import io
import json
import os
import random
import re
import subprocess
import time
import traceback
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

LICHESS_BATCH_URL = (
    "https://lichess.org/api/puzzle/batch/mix"
)

REQUEST_TIMEOUT = 20
BATCH_SIZE = 50

INACTIVITY_SECONDS = 10 * 60
PENDING_TEAM_SECONDS = 60

THREE_STRIKES = 3


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
            return True

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
        },
    )

    team["name"] = display_name

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


def fetch_lichess_batch():
    response = requests.get(
        LICHESS_BATCH_URL,
        params={
            "nb":
                BATCH_SIZE,
        },
        headers={
            "Accept":
                "application/json",
            "User-Agent":
                "Discord-Survival-Mode/1.0",
        },
        timeout=REQUEST_TIMEOUT,
    )

    response.raise_for_status()

    try:
        data = response.json()
    except ValueError:
        # Some clients/proxies may return one JSON object per line.
        items = []

        for line in response.text.splitlines():
            line = line.strip()

            if not line:
                continue

            try:
                items.append(
                    json.loads(
                        line
                    )
                )
            except Exception:
                continue

        return items

    if isinstance(
        data,
        dict,
    ):
        for key in (
            "puzzles",
            "data",
            "items",
        ):
            value = data.get(
                key
            )

            if isinstance(
                value,
                list,
            ):
                return value

        return [
            data
        ]

    if isinstance(
        data,
        list,
    ):
        return data

    return []


def choose_puzzle_for_number(
    puzzle_number,
    used_ids,
):
    minimum, maximum = difficulty_target(
        puzzle_number
    )

    for _attempt in range(12):
        items = awaitable_fetch_batch()
        candidates = []

        for raw in items:
            puzzle = sanitize_puzzle(raw)
            if not puzzle or puzzle["id"] in used_ids:
                continue
            if minimum <= puzzle["rating"] <= maximum:
                candidates.append(puzzle)

        if candidates:
            return random.choice(candidates)

    if minimum >= 2600:
        raise RuntimeError(
            "Could not find a fresh 2600+ Survival puzzle right now."
        )

    raise RuntimeError(
        f"Could not find a Survival puzzle in the required "
        f"{minimum}-{maximum} rating band right now."
    )


def awaitable_fetch_batch():
    return fetch_lichess_batch()


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

    return (
        f"🔥 **{team} — Survival**\n"
        f"Status: **{status}**\n"
        f"Puzzle: **#{run.get('puzzle_number', 0)}**\n"
        f"Strikes: **{run.get('strikes', 0)}/{THREE_STRIKES}**\n"
        f"Best difficulty: **{best_difficulty}**"
    )


def survival_leaderboard(
    state
):
    rows = []

    for team_key, team in (
        state["teams"].items()
    ):
        rows.append(
            (
                team.get(
                    "name",
                    team_key,
                ),
                int(
                    team.get(
                        "best_puzzle",
                        0,
                    )
                ),
                int(
                    team.get(
                        "best_difficulty",
                        0,
                    )
                ),
            )
        )

    rows.sort(
        key=lambda row: (
            -row[1],
            -row[2],
            row[0].casefold(),
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

    for rank, (
        name,
        best,
        difficulty,
    ) in enumerate(
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

        lines.append(
            f"{prefix} **{name}** — "
            f"Puzzle **#{best}** "
            f"(best difficulty **{difficulty}**)"
        )

    return "\n".join(
        lines
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
            timeout=60
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
        if interaction.user.id != (
            self.requester_id
        ):
            await interaction.response.send_message(
                "Only the person who requested this "
                "Survival can choose.",
                ephemeral=True,
            )
            return False

        return True

    @discord.ui.button(
        label="Continue",
        style=discord.ButtonStyle.success,
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

    async def setup_hook(
        self
    ):
        self.bg_task = asyncio.create_task(
            self.maintenance_loop()
        )
        self.action_task = asyncio.create_task(
            self.action_limit_timer()
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

        run = {
            "run_id":
                f"{team_key}:{int(time.time())}",
            "started_by_id":
                str(requester.id),
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

        if run.get(
            "puzzle"
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
                "survival-first",
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
                "survival-helper",
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

    async def handle_survival_move(
        self,
        message,
        move_text,
        run,
    ):
        if not run:
            return

        user = message.author

        run[
            "last_activity"
        ] = epoch_now()

        ensure_member(
            run,
            user,
        )

        puzzle = run.get(
            "puzzle"
        )

        if not puzzle:
            return

        submitted = move_text.strip()

        if len(
            submitted.split()
        ) != 1:
            await message.channel.send(
                f"❌ **One move at a time, {user.display_name}.**"
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

        board = chess.Board(
            puzzle["current_fen"]
        )

        expected = solution[
            next_index
        ]

        correct = san_matches_move(
            board,
            submitted,
            expected,
        )

        user_id = str(
            user.id
        )

        if not correct:

            wrong_users = run.setdefault(
                "puzzle",
                {}
            ).setdefault(
                "wrong_users",
                []
            )

            # A single person cannot accidentally burn all three hearts
            # by spamming wrong guesses on the same position.
            if user_id in wrong_users:
                await message.channel.send(
                    f"❌ **That miss is already counted, {user.display_name}.**"
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

            if run["strikes"] >= THREE_STRIKES:

                run[
                    "status"
                ] = "paused"

                run[
                    "paused_reason"
                ] = "three strikes"

                # Keep best values on the actual team below.
                team_key = None
                for key, team in (
                    self.state["teams"].items()
                ):
                    if team.get(
                        "current"
                    ) is run:
                        team_key = key
                        update_best(
                            team,
                            run,
                        )
                        break

                save_state(
                    self.state,
                    push=True,
                )

                clear_lock()

                await message.channel.send(
                    f"💀 **SURVIVAL OVER — "
                    f"{self.state['teams'][team_key].get('name', team_key)}**\n"
                    f"Reached **Puzzle #{run.get('puzzle_number', 0)}**\n"
                    f"Three strikes.\n"
                    f"Best difficulty: **{run.get('best_difficulty', 0)}**\n\n"
                    f"Run saved."
                )

                return

            await message.channel.send(
                f"❌ **Wrong! Strike {run['strikes']}/{THREE_STRIKES}.**\n"
                f"❤️ "
                f"{max(0, THREE_STRIKES - run['strikes'])}"
            )

            write_lock(
                active_team() or "Survival",
                run.get(
                    "run_id",
                ),
                run["last_activity"],
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

        # Record first solver / helper candidates for this puzzle.
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

        move = chess.Move.from_uci(
            expected["uci"]
        )

        if move not in board.legal_moves:
            return

        board.push(
            move
        )

        # Automatically play all opponent moves until it is the player's
        # turn again or the solution ends.
        next_index += 1
        opponent_replies = []

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

        puzzle[
            "wrong_users"
        ] = puzzle.get(
            "wrong_users",
            []
        )

        run[
            "last_activity"
        ] = epoch_now()

        # Persist the move BEFORE awarding shared leaderboard points.
        # The shared leaderboard helper may reset the git working tree
        # while resolving concurrent pushes.
        save_state(
            self.state,
            push=True,
        )

        if next_index >= len(
            solution
        ):
            team_key = None

            for key, team in (
                self.state["teams"].items()
            ):
                if team.get(
                    "current"
                ) is run:
                    team_key = key
                    break

            if team_key is None:
                return

            # Puzzle fully solved: now award individual shared points.
            await self.score_completed_puzzle(
                run
            )

            run[
                "puzzle"
            ] = None

            # Puzzle number is the number just completed.
            completed_number = int(
                run["puzzle_number"]
            )

            run[
                "puzzle_number"
            ] = completed_number + 1

            # Save state after the scoring transactions.
            save_state(
                self.state,
                push=True,
            )

            member_summary = (
                f"✅ **Puzzle #{completed_number} solved!**"
            )

            if opponent_replies:
                member_summary += (
                    "\n"
                    f"↩️ Opponent: "
                    f"{' '.join(opponent_replies)}"
                )

            await message.channel.send(
                member_summary
                + "\n"
                + f"Next up: **Puzzle #{run['puzzle_number']}**."
            )

            await self.post_next_puzzle(
                message.channel,
                team_key,
            )

            return

        # More moves remain.
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
                f"{self._team_name_for_run(run)}**"
            ),
            description=(
                f"✅ **{expected['san']}**\n"
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
            active_team() or self._team_name_for_run(run),
            run.get(
                "run_id",
            ),
            run["last_activity"],
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

        if lower == "!stopsurvival":
            await self.stop_survival(
                message
            )
            return

        if lower == "!survival":
            self.pending_team = {
                "user_id":
                    message.author.id,
                "channel_id":
                    message.channel.id,
                "expires":
                    epoch_now()
                    + PENDING_TEAM_SECONDS,
            }

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
        pending = self.pending_team

        if (
            pending
            and epoch_now()
            <= pending["expires"]
            and message.author.id
            == pending["user_id"]
            and message.channel.id
            == pending["channel_id"]
            and not content.startswith("!")
        ):

            self.pending_team = None

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

                await message.channel.send(
                    f"♻️ **{team.get('name', team_name)}** "
                    "has a saved Survival run.\n\n"
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
        team = self.state["teams"][
            team_key
        ]

        current = team.get(
            "current"
        )

        members_text = (
            "No contributors recorded yet."
        )

        if current:
            members = list(
                current.get(
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
                    item.get(
                        "name",
                        "",
                    ).casefold(),
                )
            )

            lines = []

            for index, member in enumerate(
                members,
                start=1,
            ):
                lines.append(
                    f"**{index}.** "
                    f"{member.get('name', 'Unknown')} — "
                    f"**{member.get('correct', 0)} correct** "
                    f"/ {member.get('wrong', 0)} wrong"
                )

            if lines:
                members_text = "\n".join(
                    lines
                )

        lines = [
            f"👥 **{team.get('name', team_key)}**",
            "",
            f"**Best run:** Puzzle "
            f"**#{team.get('best_puzzle', 0)}**",
            f"**Best difficulty:** "
            f"**{team.get('best_difficulty', 0)}**",
        ]

        if current:
            lines.extend(
                [
                    "",
                    f"**Current status:** "
                    f"{current.get('status', 'paused')}",
                    f"**Current puzzle:** "
                    f"#{current.get('puzzle_number', 0)}",
                    f"**Strikes:** "
                    f"{current.get('strikes', 0)}/3",
                    "",
                    "**Contributors:**",
                    members_text,
                ]
            )

        await message.channel.send(
            "\n".join(lines)
        )

    async def stop_survival(
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

        user_id = str(
            message.author.id
        )

        if user_id not in run.get(
            "members",
            {}
        ):
            await message.channel.send(
                "Only someone who has participated "
                "in this Survival run can stop it."
            )
            return

        run[
            "status"
        ] = "paused"

        run[
            "paused_reason"
        ] = (
            "manually stopped"
        )

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
        r"[KQRBN]?[a-h]?[1-8]?x?[a-h][1-8][+#]?"
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


async def stop_for_action_limit(self):
    active = active_current_run(self.state)

    if not active:
        return

    team_key, team = active
    run = team.get("current")

    if not run:
        return

    run["status"] = "paused"
    run["paused_reason"] = "GitHub Actions run limit"
    update_best(team, run)
    save_state(self.state, push=True)
    clear_lock()

    channel = self.get_channel(CHANNEL_ID)
    if channel:
        await channel.send(
            f"⏸️ **{team.get('name', team_key)} Survival paused automatically.**\n"
            f"Puzzle **#{run.get('puzzle_number', 0)}** saved.\n"
            "The GitHub run is ending; the team can resume later with `!survival`."
        )


async def action_limit_timer(self):
    await asyncio.sleep(
        5 * 60 * 60 + 45 * 60
    )
    try:
        await self.stop_for_action_limit()
    except Exception as error:
        print(
            f"Action-limit pause error: {error}",
            flush=True,
        )


SurvivalBot.stop_for_action_limit = stop_for_action_limit
SurvivalBot.action_limit_timer = action_limit_timer

bot = SurvivalBot()

print(
    "Starting Survival Mode bot...",
    flush=True,
)

bot.run(
    TOKEN
)
