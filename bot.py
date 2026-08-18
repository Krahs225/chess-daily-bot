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

from shared_leaderboard import (
    add_points,
    full_leaderboard,
    personal_ranking,
)


# =========================================================
# SETTINGS
# =========================================================

TOKEN = os.getenv("DISCORD_TOKEN")
CHANNEL_ID = 1468320170891022417

DAILY_PUZZLE_API = "https://api.chess.com/pub/puzzle"
RANDOM_PUZZLE_API = "https://api.chess.com/pub/puzzle/random"

STATE_FILE = "daily_puzzle_state.json"
LEGACY_LEADERBOARD_FILE = "daily_puzzle_leaderboard.json"

PUZZLE_CHECK_INTERVAL = 5 * 60
LEADERBOARD_INTERVAL = 10 * 60

ANSWER_WINDOW = 12 * 60 * 60
RANDOM_ANSWER_WINDOW = 12 * 60 * 60

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
                STATE_FILE
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

    # Puzzle state is persisted here.
    # Scores are stored only in shared_leaderboard.py.
    save_json(
        STATE_FILE,
        state
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
# PARSE PUZZLE SOLUTION
# =========================================================

def get_solution(data):

    game = chess.pgn.read_game(
        StringIO(data["pgn"])
    )

    if game is None:

        raise RuntimeError(
            "Could not read puzzle PGN."
        )

    target_board = chess.Board(
        data["fen"]
    )

    board = game.board()

    mainline_moves = list(
        game.mainline_moves()
    )

    start_index = None

    # Find the puzzle's starting position
    # inside the complete PGN.
    for index, move in enumerate(
        mainline_moves
    ):

        if (
            board.board_fen()
            == target_board.board_fen()
            and board.turn
            == target_board.turn
            and board.castling_rights
            == target_board.castling_rights
            and board.ep_square
            == target_board.ep_square
        ):

            start_index = index
            break

        board.push(move)

    if start_index is None:

        if (
            board.board_fen()
            == target_board.board_fen()
            and board.turn
            == target_board.turn
        ):

            start_index = len(
                mainline_moves
            )

    if start_index is None:

        raise RuntimeError(
            "Could not find puzzle FEN "
            "inside PGN."
        )

    board = chess.Board(
        data["fen"]
    )

    solution = []

    for move in mainline_moves[
        start_index:
    ]:

        if move not in board.legal_moves:
            break

        solution.append(
            {
                "uci":
                    move.uci(),

                "san":
                    board.san(move),

                # This is the side that makes
                # this move.
                "color":
                    "white"
                    if board.turn
                    else "black"
            }
        )

        board.push(move)

    if not solution:

        raise RuntimeError(
            "Puzzle has no solution moves."
        )

    # The side to move in the puzzle FEN
    # is the side the user must play.
    player_color = (
        "white"
        if target_board.turn
        else "black"
    )

    # IMPORTANT:
    #
    # Count ONLY the moves belonging to the
    # player who is solving the puzzle.
    #
    # The opponent's moves are still stored,
    # because the bot needs to automatically
    # play them between the user's moves.
    player_moves = [
        move
        for move in solution
        if move["color"] == player_color
    ]

    return {
        "all_moves":
            solution,

        "player_moves":
            player_moves,

        "player_color":
            player_color,

        "player_move_count":
            len(player_moves),

        "first_uci":
            solution[0]["uci"]
    }


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

        puzzle["puzzle_id"] = (
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

        count = puzzle[
            "player_move_count"
        ]

        title = puzzle[
            "title"
        ]

        embed = discord.Embed(
            title=(
                f"🎲 Random Puzzle — {title}"
            ),
            description=(
                f"**{side} to move.**\n"
                f"Find the best line in "
                f"**{count} {move_word(count)}**."
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
            f"Random Puzzle posted "
            f"({count} player moves).",
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

    board = chess.Board(
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
    # Compatibility helper. The actual score source is the shared ledger.
    from shared_leaderboard import get_score

    return get_score(
        user_id
    )



# =========================================================
# PERSONAL RANKING
# =========================================================

def build_personal_ranking(
    user_id
):
    return personal_ranking(
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
# FIRST CORRECT ANSWER GETS POINT
# =========================================================

async def award_point(
    puzzle,
    user
):
    user_id = str(
        user.id
    )

    puzzle_id = str(
        puzzle.get(
            "puzzle_id",
            puzzle.get(
                "url",
                "unknown-puzzle"
            )
        )
    )

    transaction_id = (
        f"daily:{puzzle_id}:winner:{user_id}"
    )

    async with data_lock:

        if puzzle.get(
            "winner_user_id"
        ) is not None:
            return False, get_player_score(
                user.id
            )

        # Record the point in the append-only shared ledger FIRST.
        # The transaction_id makes retries idempotent.
        total = await asyncio.to_thread(
            add_points,
            user.id,
            user.display_name,
            1,
            transaction_id,
            "daily-puzzle",
        )

        puzzle[
            "winner_user_id"
        ] = user_id

        puzzle[
            "winner_name"
        ] = user.display_name

        save_json(
            STATE_FILE,
            state
        )

        await asyncio.to_thread(
            push_to_github
        )

        return True, total



# =========================================================
# FULL LEADERBOARD
# =========================================================

def make_leaderboard():
    return full_leaderboard(
        "🏆 **Shared Leaderboard**"
    )



# =========================================================
# HELP
# =========================================================

def help_message():
    return """🧠 **Chess Puzzle Game**

**Daily Puzzle**
`Kh1` or `!Kh1` — answer the latest Daily Puzzle.

**Random Puzzle**
`rp` or `!rp` — get a random chess puzzle.
Then answer with moves as `Kh1` or `!Kh1`.

**Quick Answer**
You can enter moves with or without `!`.
The `!` is optional for moves only.

**Commands**
`!random`, `!randompuzzle` — new random puzzle.
`!leaderboard`, `!lb`, `!l` — shared leaderboard.
`!help` or `!info` — show this message.

Only your own moves are required. The opponent's moves are automatically played.

**Points**
The first complete correct answer gets **+1 point**.
Daily uses the same shared leaderboard as the two Chatter games.
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
        f"**Your moves:** "
        f"||{solution_text}||"
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

    last_leaderboard = time.monotonic()

    while True:

        try:

            await check_expired_puzzles(
                channel
            )

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
# HANDLE ANSWER
# =========================================================

async def handle_answer(
    message,
    puzzle,
    answer_window,
    move_text
):

    if not puzzle:
        return

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

    # Give a useful response if they didn't
    # provide enough of their own moves.
    if len(submitted_moves) != required:

        await message.channel.send(
            f"❌ **Not quite, "
            f"{message.author.display_name}.**\n"
            f"This puzzle requires "
            f"**{required} "
            f"{move_word(required)}** "
            f"from your side."
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

    # =====================================================
    # WRONG
    # =====================================================

    if not correct:

        wrong_replies = [
            (
                f"❌ **Not quite, "
                f"{message.author.display_name}.** "
                "The board has respectfully rejected that move."
            ),
            (
                f"❌ **Nope, "
                f"{message.author.display_name}.** "
                "The pieces have seen enough."
            ),
            (
                f"❌ **Wrong line, "
                f"{message.author.display_name}.** "
                "Your knight would like to speak to management."
            ),
            (
                f"❌ **Close, "
                f"{message.author.display_name}.** "
                "Unfortunately the chess gods said no."
            ),
            (
                f"❌ **That ain't it, "
                f"{message.author.display_name}.** "
                "The engine has filed a complaint."
            ),
        ]

        await message.channel.send(
            random.choice(
                wrong_replies
            )
        )

        return

    # =====================================================
    # CORRECT
    # =====================================================

    got_point, current_points = await award_point(
        puzzle,
        message.author
    )

    if got_point:
        await message.channel.send(
            f"🎉 **{message.author.display_name} +1**"
        )

    else:
        await message.channel.send(
            f"✅ **Correct, "
            f"{message.author.display_name}!** "
            "Someone else got the point first."
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

    if not content:
        return

    command_lower = content.casefold()

    # -------------------------
    # ! commands stay !-based
    # -------------------------
    if command_lower in (
        "!help",
        "!info",
    ):

        await message.channel.send(
            help_message()
        )

        return

    if command_lower in (
        "!leaderboard",
        "!lb",
        "!l",
    ):

        await message.channel.send(
            make_leaderboard()
        )

        return

    # -------------------------
    # Random puzzle:
    # both rp and !rp work.
    # -------------------------
    if command_lower in (
        "rp",
        "!rp",
        "random",
        "!random",
        "randompuzzle",
        "!randompuzzle",
    ):

        await post_random_puzzle(
            message.channel
        )

        return

    if command_lower.startswith(
        "!random "
    ):

        move_text = content[
            len("!random "):
        ].strip()

        if move_text:
            puzzle = state.get(
                "latest_random_puzzle"
            )

            await handle_answer(
                message,
                puzzle,
                RANDOM_ANSWER_WINDOW,
                move_text,
            )

        return

    if command_lower.startswith(
        "random "
    ):

        move_text = content[
            len("random "):
        ].strip()

        if move_text:
            puzzle = state.get(
                "latest_random_puzzle"
            )

            await handle_answer(
                message,
                puzzle,
                RANDOM_ANSWER_WINDOW,
                move_text,
            )

        return

    # -------------------------
    # Daily prefix commands:
    # !daily moves and daily moves
    # remain available.
    # -------------------------
    if command_lower.startswith(
        "!daily "
    ):

        move_text = content[
            len("!daily "):
        ].strip()

        if move_text:
            puzzle = state.get(
                "current_puzzle"
            )

            await handle_answer(
                message,
                puzzle,
                ANSWER_WINDOW,
                move_text,
            )

        return

    if command_lower.startswith(
        "daily "
    ):

        move_text = content[
            len("daily "):
        ].strip()

        if move_text:
            puzzle = state.get(
                "current_puzzle"
            )

            await handle_answer(
                message,
                puzzle,
                ANSWER_WINDOW,
                move_text,
            )

        return

    # -------------------------
    # Moves can now be submitted
    # with OR without !.
    # We only treat a plain message as
    # a move when it resembles chess SAN/UCI.
    # -------------------------
    move_text = content[1:].strip() if content.startswith("!") else content

    looks_like_move = bool(
        re.fullmatch(
            r"(?:[KQRBN]?[a-h]?[1-8]?(?:x[a-h][1-8])?[a-h][1-8][+#]?|"
            r"[a-h][1-8](?:[+#])?)(?:\s+"
            r"(?:[KQRBN]?[a-h]?[1-8]?(?:x[a-h][1-8])?[a-h][1-8][+#]?|"
            r"[a-h][1-8](?:[+#])?))*",
            move_text,
            flags=re.IGNORECASE,
        )
    )

    if not looks_like_move:
        return

    latest_type = state.get(
        "latest_puzzle_type"
    )

    if latest_type == "random":
        puzzle = state.get(
            "latest_random_puzzle"
        )
        answer_window = RANDOM_ANSWER_WINDOW

    elif latest_type == "daily":
        puzzle = state.get(
            "current_puzzle"
        )
        answer_window = ANSWER_WINDOW

    else:
        return

    await handle_answer(
        message,
        puzzle,
        answer_window,
        move_text,
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

    # Legacy local scores are deliberately ignored.
    # Shared leaderboard is the only score authority.
    scores = {}

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
