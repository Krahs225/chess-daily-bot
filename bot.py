import discord
import os
import requests
import chess
import chess.svg
import chess.pgn
from io import BytesIO
import cairosvg
import asyncio
import json
import subprocess
import re
import time
from datetime import datetime, timezone


# =========================================================
# SETTINGS
# =========================================================

TOKEN = os.getenv("DISCORD_TOKEN")
CHANNEL_ID = 1468320170891022417

PUZZLE_API = "https://api.chess.com/pub/puzzle"

STATE_FILE = "daily_puzzle_state.json"
LEADERBOARD_FILE = "daily_puzzle_leaderboard.json"

# Check Chess.com every 5 minutes
PUZZLE_CHECK_INTERVAL = 5 * 60

# Leaderboard every 10 minutes
LEADERBOARD_INTERVAL = 10 * 60

# Answers accepted for 12 hours
ANSWER_WINDOW = 12 * 60 * 60

# GitHub Action runs for less than 6 hours
RUN_TIME = 5 * 60 * 60 + 50 * 60


# =========================================================
# DISCORD
# =========================================================

intents = discord.Intents.default()
intents.message_content = True

client = discord.Client(intents=intents)


# =========================================================
# DATA
# =========================================================

state = {}
scores = {}

data_lock = asyncio.Lock()


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


# =========================================================
# GITHUB PERSISTENCE
# =========================================================

def push_to_github():

    try:

        subprocess.run(
            [
                "git",
                "config",
                "user.name",
                "Daily Puzzle Bot"
            ],
            check=True,
            capture_output=True
        )

        subprocess.run(
            [
                "git",
                "config",
                "user.email",
                "daily-puzzle-bot@users.noreply.github.com"
            ],
            check=True,
            capture_output=True
        )

        subprocess.run(
            [
                "git",
                "add",
                STATE_FILE,
                LEADERBOARD_FILE
            ],
            check=True,
            capture_output=True
        )

        commit = subprocess.run(
            [
                "git",
                "commit",
                "-m",
                "Update Daily Puzzle data"
            ],
            capture_output=True,
            text=True
        )

        # Nothing changed
        if commit.returncode != 0:
            return

        branch = os.getenv(
            "GITHUB_REF_NAME",
            "main"
        )

        subprocess.run(
            [
                "git",
                "push",
                "origin",
                f"HEAD:{branch}"
            ],
            check=True,
            capture_output=True,
            text=True
        )

        print(
            "Saved Daily Puzzle data to GitHub.",
            flush=True
        )

    except Exception as error:

        print(
            f"Could not push data to GitHub: {error}",
            flush=True
        )


async def save_all():

    save_json(
        STATE_FILE,
        state
    )

    save_json(
        LEADERBOARD_FILE,
        scores
    )

    await asyncio.to_thread(
        push_to_github
    )


# =========================================================
# CHESS.COM DAILY PUZZLE
# =========================================================

