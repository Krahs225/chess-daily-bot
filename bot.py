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
LEADERBOARD_FILE = "daily_puzzle_leaderboard.json"

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
# FIND PUZZLE SOLUTION
# =========================================================

def get_solution(data):

    """
    Chess.com gives us:
        - FEN = position where the puzzle starts
        - PGN = game containing the moves

    We replay the PGN until we reach the FEN.
    Everything after that position is the puzzle line.

    This is important because the PGN can contain
    the complete original game, not just the puzzle moves.
    """

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

    # Find the exact position from which
    # the Chess.com puzzle starts.
    for index, move in enumerate(
        mainline_moves
    ):

        if board.board_fen() == target_board.board_fen():

            if (
                board.turn
                == target_board.turn
            ):

                if (
                    board.castling_rights
                    == target_board.castling_rights
                ):

                    if (
                        board.ep_square
                        == target_board.ep_square
                    ):

                        start_index = index
                        break

        board.push(move)

    # In case the puzzle position is
    # the final position in some unusual PGN.
    if start_index is None:

        if (
            board.board_fen()
            == target_board.board_fen()
        ):

            start_index = len(
                mainline_moves
            )

    if start_index is None:

        raise RuntimeError(
            "Could not find puzzle FEN "
            "inside the PGN."
        )

    # Rebuild the board at the puzzle position.
    board = chess.Board(
        data["fen"]
    )

    solution_moves = []

    for move in mainline_moves[
        start_index:
    ]:

        # Make sure this move is legal
        # from the puzzle position.
        if move not in board.legal_moves:
            break

        solution_moves.append(
            {
                "uci":
                    move.uci(),

                "san":
                    board.san(move)
            }
        )

        board.push(move)

    if not solution_moves:

        raise RuntimeError(
            "Puzzle has no solution moves."
        )

    return solution_moves


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

        "solution":
            solution,

        "solution_length":
            len(solution),

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
# MOVE TEXT
# =========================================================

def move_word(count):

    if count == 1:
        return "move"

    return "moves"


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
        "solution_length"
    ]

    embed = discord.Embed(
        title="♟️ Daily Chess Puzzle",
        description=(
            f"**{puzzle['title']}**\n\n"
            f"**{side} to move. "
            f"Find the best line in "
            f"{count} {move_word(count)}.**"
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
        f"({count} moves).",
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
            "solution_length"
        ]

        embed = discord.Embed(
            title=(
                f"🎲 Random Puzzle — "
                f"{puzzle['title']}"
            ),
            description=(
                f"**{side} to move. "
                f"Find the best line in "
                f"{count} {move_word(count)}.**"
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
            f"({count} moves).",
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
    Makes SAN case-insensitive.

    Also makes + and # optional.

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

    # + and # should not matter.
    while (
        text.endswith("+")
        or text.endswith("#")
    ):

        text = text[:-1]

    return text


# =========================================================
# CHECK ONE MOVE
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

    # Check every legal move and compare
    # normalized SAN.
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

    # UCI support too.
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
# CHECK FULL SOLUTION
# =========================================================

def solution_is_correct(
    submitted_text,
    puzzle
):

    """
    The user must provide the COMPLETE line.

    Example solution:

        Bf2 Qxf2+ Kh1

    Then:

        !Bf2 Qxf2 Kh1      -> correct
        !bf2 qxf2 kh1      -> correct
        !Bf2 Qxf2+ Kh1    -> correct

    But:

        !Bf2               -> not enough
        !Bf2 Qxf2          -> not enough
        !Bf2 Qh4           -> wrong
    """

    if not submitted_text:
        return False

    # Split into separate moves.
    submitted_moves = (
        submitted_text.strip().split()
    )

    solution = puzzle.get(
        "solution",
        []
    )

    # Must have EXACTLY the same number
    # of moves as the puzzle solution.
    if len(submitted_moves) != len(
        solution
    ):

        return False

    board = chess.Board(
        puzzle["fen"]
    )

    for submitted, expected in zip(
        submitted_moves,
        solution
    ):

        if not san_matches_move(
            board,
            submitted,
            expected
        ):

            return False

        # Push the expected move.
        move = chess.Move.from_uci(
            expected["uci"]
        )

        board.push(move)

    return True


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

    user_id = str(
        user_id
    )

    if user_id not in scores:

        scores[user_id] = {
            "name":
                "Unknown",

            "points":
                0
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

    players = []

    for player_id, player in scores.items():

        players.append(
            {
                "id":
                    str(player_id),

                "name":
                    player.get(
                        "name",
                        "Unknown"
                    ),

                "points":
                    player.get(
                        "points",
                        0
                    )
            }
        )

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
                "rank":
                    index + 1,

                "name":
                    player["name"],

                "points":
                    player["points"],

                "is_you":
                    player["id"] == user_id
            }
        )

    return result


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

        if player["is_you"]:

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
# AWARD FIRST POINT ONLY
# =========================================================

async def award_point(
    puzzle,
    user
):

    """
    Only the FIRST correct person gets +1.

    This is protected by data_lock so two
    nearly simultaneous correct answers
    cannot both receive the point.
    """

    user_id = str(
        user.id
    )

    async with data_lock:

        # Someone already won.
        if puzzle.get(
            "winner_user_id"
        ) is not None:

            return False

        puzzle[
            "winner_user_id"
        ] = user_id

        puzzle[
            "winner_name"
        ] = user.display_name

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

        scores[user_id][
            "points"
        ] = (
            scores[user_id].get(
                "points",
                0
            ) + 1
        )

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

    return True


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
# HELP
# =========================================================

def help_message():

    return """🧠 **Chess Puzzle Game**

**Daily Puzzle**
`!daily <moves>` — Answer the latest Daily Puzzle.

**Random Puzzle**
`!random` or `!rp` — Get a random chess puzzle.
`!random <moves>` — Answer the latest Random Puzzle.

**Quick Answer**
`!<moves>` — Answer whichever puzzle was posted most recently.

**Points**
Correct answers are worth **+1 point**.
Only the **first complete correct answer** gets the point.

**Other**
`!help` or `!info` — Show this message.

🏆 The leaderboard is posted automatically every 10 minutes.
"""


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
        move["san"]
        for move in solution
    )

    if puzzle_type == "daily":

        title = "💡 **Daily Puzzle — Answer**"

    else:

        title = "💡 **Random Puzzle — Answer**"

    await channel.send(
        f"{title}\n\n"
        f"**The correct line is:** "
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

    # Check the COMPLETE line.
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

    # -----------------------------------------------------
    # WRONG / INCOMPLETE
    # -----------------------------------------------------

    if not correct:

        required = puzzle.get(
            "solution_length",
            1
        )

        await message.channel.send(
            f"❌ **Wrong, "
            f"{message.author.display_name}.**\n"
            f"You need to give the complete "
            f"**{required} "
            f"{move_word(required)}**."
        )

        return

    # -----------------------------------------------------
    # CORRECT
    # -----------------------------------------------------

    got_point = await award_point(
        puzzle,
        message.author
    )

    current_points = get_player_score(
        message.author.id
    )

    personal_ranking = (
        build_personal_ranking(
            message.author.id
        )
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

    await message.channel.send(
        response
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
        "!rp",
        "!randompuzzle"
    ):

        await post_random_puzzle(
            message.channel
        )

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
    # !Bf2 Qxf2 Kh1
    # !bf2 qxf2 kh1
    # =====================================================

    move_text = content[1:].strip()

    if not move_text:
        return

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

    # Check Daily immediately.
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
