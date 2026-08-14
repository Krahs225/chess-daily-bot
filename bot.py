import discord
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
import traceback
import random
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

    headers = {
        "User-Agent":
            "DailyChessPuzzleBot/2.0",
        "Accept":
            "application/json"
    }

    last_error = None

    # Chess.com documents this endpoint as the random daily
    # puzzle endpoint. Retry a few times because transient 429/5xx
    # responses can happen, and the endpoint itself may be cached.
    for attempt in range(1, 4):

        try:
            response = requests.get(
                RANDOM_PUZZLE_API,
                headers=headers,
                timeout=15
            )

            if response.status_code == 200:
                data = response.json()

                if not data.get("fen"):
                    raise RuntimeError(
                        "Random puzzle response has no FEN."
                    )

                if not data.get("pgn"):
                    raise RuntimeError(
                        "Random puzzle response has no PGN."
                    )

                return data

            retry_after = response.headers.get(
                "Retry-After"
            )

            body_preview = response.text.strip().replace("\n", " ")
            if len(body_preview) > 300:
                body_preview = body_preview[:300] + "..."

            last_error = (
                f"HTTP {response.status_code}"
                + (
                    f" (Retry-After {retry_after}s)"
                    if retry_after else ""
                )
                + (
                    f" | Response: {body_preview}"
                    if body_preview else ""
                )
            )

            if response.status_code == 429 and retry_after:
                try:
                    time.sleep(
                        min(float(retry_after), 5.0)
                    )
                except ValueError:
                    time.sleep(2)
            else:
                time.sleep(1.5)

        except Exception as error:
            last_error = str(error)
            if attempt < 3:
                time.sleep(1.5)

    raise RuntimeError(
        f"Could not fetch random puzzle after 3 attempts: {last_error}"
    )


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

        if count == 1:
            move_description = (
                "Find the best move."
            )
        else:
            move_description = (
                f"Find the best line in "
                f"**{count} {move_word(count)}**."
            )

        embed = discord.Embed(
            title=(
                f"🎲 Random Puzzle — {title}"
            ),
            description=(
                f"**{side} to move.**\n"
                f"{move_description}\n\n"
                f"You only enter **your own moves**. "
                f"The opponent's replies will be played automatically."
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

        # Persist the message ID locally.
        # Do NOT run the GitHub push immediately after sending.
        # The random puzzle is already successfully posted, and
        # the extra push was causing the command to report an
        # error after the message had appeared.
        save_json(
            STATE_FILE,
            state
        )

        print(
            f"Random Puzzle posted "
            f"({count} player moves).",
            flush=True
        )

        # IMPORTANT:
        # A successful Discord post is a successful random-puzzle
        # command. Do not turn a post-send persistence issue into
        # a visible "Random Puzzle Error" message.
        return

    except Exception as error:
        print(
            "RANDOM PUZZLE ERROR:",
            flush=True
        )
        traceback.print_exc()

        # Show the real error in Discord so a failed request or
        # PGN/FEN parsing problem can be diagnosed immediately.
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
                f"{format_points(points)} "
                f"{'point' if float(points) == 1 else 'points'} ← you**"
            )

        else:

            lines.append(
                f"#{rank} {name} — "
                f"{format_points(points)} "
                f"{'point' if float(points) == 1 else 'points'}"
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
# RANDOM PUZZLE SCORING
# =========================================================

async def award_random_move_points(
    puzzle,
    user,
    first_move
):
    """
    Random puzzle scoring:
    - First correct player move: +1.0, once.
    - Later correct move by a different player: +0.5, once.
    - The first mover can never also receive the helper 0.5.
    - Each helper can receive at most 0.5 on this puzzle.
    """

    user_id = str(user.id)
    score_kind = "none"

    async with data_lock:

        puzzle.setdefault(
            "first_move_user_id",
            None
        )
        puzzle.setdefault(
            "first_move_user_name",
            None
        )
        puzzle.setdefault(
            "first_move_awarded",
            False
        )
        puzzle.setdefault(
            "helper_awarded_users",
            []
        )

        # First player move.
        if first_move:

            if puzzle["first_move_user_id"] is None:

                puzzle[
                    "first_move_user_id"
                ] = user_id

                puzzle[
                    "first_move_user_name"
                ] = user.display_name

                puzzle[
                    "first_move_awarded"
                ] = True

                if user_id not in scores:
                    scores[user_id] = {
                        "name": user.display_name,
                        "points": 0
                    }

                scores[user_id]["name"] = (
                    user.display_name
                )

                scores[user_id]["points"] = round(
                    float(
                        scores[user_id].get(
                            "points",
                            0
                        )
                    ) + 1.0,
                    2
                )

                score_kind = "first"

        # Helper move.
        else:

            first_user_id = puzzle.get(
                "first_move_user_id"
            )

            helper_users = puzzle[
                "helper_awarded_users"
            ]

            if (
                user_id != first_user_id
                and user_id not in helper_users
            ):

                helper_users.append(
                    user_id
                )

                if user_id not in scores:
                    scores[user_id] = {
                        "name": user.display_name,
                        "points": 0
                    }

                scores[user_id]["name"] = (
                    user.display_name
                )

                scores[user_id]["points"] = round(
                    float(
                        scores[user_id].get(
                            "points",
                            0
                        )
                    ) + 0.5,
                    2
                )

                score_kind = "helper"

        if score_kind != "none":

            save_json(
                STATE_FILE,
                state
            )

            save_json(
                LEADERBOARD_FILE,
                scores
            )

    if score_kind != "none":
        await asyncio.to_thread(
            push_to_github
        )

    return score_kind


# =========================================================
# LEGACY DAILY SCORING
# =========================================================

async def award_point(
    puzzle,
    user
):
    """
    Daily puzzle keeps its existing +1 first-correct system.
    """

    user_id = str(
        user.id
    )

    async with data_lock:

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
        ] = round(
            float(
                scores[user_id].get(
                    "points",
                    0
                )
            ) + 1.0,
            2
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
            if float(points) == 1
            else "points"
        )

        lines.append(
            f"{prefix} {name} — "
            f"**{format_points(points)} {word}**"
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
`!random`, `!rp` or `!r` — Get a random chess puzzle.
`!random <move>` — Make the next move in the latest Random Puzzle.

**Quick Answer**
`!<moves>` — Answer whichever puzzle was posted most recently.

Only your own moves are required. The opponent's replies are automatically played between your moves.

**Points**
• First correct move in a Random Puzzle: **+1 point**
• Correct later move that helps: **+0.5 point**
• First-move player can never earn more than **+1**
• A helper can never earn more than **+0.5** per puzzle

**Other**
`!help` or `!info` — Show this message.
`!leaderboard`, `!lb` or `!l` — Show the full leaderboard.

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
# FUN WRONG-ANSWER MESSAGES
# =========================================================

def wrong_message(user):
    name = user.display_name
    lower = name.casefold()

    if lower == "thice":
        thice_lines = [
            f"❌ **Wrong again, {name}.**",
            f"❌ **Nope, {name}.**",
            f"❌ **Not that one, {name}.**",
            f"❌ **Still wrong, {name}.**",
            f"❌ **Absolutely not, {name}.**",
            f"❌ **That ain't it, {name}.**",
            f"❌ **Wrong move, {name}.**",
            f"❌ **Nice try, {name}.**",
            f"❌ **No chance, {name}.**",
            f"❌ **Try again, {name}.**",
            f"❌ **The board says no, {name}.**",
            f"❌ **Incorrect, {name}.**",
            f"❌ **Another miss, {name}.**",
            f"❌ **Not even close, {name}.**",
            f"❌ **So wrong, {name}.**",
            f"❌ **That was brave, {name}.**",
            f"❌ **Bold choice, {name}.**",
            f"❌ **The pieces disagree, {name}.**",
            f"❌ **The king says no, {name}.**",
            f"❌ **The engine says no, {name}.**",
            f"❌ **Chess says no, {name}.**",
            f"❌ **That move is cursed, {name}.**",
            f"❌ **Please reconsider, {name}.**",
            f"❌ **The puzzle rejects that, {name}.**",
            f"❌ **That was not the plan, {name}.**",
            f"❌ **Wrong direction, {name}.**",
            f"❌ **Wrong idea, {name}.**",
            f"❌ **Wrong square, {name}.**",
            f"❌ **Wrong again, obviously, {name}.**",
            f"❌ **The answer is elsewhere, {name}.**",
            f"❌ **The tactics disagree, {name}.**",
            f"❌ **The board remains undefeated, {name}.**",
            f"❌ **That move had issues, {name}.**",
            f"❌ **That was not the one, {name}.**",
            f"❌ **Nope, try another, {name}.**",
            f"❌ **You found the anti-move, {name}.**",
            f"❌ **The position is unimpressed, {name}.**",
            f"❌ **That move did not cook, {name}.**",
            f"❌ **The pieces are disappointed, {name}.**",
            f"❌ **The puzzle is laughing, {name}.**",
            f"❌ **Still not it, {name}.**",
            f"❌ **Another tactical disaster, {name}.**",
            f"❌ **That was aggressively wrong, {name}.**",
            f"❌ **The knight saw it coming, {name}.**",
            f"❌ **The bishop disagrees, {name}.**",
            f"❌ **The rook is judging, {name}.**",
            f"❌ **The king is concerned, {name}.**",
            f"❌ **The engine facepalms, {name}.**",
            f"❌ **That move belongs nowhere, {name}.**",
        ]
        return random.choice(thice_lines)

    if "sharkmeister" in lower:
        shark_lines = [
            f"❌ **So close, {name}... you almost had it there.**",
            f"❌ **Almost, {name}. You were right on the edge.**",
            f"❌ **So close, {name}. One tiny detail off.**",
            f"❌ **Nearly, {name}. The idea was there.**",
            f"❌ **Oof, {name}. That was almost it.**",
        ]
        return random.choice(shark_lines)

    return f"❌ **Wrong, {name}.**"


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

    if not puzzle_is_open(
        puzzle,
        RANDOM_ANSWER_WINDOW
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

    if not correct:
        await save_all()
        await message.channel.send(
            wrong_message(message.author)
        )
        return

    # -----------------------------------------------------
    # SCORE THIS CORRECT PLAYER MOVE
    # -----------------------------------------------------

    score_kind = await award_random_move_points(
        puzzle,
        message.author,
        first_move=move_was_first
    )

    # -----------------------------------------------------
    # PUZZLE COMPLETE
    # -----------------------------------------------------

    if next_player_index >= len(player_moves):
        puzzle["solved"] = True

        got_point = await award_point(
            puzzle,
            message.author
        )

        points = get_player_score(
            message.author.id
        )

        ranking = build_personal_ranking(
            message.author.id
        )

        # The embed is ONLY for the board/progress.
        # Points and ranking are sent as a separate message.
        embed_progress = (
            f"🎉 **Puzzle solved!**"
        )

        if opponent_replies:
            embed_progress += (
                "\n"
                f"↩️ **Opponent:** "
                f"{' '.join(opponent_replies)}"
            )

        await save_all()

        await update_random_puzzle_message(
            message.channel,
            puzzle,
            embed_progress
        )

        if score_kind == "first":
            score_message = (
                f"✅ **Correct, {message.author.display_name}!**\n"
                f"🎉 **Puzzle solved!**\n"
                f"**+1 point** — you now have "
                f"**{format_points(points)} points.**"
            )

        elif score_kind == "helper":
            score_message = (
                f"✅ **Correct, {message.author.display_name}!**\n"
                f"🎉 **Puzzle solved!**\n"
                f"**+0.5 point** for helping — "
                f"you now have "
                f"**{format_points(points)} points.**"
            )

        else:
            score_message = (
                f"✅ **Correct, {message.author.display_name}!**\n"
                f"🎉 **Puzzle solved!**\n"
                f"No additional points this move.\n"
                f"You have **{format_points(points)} points.**"
            )

        # Score is a separate message; the puzzle embed never contains points.
        await message.channel.send(score_message)

        # Small personal leaderboard: one above, you, one below.
        if ranking:
            await message.channel.send(ranking)

        await post_answer(
            message.channel,
            puzzle,
            "random"
        )

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
            "**Correct! Now make your final move.**"
        )
    else:
        progress = (
            f"**Correct! {remaining} "
            f"{move_word(remaining)} remaining.**"
        )

    await save_all()

    await update_random_puzzle_message(
        message.channel,
        puzzle,
        progress
        + (
            f"\n{reply_text}"
            if reply_text
            else ""
        )
    )

    # Points are always a separate message, never part of the embed.
    if score_kind in ("first", "helper"):

        current_points = get_player_score(
            message.author.id
        )

        if score_kind == "first":
            score_text = (
                f"✅ **{message.author.display_name} "
                f"found the first move!**\n"
                f"**+1 point** — you now have "
                f"**{format_points(current_points)} points.**"
            )

        else:
            score_text = (
                f"🤝 **{message.author.display_name} "
                f"helped solve the puzzle!**\n"
                f"**+0.5 point** — you now have "
                f"**{format_points(current_points)} points.**"
            )

        await message.channel.send(
            score_text
        )

        ranking = build_personal_ranking(
            message.author.id
        )

        if ranking:
            await message.channel.send(
                ranking
            )


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

    # Random puzzles are solved interactively:
    # one user move -> automatic opponent reply -> next user move.
    if str(
        puzzle.get("puzzle_id", "")
    ).startswith("random_"):
        await handle_random_answer(
            message,
            puzzle,
            move_text
        )
        return

    # Daily puzzle: user submits the complete sequence of THEIR moves.
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

    personal_ranking = build_personal_ranking(
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
    # FULL LEADERBOARD
    # =====================================================

    if command_lower in ("!leaderboard", "!lb", "!l"):
        await message.channel.send(
            make_leaderboard()
        )
        return

    # =====================================================
    # RANDOM PUZZLE
    # =====================================================

    if command_lower in (
        "!random",
        "!rp",
        "!r",
        "!randompuzzle"
    ):

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