def fetch_daily_puzzle():

    response = requests.get(
        PUZZLE_API,
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
# PUZZLE SOLUTION
# =========================================================

def get_solution(data):

    game = chess.pgn.read_game(
        BytesIO(
            data["pgn"].encode("utf-8")
        )
    )

    if game is None:
        raise RuntimeError(
            "Could not read puzzle PGN."
        )

    board = chess.Board(
        data["fen"]
    )

    moves = list(
        game.mainline_moves()
    )

    if not moves:
        raise RuntimeError(
            "Puzzle has no solution."
        )

    first_move = moves[0]

    first_san = board.san(
        first_move
    )

    first_uci = first_move.uci()

    full_solution = []

    for move in moves:

        full_solution.append(
            board.san(move)
        )

        board.push(move)

    return {
        "first_san": first_san,
        "first_uci": first_uci,
        "solution": full_solution
    }


def build_puzzle(data):

    solution = get_solution(
        data
    )

    return {
        "url": data.get("url"),
        "title": data.get(
            "title",
            "Daily Chess Puzzle"
        ),
        "fen": data["fen"],
        "pgn": data["pgn"],
        "first_san": solution["first_san"],
        "first_uci": solution["first_uci"],
        "solution": solution["solution"]
    }


# =========================================================
# POST PUZZLE
# =========================================================

async def post_puzzle(
    channel,
    puzzle
):

    board = chess.Board(
        puzzle["fen"]
    )

    side = (
        "White"
        if board.turn
        else "Black"
    )

    # Keep the same board orientation
    # as your existing working bot.
    orientation = (
        chess.WHITE
        if board.turn
        else chess.BLACK
    )

    svg_board = chess.svg.board(
        board=board,
        orientation=orientation,
        size=500,
        coordinates=True
    )

    png_bytes = cairosvg.svg2png(
        bytestring=svg_board.encode(
            "utf-8"
        )
    )

    image = BytesIO(
        png_bytes
    )

    file = discord.File(
        fp=image,
        filename="puzzle.png"
    )

    embed = discord.Embed(
        title="♟️ Daily Chess Puzzle",
        description=(
            f"**{puzzle['title']}**\n\n"
            f"**{side} to move. "
            f"Find the best move!**"
        ),
        color=0x2ecc71
    )

    embed.set_image(
        url="attachment://puzzle.png"
    )

    await channel.send(
        embed=embed,
        file=file
    )

    print(
        "Daily Puzzle posted.",
        flush=True
    )


# =========================================================
# POST ANSWER
# =========================================================

async def post_answer(
    channel,
    puzzle
):

    solution = puzzle.get(
        "solution",
        []
    )

    if not solution:
        return

    solution_text = " ".join(
        solution
    )

    await channel.send(
        "♟️ **Daily Chess Puzzle — Answer**\n\n"
        f"**First move:** `{puzzle['first_san']}`\n\n"
        f"**Full solution:** `{solution_text}`"
    )

    print(
        "Daily Puzzle answer posted.",
        flush=True
    )


# =========================================================
# MOVE CHECK
# =========================================================

def move_is_correct(
    text,
    puzzle
):

    text = text.strip()

    if not text:
        return False

    board = chess.Board(
        puzzle["fen"]
    )

    # SAN:
    # Bf2
    # Bf2+
    # Qh7#
    try:

        move = board.parse_san(
            text
        )

        return (
            move.uci()
            == puzzle["first_uci"]
        )

    except ValueError:
        pass

    # UCI:
    # e2e4
    try:

        move = board.parse_uci(
            text
        )

        return (
            move.uci()
            == puzzle["first_uci"]
        )

    except ValueError:

        return False


# =========================================================
# PUZZLE STILL OPEN?
# =========================================================

def puzzle_is_open(puzzle):

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

    return elapsed < ANSWER_WINDOW


# =========================================================
# ADD POINT
# =========================================================

async def add_point(
    user
):

    async with data_lock:

        puzzle = state.get(
            "current_puzzle"
        )

        # No active Daily Puzzle
        if not puzzle:
            return False

        # Answer already posted
        if puzzle.get(
            "answer_posted",
            False
        ):
            return False

        # 12 hours have passed
        if not puzzle_is_open(
            puzzle
        ):
            return False

        user_id = str(
            user.id
        )

        scored_users = puzzle.setdefault(
            "scored_users",
            []
        )

        # Already got a point for this puzzle
        if user_id in scored_users:
            return False

        if user_id not in scores:

            scores[user_id] = {
                "name": user.display_name,
                "points": 0
            }

        # Keep current Discord display name
        scores[user_id]["name"] = (
            user.display_name
        )

        scores[user_id]["points"] += 1

        scored_users.append(
            user_id
        )

        await save_all()

        print(
            f"+1 Daily Puzzle point: "
            f"{user.display_name}",
            flush=True
        )

        return True


# =========================================================
# DISCORD ANSWERS
# =========================================================

@client.event
async def on_message(
    message
):

    if message.author.bot:
        return

    if message.channel.id != CHANNEL_ID:
        return

    content = message.content.strip()

    if not content.startswith("!"):
        return

    # Only !MOVE
    match = re.fullmatch(
        r"!\s*(\S+)",
        content
    )

    if not match:
        return

    move_text = match.group(1)

    puzzle = state.get(
        "current_puzzle"
    )

    # Nothing active
    if not puzzle:
        return

    # IMPORTANT:
    # After the official answer, stop completely.
    if puzzle.get(
        "answer_posted",
        False
    ):
        return

    # IMPORTANT:
    # After 12 hours, stop completely.
    if not puzzle_is_open(
        puzzle
    ):
        return

    # Wrong move = nothing
    if not move_is_correct(
        move_text,
        puzzle
    ):
        return

    awarded = await add_point(
        message.author
    )

    if awarded:

        await message.channel.send(
            f"✅ **{message.author.display_name} "
            f"+1 Daily Puzzle point!**"
        )


# =========================================================
# LEADERBOARD
# =========================================================

def make_leaderboard():

    if not scores:

        return (
            "🏆 **Daily Puzzle — Leaderboard**\n\n"
            "No points yet!"
        )

    ordered = sorted(
        scores.items(),
        key=lambda item:
            item[1]["points"],
        reverse=True
    )

    lines = [
        "🏆 **Daily Puzzle — Leaderboard**",
        ""
    ]

    # Keep all players for now.
    # We can change this to Top 10 later.
    for rank, (_, player) in enumerate(
        ordered,
        start=1
    ):

        name = player["name"]
        points = player["points"]

        if rank == 1:
            prefix = "🥇"
        elif rank == 2:
            prefix = "🥈"
        elif rank == 3:
            prefix = "🥉"
        else:
            prefix = f"**{rank}.**"

        word = (
            "point"
            if points == 1
            else "points"
        )

        lines.append(
            f"{prefix} {name} — "
            f"**{points} {word}**"
        )

    return "\n".join(lines)


# =========================================================
# FIND NEW DAILY PUZZLE
# =========================================================

async def check_for_new_puzzle(
    channel
):

    try:

        data = await asyncio.to_thread(
            fetch_daily_puzzle
        )

        puzzle = build_puzzle(
            data
        )

    except Exception as error:

        print(
            f"Could not load Daily Puzzle: "
            f"{error}",
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

    # Same puzzle
    if current_url == puzzle["url"]:
        return

    print(
        "NEW DAILY PUZZLE DETECTED.",
        flush=True
    )

    # Old puzzle answer
    if current:

        if not current.get(
            "answer_posted",
            False
        ):

            try:

                await post_answer(
                    channel,
                    current
                )

            except Exception as error:

                print(
                    f"Could not post old answer: "
                    f"{error}",
                    flush=True
                )

    # New puzzle starts now
    puzzle["posted_at"] = (
        datetime.now(
            timezone.utc
        ).isoformat()
    )

    puzzle["answer_posted"] = False
    puzzle["scored_users"] = []

    state["current_puzzle"] = puzzle

    await save_all()

    await post_puzzle(
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
# ANSWER + LEADERBOARD LOOP
# =========================================================

async def maintenance_loop(
    channel
):

    last_leaderboard = time.monotonic()

    while True:

        try:

            puzzle = state.get(
                "current_puzzle"
            )

            # -----------------------------------------
            # 12-HOUR ANSWER
            # -----------------------------------------

            if puzzle:

                if not puzzle.get(
                    "answer_posted",
                    False
                ):

                    if not puzzle_is_open(
                        puzzle
                    ):

                        await post_answer(
                            channel,
                            puzzle
                        )

                        puzzle[
                            "answer_posted"
                        ] = True

                        await save_all()

                        print(
                            "12-hour answer window closed.",
                            flush=True
                        )

            # -----------------------------------------
            # LEADERBOARD
            # -----------------------------------------

            if (
                time.monotonic()
                - last_leaderboard
                >= LEADERBOARD_INTERVAL
            ):

                await channel.send(
                    make_leaderboard()
                )

                last_leaderboard = (
                    time.monotonic()
                )

                print(
                    "Leaderboard posted.",
                    flush=True
                )

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
        "Run time reached. "
        "Ending cleanly for the next GitHub run.",
        flush=True
    )

    await client.close()


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
    global scores

    state = load_json(
        STATE_FILE,
        {}
    )

    scores = load_json(
        LEADERBOARD_FILE,
        {}
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

    # Immediately check whether
    # there is a new Daily Puzzle.
    await check_for_new_puzzle(
        channel
    )

    asyncio.create_task(
        puzzle_loop(channel)
    )

    asyncio.create_task(
        maintenance_loop(channel)
    )

    asyncio.create_task(
        run_timer()
    )

    print(
        "Daily Puzzle system is running.",
        flush=True
    )


# =========================================================
# START
# =========================================================

print(
    "Starting Daily Chess Puzzle Bot...",
    flush=True
)

client.run(TOKEN)
