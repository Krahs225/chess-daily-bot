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

# Keep the existing files so existing scores remain.
STATE_FILE = "daily_puzzle_state.json"
LEADERBOARD_FILE = "daily_puzzle_leaderboard.json"

# Check Chess.com for a new Daily Puzzle
PUZZLE_CHECK_INTERVAL = 5 * 60

# Full leaderboard every 10 minutes
LEADERBOARD_INTERVAL = 10 * 60

# Answer window
ANSWER_WINDOW = 12 * 60 * 60
RANDOM_ANSWER_WINDOW = 12 * 60 * 60

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
            "Data saved to GitHub.",
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
# PARSE PUZZLE
# =========================================================

def get_solution(data):

    # python-chess expects a TEXT stream.
    # This fixes the previous BytesIO error.
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
            "Chess Puzzle"
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
        url="attachment://daily_puzzle.png"
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

        puzzle[
            "answer_posted"
        ] = False

        puzzle[
            "latest_attempts"
        ] = {}

        puzzle[
            "points_awarded"
        ] = []

        # Every random puzzle gets its own ID.
        puzzle[
            "puzzle_id"
        ] = (
            "random_"
            + str(
                int(
                    time.time() * 1000
                )
            )
        )

        state[
            "latest_random_puzzle"
        ] = puzzle

        # This is now the most recently posted puzzle.
        state[
            "latest_puzzle_type"
        ] = "random"

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
# POST ANSWER
# =========================================================

