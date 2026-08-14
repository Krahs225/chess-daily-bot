import discord
import os
import requests
import chess
import chess.svg
import chess.pgn
from io import BytesIO, StringIO
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

DAILY_PUZZLE_API = "https://api.chess.com/pub/puzzle"
RANDOM_PUZZLE_API = "https://api.chess.com/pub/puzzle/random"

STATE_FILE = "daily_puzzle_state.json"
LEADERBOARD_FILE = "leaderboard.json"

# Check for a new Daily Puzzle every 5 minutes
PUZZLE_CHECK_INTERVAL = 5 * 60

# Leaderboard once every 24 hours
LEADERBOARD_INTERVAL = 24 * 60 * 60

# Players have 12 hours to answer
ANSWER_WINDOW = 12 * 60 * 60

# Stop GitHub Actions before the 6-hour limit
RUN_TIME = 5 * 60 * 60 + 50 * 60


# =========================================================
# DISCORD
# =========================================================

intents = discord.Intents.default()
intents.message_content = True

client = discord.Client(intents=intents)


# =========================================================
# GLOBAL DATA
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
# GITHUB SAVE
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
            "Daily Puzzle data saved.",
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
# DAILY PUZZLE
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
# RANDOM PUZZLE
# =========================================================

def fetch_random_puzzle():

    response = requests.get(
        RANDOM_PUZZLE_API,
        headers={
            "User-Agent":
                "DailyChessPuzzleBot/1.0"
        },
        timeout=10
    )

    if response.status_code != 200:

        raise RuntimeError(
            f"Chess.com random puzzle returned "
            f"HTTP {response.status_code}"
        )

    data = response.json()

    if not data.get("fen"):

        raise RuntimeError(
            "Random puzzle has no FEN."
        )

    if not data.get("pgn"):

        raise RuntimeError(
            "Random puzzle has no PGN."
        )

    return data


# =========================================================
# SOLUTION
# =========================================================

def get_solution(data):

    # IMPORTANT:
    # chess.pgn.read_game() expects a TEXT stream.
    # Therefore we use StringIO, not BytesIO.
    game = chess.pgn.read_game(
        StringIO(
            data["pgn"]
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

    solution_moves = []

    for move in moves:

        solution_moves.append(
            board.san(move)
        )

        board.push(move)

    return {
        "first_san": first_san,
        "first_uci": first_uci,
        "solution": solution_moves
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

        "first_san":
            solution["first_san"],

        "first_uci":
            solution["first_uci"],

        "solution":
            solution["solution"]
    }


# =========================================================
# BOARD IMAGE
# =========================================================

async def make_board_file(
    puzzle,
    filename
):

    board = chess.Board(
        puzzle["fen"]
    )

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
# POST DAILY PUZZLE
# =========================================================

async def post_puzzle(
    channel,
    puzzle
):

    file, board = await make_board_file(
        puzzle,
        "puzzle.png"
    )

    side = (
        "White"
        if board.turn
        else "Black"
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
# POST RANDOM PUZZLE
# =========================================================

async def post_random_puzzle(
    channel
):

    try:

        # Run the HTTP request outside the Discord
        # event loop so the bot stays responsive.
        data = await asyncio.to_thread(
            fetch_random_puzzle
        )

        puzzle = build_puzzle(
            data
        )

        file, board = await make_board_file(
            puzzle,
            "random_puzzle.png"
        )

        side = (
            "White"
            if board.turn
            else "Black"
        )

        embed = discord.Embed(
            title="🎲 Random Chess Puzzle",
            description=(
                f"**{puzzle['title']}**\n\n"
                f"**{side} to move. "
                f"Find the best move!**"
            ),
            color=0x3498db
        )

        embed.set_image(
            url="attachment://random_puzzle.png"
        )

        await channel.send(
            embed=embed,
            file=file
        )

        print(
            "Random Puzzle posted.",
            flush=True
        )

    except Exception as error:

        print(
            f"Random puzzle error: {error}",
            flush=True
        )

        await channel.send(
            "❌ Could not load a random puzzle "
            "right now. Try again."
        )


# =========================================================
# POST DAILY ANSWER
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
        "💡 **The correct answer is:** "
        f"||{solution_text}||"
    )

    print(
        "Daily Puzzle answer posted.",
        flush=True
    )


# =========================================================
# CHECK MOVE
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

    # SAN
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

    # UCI
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
# PUZZLE OPEN?
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
# SAVE LATEST USER ATTEMPT
# =========================================================

async def save_latest_attempt(
    user,
    move_text,
    correct
):

    async with data_lock:

        puzzle = state.get(
            "current_puzzle"
        )

        if not puzzle:
            return False

        if not puzzle_is_open(
            puzzle
        ):
            return False

        user_id = str(
            user.id
        )

        attempts = puzzle.setdefault(
            "latest_attempts",
            {}
        )

        # Every player has their own latest answer.
        #
        # Example:
        #
        # Sharkmeister -> !Bf2
        # Thice        -> !Bf3
        #
        # Thice's answer does NOT replace
        # Sharkmeister's answer.

        attempts[user_id] = {
            "name":
                user.display_name,

            "move":
                move_text,

            "correct":
                correct,

            "timestamp":
                datetime.now(
                    timezone.utc
                ).isoformat()
        }

        await save_all()

        print(
            f"{user.display_name}: "
            f"{move_text} -> "
            f"{'correct' if correct else 'wrong'}",
            flush=True
        )

        return True


# =========================================================
# FINALIZE POINTS
# =========================================================

async def finalize_puzzle(
    puzzle
):

    async with data_lock:

        attempts = puzzle.get(
            "latest_attempts",
            {}
        )

        already_awarded = puzzle.get(
            "points_awarded",
            []
        )

        for user_id, attempt in attempts.items():

            # Never award the same player twice
            if user_id in already_awarded:
                continue

            # Only their MOST RECENT answer counts
            if not attempt.get(
                "correct",
                False
            ):
                continue

            name = attempt.get(
                "name",
                "Unknown"
            )

            if user_id not in scores:

                scores[user_id] = {
                    "name": name,
                    "points": 0
                }

            scores[user_id]["name"] = name

            scores[user_id]["points"] += 1

            already_awarded.append(
                user_id
            )

            print(
                f"+1 point for {name}",
                flush=True
            )

        puzzle[
            "points_awarded"
        ] = already_awarded

        puzzle[
            "answer_posted"
        ] = True

        await save_all()


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
            item[1].get(
                "points",
                0
            ),
        reverse=True
    )

    lines = [
        "🏆 **Daily Puzzle — Leaderboard**",
        ""
    ]

    for rank, (_, player) in enumerate(
        ordered,
        start=1
    ):

        name = player.get(
            "name",
            "Unknown"
        )

        points = player.get(
            "points",
            0
        )

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
# CHECK FOR NEW DAILY PUZZLE
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

    # Same puzzle -> do nothing
    if current_url == puzzle["url"]:
        return

    print(
        "NEW DAILY PUZZLE DETECTED.",
        flush=True
    )

    # Finish previous puzzle
    if current:

        if not current.get(
            "answer_posted",
            False
        ):

            await finalize_puzzle(
                current
            )

            await post_answer(
                channel,
                current
            )

    # Store new puzzle
    puzzle["posted_at"] = (
        datetime.now(
            timezone.utc
        ).isoformat()
    )

    puzzle[
        "answer_posted"
    ] = False

    puzzle[
        "latest_attempts"
    ] = {}

    puzzle[
        "points_awarded"
    ] = []

    state[
        "current_puzzle"
    ] = puzzle

    await save_all()

    await post_puzzle(
        channel,
        puzzle
    )


# =========================================================
# DAILY PUZZLE LOOP
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

    last_leaderboard = time.monotonic()

    while True:

        try:

            puzzle = state.get(
                "current_puzzle"
            )

            # -----------------------------------------
            # 12-HOUR ANSWER DEADLINE
            # -----------------------------------------

            if puzzle:

                if not puzzle.get(
                    "answer_posted",
                    False
                ):

                    if not puzzle_is_open(
                        puzzle
                    ):

                        print(
                            "12 hours reached. "
                            "Finalizing answers.",
                            flush=True
                        )

                        await finalize_puzzle(
                            puzzle
                        )

                        await post_answer(
                            channel,
                            puzzle
                        )

            # -----------------------------------------
            # DAILY LEADERBOARD
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
        "Ending run cleanly before "
        "GitHub's 6-hour limit.",
        flush=True
    )

    await client.close()


# =========================================================
# DISCORD MESSAGE HANDLER
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

    command = content.lower()

    # =====================================================
    # RANDOM PUZZLE
    # =====================================================

    # All three commands work:
    #
    # !random
    # !randompuzzle
    # !random puzzle

    if command in (
        "!random",
        "!randompuzzle",
        "!random puzzle"
    ):

        await post_random_puzzle(
            message.channel
        )

        return

    # =====================================================
    # DAILY PUZZLE ANSWERS
    # =====================================================

    if not content.startswith("!"):
        return

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

    if not puzzle:
        return

    # After official answer -> ignore
    if puzzle.get(
        "answer_posted",
        False
    ):
        return

    # After 12 hours -> ignore
    if not puzzle_is_open(
        puzzle
    ):
        return

    correct = move_is_correct(
        move_text,
        puzzle
    )

    # Save this user's latest attempt
    await save_latest_attempt(
        message.author,
        move_text,
        correct
    )


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

    # Check Daily Puzzle immediately
    await check_for_new_puzzle(
        channel
    )

    # Continue checking for new Daily Puzzles
    asyncio.create_task(
        puzzle_loop(channel)
    )

    # 12-hour answers + leaderboard
    asyncio.create_task(
        maintenance_loop(channel)
    )

    # Stop before GitHub's 6-hour limit
    asyncio.create_task(
        run_timer()
    )

    print(
        "Daily Puzzle Bot is running.",
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