async def post_answer(
    channel,
    puzzle,
    puzzle_type
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

    if puzzle_type == "daily":

        title = "💡 **Daily Puzzle — Answer**"

    else:

        title = "💡 **Random Puzzle — Answer**"

    await channel.send(
        f"{title}\n\n"
        f"**The correct answer is:** "
        f"||{solution_text}||"
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
# GET SCORE
# =========================================================

def get_player_score(
    user_id
):

    user_id = str(
        user_id
    )

    if user_id not in scores:

        scores[user_id] = {
            "name": "Unknown",
            "points": 0
        }

    return scores[user_id].get(
        "points",
        0
    )


# =========================================================
# PERSONAL RANKING
# =========================================================

def get_personal_ranking(
    user_id
):

    user_id = str(
        user_id
    )

    # Make sure every score has usable data.
    players = []

    for player_id, player in scores.items():

        players.append(
            {
                "id": str(player_id),

                "name": player.get(
                    "name",
                    "Unknown"
                ),

                "points": player.get(
                    "points",
                    0
                )
            }
        )

    # Highest points first.
    players.sort(
        key=lambda player: (
            -player["points"],
            player["name"].lower()
        )
    )

    player_index = None

    for index, player in enumerate(
        players
    ):

        if player["id"] == user_id:

            player_index = index
            break

    # If somehow not found.
    if player_index is None:

        return []

    start = max(
        0,
        player_index - 1
    )

    end = min(
        len(players),
        player_index + 2
    )

    result = []

    for index in range(
        start,
        end
    ):

        player = players[index]

        result.append(
            {
                "rank": index + 1,
                "name": player["name"],
                "points": player["points"],
                "is_you":
                    player["id"] == user_id
            }
        )

    return result


# =========================================================
# PERSONAL RANKING MESSAGE
# =========================================================

def build_personal_ranking(
    user_id
):

    ranking = get_personal_ranking(
        user_id
    )

    if not ranking:
        return ""

    lines = [
        "",
        "📊 **Your ranking**"
    ]

    for player in ranking:

        rank = player["rank"]
        name = player["name"]
        points = player["points"]
        is_you = player["is_you"]

        if is_you:

            lines.append(
                f"**#{rank} {name} — "
                f"{points} points ← you**"
            )

        else:

            lines.append(
                f"#{rank} {name} — "
                f"{points} points"
            )

    return "\n".join(lines)


# =========================================================
# AWARD / REMOVE POINT BASED ON LATEST ANSWER
# =========================================================

async def update_player_score_for_attempt(
    puzzle,
    user,
    correct
):

    user_id = str(
        user.id
    )

    old_attempts = puzzle.setdefault(
        "latest_attempts",
        {}
    )

    old_attempt = old_attempts.get(
        user_id
    )

    old_correct = (
        old_attempt.get(
            "correct",
            False
        )
        if old_attempt
        else False
    )

    # Update latest attempt.
    old_attempts[user_id] = {
        "name":
            user.display_name,

        "move":
            old_attempt.get(
                "move",
                ""
            )
            if old_attempt
            else "",

        "correct":
            correct,

        "timestamp":
            datetime.now(
                timezone.utc
            ).isoformat()
    }

    # Keep the name and move updated by caller later.
    old_attempts[user_id][
        "name"
    ] = user.display_name

    # Determine score change.
    score_change = 0

    if not old_correct and correct:

        # Wrong -> Correct
        score_change = 1

    elif old_correct and not correct:

        # Correct -> Wrong
        score_change = -1

    # Ensure score object exists.
    if user_id not in scores:

        scores[user_id] = {
            "name":
                user.display_name,

            "points":
                0
        }

    scores[user_id][
        "name"
    ] = user.display_name

    if score_change != 0:

        scores[user_id][
            "points"
        ] = max(
            0,
            scores[user_id].get(
                "points",
                0
            ) + score_change
        )

    return score_change


# =========================================================
# SAVE ANSWER
# =========================================================

async def save_attempt_and_update_score(
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

        previous = attempts.get(
            user_id
        )

        previous_correct = (
            previous.get(
                "correct",
                False
            )
            if previous
            else False
        )

        # Store newest answer.
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

        # Create player if needed.
        if user_id not in scores:

            scores[user_id] = {
                "name":
                    user.display_name,

                "points":
                    0
            }

        scores[user_id][
            "name"
        ] = user.display_name

        # Determine point change.
        point_change = 0

        if not previous_correct and correct:

            # New correct answer
            point_change = 1

        elif previous_correct and not correct:

            # Player changed their latest answer
            # from correct to wrong.
            point_change = -1

        if point_change != 0:

            scores[user_id][
                "points"
            ] = max(
                0,
                scores[user_id].get(
                    "points",
                    0
                ) + point_change
            )

        # Save copies AFTER changing data.
        save_json(
            STATE_FILE,
            state
        )

        save_json(
            LEADERBOARD_FILE,
            scores
        )

    # Push outside the lock.
    await asyncio.to_thread(
        push_to_github
    )

    return point_change


# =========================================================
# FULL LEADERBOARD
# =========================================================

def make_leaderboard():

    if not scores:

        return (
            "🏆 **Leaderboard**\n\n"
            "No points yet!"
        )

    ordered = sorted(
        scores.items(),
        key=lambda item: (
            -item[1].get(
                "points",
                0
            ),
            item[1].get(
                "name",
                "Unknown"
            ).lower()
        )
    )

    lines = [
        "🏆 **Leaderboard**",
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
# HELP / INFO
# =========================================================

def help_message():

    return """🧠 **Chess Puzzle Game**

**Daily Puzzle**
`!dailyzet <move>` — Answer the latest Daily Puzzle.

**Random Puzzle**
`!randompuzzle` — Get a random chess puzzle.
`!randomzet <move>` — Answer the latest Random Puzzle.

**Quick Answer**
`!zet <move>` — Answer whichever puzzle was posted most recently.

**Points**
Correct answers are worth **+1 point**.
Only your **most recent answer** to a puzzle counts.

**Other**
`!help` or `!info` — Show this message.

🏆 The leaderboard is posted automatically every 10 minutes.
"""


# =========================================================
# FINALIZE EXPIRED PUZZLE
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

    # Points are already updated immediately
    # when answers are submitted.
    puzzle[
        "answer_posted"
    ] = True

    save_json(
        STATE_FILE,
        state
    )

    await asyncio.to_thread(
        push_to_github
    )

    await post_answer(
        channel,
        puzzle,
        puzzle_type
    )


# =========================================================
# CHECK EXPIRED PUZZLES
# =========================================================

async def check_expired_puzzles(
    channel
):

    # -----------------------------
    # DAILY
    # -----------------------------

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

    # -----------------------------
    # RANDOM
    # -----------------------------

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
# CHECK NEW DAILY PUZZLE
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

    # Same Daily Puzzle -> nothing to do.
    if current_url == puzzle["url"]:
        return

    print(
        "NEW DAILY PUZZLE DETECTED.",
        flush=True
    )

    # Finish previous Daily Puzzle.
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
        "answer_posted"
    ] = False

    puzzle[
        "latest_attempts"
    ] = {}

    puzzle[
        "points_awarded"
    ] = []

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

    state[
        "current_puzzle"
    ] = puzzle

    # Daily is now the most recent puzzle.
    state[
        "latest_puzzle_type"
    ] = "daily"

    await save_all()

    await post_daily_puzzle(
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

            # Check expired puzzles.
            await check_expired_puzzles(
                channel
            )

            # Full leaderboard every 10 minutes.
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
                    "Full leaderboard posted.",
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
# ANSWER HANDLER
# =========================================================

async def handle_answer(
    message,
    puzzle,
    puzzle_type,
    answer_window,
    move_text
):

    if not puzzle:
        return

    # Answer already revealed.
    if puzzle.get(
        "answer_posted",
        False
    ):
        return

    # Answer window expired.
    if not puzzle_is_open(
        puzzle,
        answer_window
    ):
        return

    correct = move_is_correct(
        move_text,
        puzzle
    )

    point_change = await save_attempt_and_update_score(
        puzzle,
        message.author,
        move_text,
        correct
    )

    if not correct:

        await message.channel.send(
            f"❌ **Wrong, "
            f"{message.author.display_name}.**"
        )

        return

    # Current score after the answer.
    user_id = str(
        message.author.id
    )

    current_points = get_player_score(
        user_id
    )

    # Build the small personal leaderboard.
    personal_ranking = (
        build_personal_ranking(
            user_id
        )
    )

    if point_change > 0:

        point_text = (
            f"+{point_change} point"
            if point_change == 1
            else f"+{point_change} points"
        )

        message_text = (
            f"✅ **Correct, "
            f"{message.author.display_name}!**\n"
            f"{point_text} — you now have "
            f"**{current_points} points**."
        )

    elif point_change == 0:

        # This means the player was already
        # on a correct latest answer.
        message_text = (
            f"✅ **Correct, "
            f"{message.author.display_name}!**\n"
            f"You have **{current_points} points**."
        )

    else:

        message_text = (
            f"✅ **Correct, "
            f"{message.author.display_name}!**\n"
            f"You now have **{current_points} points**."
        )

    await message.channel.send(
        message_text
        + personal_ranking
    )


# =========================================================
# MESSAGE HANDLER
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

    command_lower = content.lower()

    # =====================================================
    # HELP / INFO
    # =====================================================

    if command_lower in (
        "!help",
        "!info"
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
        "!randompuzzle",
        "!random puzzle"
    ):

        await post_random_puzzle(
            message.channel
        )

        return

    # =====================================================
    # ANSWER COMMANDS
    # =====================================================

    parts = content.split(
        maxsplit=1
    )

    command = parts[0].lower()

    if len(parts) < 2:
        return

    move_text = parts[1].strip()

    if not move_text:
        return

    # =====================================================
    # SELECT PUZZLE
    # =====================================================

    puzzle = None
    puzzle_type = None
    answer_window = None

    # -----------------------------------------
    # DAILY PUZZLE
    # -----------------------------------------

    if command == "!dailyzet":

        puzzle = state.get(
            "current_puzzle"
        )

        puzzle_type = "daily"

        answer_window = ANSWER_WINDOW

    # -----------------------------------------
    # RANDOM PUZZLE
    # -----------------------------------------

    elif command == "!randomzet":

        puzzle = state.get(
            "latest_random_puzzle"
        )

        puzzle_type = "random"

        answer_window = RANDOM_ANSWER_WINDOW

    # -----------------------------------------
    # MOST RECENT PUZZLE
    # -----------------------------------------

    elif command == "!zet":

        latest_type = state.get(
            "latest_puzzle_type"
        )

        if latest_type == "random":

            puzzle = state.get(
                "latest_random_puzzle"
            )

            puzzle_type = "random"

            answer_window = RANDOM_ANSWER_WINDOW

        elif latest_type == "daily":

            puzzle = state.get(
                "current_puzzle"
            )

            puzzle_type = "daily"

            answer_window = ANSWER_WINDOW

        else:

            return

    else:

        return

    # =====================================================
    # HANDLE ANSWER
    # =====================================================

    await handle_answer(
        message,
        puzzle,
        puzzle_type,
        answer_window,
        move_text
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

    # Immediately check Daily Puzzle.
    await check_for_new_puzzle(
        channel
    )

    # Keep checking for new Daily Puzzles.
    asyncio.create_task(
        puzzle_loop(channel)
    )

    # Answers + leaderboard.
    asyncio.create_task(
        maintenance_loop(channel)
    )

    # Stop before GitHub Actions limit.
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
