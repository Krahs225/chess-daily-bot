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
import chess.pgn
import requests
import discord
from discord.ext import commands

from shop_catalog import (
    SURVIVAL_HEART_COST, BOARD_THEMES, PIECE_SETS,
    ARROW_COLORS, DEFAULT_ARROW_COLOR,
)
from puzzle_stats import record_puzzle_attempt

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
    get_coins,
    get_cosmetic_profile,
    spend_coins,
    credit_coins,
    personal_ranking,
)


TOKEN = os.getenv(
    "DISCORD_TOKEN"
)

CHANNEL_ID = 1468320170891022417

SURVIVAL_STATE_FILE = "survival_runs.json"

# Fast path: official Lichess puzzle batch API. It returns random puzzles,
# but only supports difficulty relative to a 1500 anonymous puzzle rating,
# not an exact min/max band. Survival therefore filters the returned ratings
# locally and falls back to the exact-range Dataset Viewer when necessary.
LICHESS_BATCH_URL = (
    "https://lichess.org/api/puzzle/batch/mix"
)
LICHESS_BATCH_SIZE = 8
LICHESS_API_COOLDOWN_SECONDS = 60
_lichess_api_cooldown_until = 0.0

# Exact-range fallback: official CC0 Lichess/chess-puzzles collection on
# Hugging Face. The old code picked an offset from all 6M rows AFTER applying
# a rating filter, which made most offsets invalid. We now first read the
# filtered row count and randomize only inside the matching rows.
HF_FILTER_URL = (
    "https://datasets-server.huggingface.co/filter"
)
HF_DATASET = "Lichess/chess-puzzles"
HF_CONFIG = "default"
HF_SPLIT = "train"
HF_FILTER_COUNT_CACHE = {}

REQUEST_TIMEOUT = 20
BATCH_SIZE = 25

INACTIVITY_SECONDS = 10 * 60
PENDING_TEAM_SECONDS = 60

RUN_TIME = 5 * 60 * 60 + 50 * 60

THREE_STRIKES = 3
SHARKMEISTER_DEFAULT_USER_ID = "362606514764251137"


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



def load_remote_runs():
    """Read the latest persisted Survival state without mutating the worktree."""
    branch = git_branch()
    fetch = git_run([
        "git", "fetch", "origin",
        f"+refs/heads/{branch}:refs/remotes/origin/{branch}",
    ])
    if fetch.returncode != 0:
        return None
    result = git_run([
        "git", "show", f"origin/{branch}:{SURVIVAL_STATE_FILE}",
    ])
    if result.returncode != 0:
        return None
    try:
        data = json.loads(result.stdout)
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    data.setdefault("teams", {})
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


def lichess_difficulty_for_band(
    minimum_rating,
    maximum_rating,
):
    """
    Lichess anonymous puzzle difficulty is relative to 1500:
    easiest -600, easier -300, normal 0, harder +300, hardest +600.
    Pick the closest official difficulty to the middle of our band.
    """
    effective_maximum = (
        3000
        if maximum_rating >= 9999
        else maximum_rating
    )

    target = (
        int(minimum_rating)
        + int(effective_maximum)
    ) / 2

    choices = [
        ("easiest", 900),
        ("easier", 1200),
        ("normal", 1500),
        ("harder", 1800),
        ("hardest", 2100),
    ]

    # On an exact tie, prefer the harder option. That makes the
    # 1850-2050 band lean toward 2100 instead of 1800.
    return min(
        choices,
        key=lambda item: (
            abs(item[1] - target),
            -item[1],
        ),
    )[0]


def sanitize_lichess_api_puzzle(
    item,
):
    """
    Convert the official Lichess puzzle API response into the same source
    shape the existing Survival runtime already understands:

      fen   = position BEFORE the opponent's first puzzle move
      moves = [opponent first move] + solution

    The official API gives game.pgn through that opponent move and gives the
    remaining solution separately, so reconstructing this shape is lossless.
    """
    if not isinstance(
        item,
        dict,
    ):
        return None

    puzzle = item.get(
        "puzzle"
    )
    game_data = item.get(
        "game"
    )

    if not isinstance(
        puzzle,
        dict,
    ):
        return None

    if not isinstance(
        game_data,
        dict,
    ):
        return None

    puzzle_id = puzzle.get(
        "id"
    )
    rating = puzzle.get(
        "rating"
    )
    solution = puzzle.get(
        "solution"
    )
    themes = puzzle.get(
        "themes",
        [],
    )
    game_pgn = game_data.get(
        "pgn"
    )

    if not puzzle_id or rating is None:
        return None

    if not isinstance(
        solution,
        list,
    ) or not solution:
        return None

    if not game_pgn:
        return None

    try:
        rating = int(
            rating
        )

        parsed_game = chess.pgn.read_game(
            io.StringIO(
                str(game_pgn)
            )
        )

        if parsed_game is None:
            return None

        game_moves = list(
            parsed_game.mainline_moves()
        )

        if not game_moves:
            return None

        board = parsed_game.board()

        for move in game_moves[:-1]:
            if move not in board.legal_moves:
                return None
            board.push(
                move
            )

        first_move = game_moves[-1]

        if first_move not in board.legal_moves:
            return None

        source_fen = board.fen()
        all_moves = [
            first_move.uci()
        ] + [
            str(uci)
            for uci in solution
        ]

        # Validate the API solution against the reconstructed position.
        check_board = board.copy()
        for uci in all_moves:
            move = chess.Move.from_uci(
                uci
            )
            if move not in check_board.legal_moves:
                return None
            check_board.push(
                move
            )

    except Exception:
        return None

    return {
        "id": str(puzzle_id),
        "fen": source_fen,
        "moves": all_moves,
        "rating": rating,
        "themes": (
            " ".join(themes)
            if isinstance(themes, list)
            else str(themes or "")
        ),
        "url": (
            f"https://lichess.org/training/"
            f"{puzzle_id}"
        ),
    }


def fetch_lichess_api_batch(
    minimum_rating,
    maximum_rating,
):
    """
    Fast live random source from Lichess itself.

    We keep the batch small because anonymous batch cost scales with `nb`.
    The caller still checks the actual puzzle rating; difficulty only makes
    matching our Survival band much more likely.
    """
    global _lichess_api_cooldown_until

    if time.monotonic() < _lichess_api_cooldown_until:
        return []

    difficulty = lichess_difficulty_for_band(
        minimum_rating,
        maximum_rating,
    )

    try:
        response = requests.get(
            LICHESS_BATCH_URL,
            params={
                "difficulty": difficulty,
                "nb": LICHESS_BATCH_SIZE,
            },
            headers={
                "Accept": "application/json",
                "User-Agent": "Discord-Survival-Mode/3.0",
            },
            timeout=REQUEST_TIMEOUT,
        )

        if response.status_code == 429:
            _lichess_api_cooldown_until = (
                time.monotonic()
                + LICHESS_API_COOLDOWN_SECONDS
            )
            print(
                "Lichess puzzle API returned 429; "
                "using exact-range fallback for 60 seconds.",
                flush=True,
            )
            return []

        response.raise_for_status()
        payload = response.json()

        result = []

        for item in payload.get(
            "puzzles",
            [],
        ):
            puzzle = sanitize_lichess_api_puzzle(
                item
            )

            if puzzle:
                result.append(
                    puzzle
                )

        return result

    except Exception as error:
        print(
            f"Lichess puzzle API error: {error}",
            flush=True,
        )
        return []


def _hf_filter_request(
    where,
    offset,
    length,
):
    response = requests.get(
        HF_FILTER_URL,
        params={
            "dataset": HF_DATASET,
            "config": HF_CONFIG,
            "split": HF_SPLIT,
            "where": where,
            "offset": int(offset),
            "length": int(length),
        },
        headers={
            "Accept": "application/json",
            "User-Agent": "Discord-Survival-Mode/3.0",
        },
        timeout=REQUEST_TIMEOUT,
    )

    response.raise_for_status()
    return response.json()


def _hf_filtered_row_count(
    minimum_rating,
    maximum_rating,
    where,
    refresh=False,
):
    key = (
        int(minimum_rating),
        int(maximum_rating),
    )

    if (
        not refresh
        and key in HF_FILTER_COUNT_CACHE
    ):
        return HF_FILTER_COUNT_CACHE[
            key
        ]

    payload = _hf_filter_request(
        where,
        0,
        1,
    )

    total = payload.get(
        "num_rows_total"
    )

    try:
        total = int(
            total
        )
    except Exception:
        # Very old Dataset Viewer responses did not always include the
        # count. We can still use the first matching page safely.
        total = len(
            payload.get(
                "rows",
                [],
            )
        )

    HF_FILTER_COUNT_CACHE[
        key
    ] = max(
        0,
        total,
    )

    return HF_FILTER_COUNT_CACHE[
        key
    ]


def fetch_lichess_batch(
    minimum_rating,
    maximum_rating,
):
    """
    Exact-range fallback from the Lichess/chess-puzzles Dataset Viewer.

    Important fix: offset is relative to the FILTERED result set. The old
    code randomized it over all 6,057,356 dataset rows, which made many
    filtered requests land past the end and return nothing.
    """
    where = (
        f'"Rating">={int(minimum_rating)} '
        f'AND "Rating"<={int(maximum_rating)}'
    )

    for refresh in (
        False,
        True,
    ):
        try:
            total_rows = _hf_filtered_row_count(
                minimum_rating,
                maximum_rating,
                where,
                refresh=refresh,
            )

            if total_rows <= 0:
                return []

            length = min(
                BATCH_SIZE,
                100,
                total_rows,
            )

            max_offset = max(
                0,
                total_rows - length,
            )

            offset = random.randint(
                0,
                max_offset,
            )

            payload = _hf_filter_request(
                where,
                offset,
                length,
            )

            rows = payload.get(
                "rows",
                [],
            )

            if not rows and not refresh:
                # Dataset may have been refreshed since the cached count.
                continue

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
                        "PuzzleId": row.get("PuzzleId"),
                        "FEN": row.get("FEN"),
                        "Moves": row.get("Moves"),
                        "Rating": row.get("Rating"),
                        "Themes": row.get("Themes"),
                    }
                )

            return result

        except Exception as error:
            if refresh:
                print(
                    f"Lichess exact-range fallback error: {error}",
                    flush=True,
                )
                return []

    return []


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

    # Fast path for the bands the anonymous Lichess difficulty selector can
    # target well. For 2250+ we skip straight to the exact-range source so
    # high-level Survival does not waste time hoping for a rare outlier.
    if minimum < 2250:
        items = fetch_lichess_api_batch(
            minimum,
            maximum,
        )

        candidates = [
            puzzle
            for puzzle in items
            if (
                puzzle["id"] not in used_ids
                and minimum
                <= puzzle["rating"]
                <= maximum
            )
        ]

        if candidates:
            return random.choice(
                candidates
            )

    # Exact-range fallback. With the corrected filtered offset this should
    # normally succeed on the first request; keep three attempts for
    # transient API/sanitization issues without the old 8-request stall.
    for _attempt in range(3):
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
        "rated_users":
            [],
        # Keep the opponent/setup move so the very first board already shows
        # the same last-move highlight/arrow behavior as RP and Rush.
        "setup_move_uci":
            str(puzzle["moves"][0]),
        "last_move_uci":
            str(puzzle["moves"][0]),
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


def side_to_move_text(
    board,
):
    return (
        "White"
        if board.turn == chess.WHITE
        else "Black"
    )



_SURVIVAL_UNICODE_CHESS_GLYPHS = {
    "K": "♔", "Q": "♕", "R": "♖", "B": "♗", "N": "♘", "P": "♙",
    "k": "♚", "q": "♛", "r": "♜", "b": "♝", "n": "♞", "p": "♟",
}
_SURVIVAL_BLACK_CHESS_GLYPHS_BY_TYPE = {
    chess.PAWN: "♟", chess.KNIGHT: "♞", chess.BISHOP: "♝",
    chess.ROOK: "♜", chess.QUEEN: "♛", chess.KING: "♚",
}


def _survival_piece_overlay_svg(board, orientation, piece_theme):
    style = PIECE_SETS.get(piece_theme, PIECE_SETS["classic"])
    shape = style.get("shape", "classic")
    if shape == "classic":
        return ""

    square_size = 45.0
    board_offset = 15.0
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

        if shape == "glyph":
            glyph = (
                _SURVIVAL_UNICODE_CHESS_GLYPHS[symbol]
                if style.get("glyph_variant") == "native"
                else _SURVIVAL_BLACK_CHESS_GLYPHS_BY_TYPE[piece.piece_type]
            )
            font_family = style.get("font_family", "DejaVu Sans")
            font_size = float(style.get("font_size", 40))
            font_weight = style.get("font_weight", 700)
            stroke_width = float(style.get("stroke_width", 0.65))
            scale_x = float(style.get("scale_x", 1.0))
            scale_y = float(style.get("scale_y", 1.0))
            glyph_fill = "none" if style.get("outline_only") else fill
            parts.append(
                f'<g transform="translate({cx:.2f} {cy:.2f}) scale({scale_x:.3f} {scale_y:.3f})">'
                f'<text x="0" y="1" text-anchor="middle" dominant-baseline="central" '
                f'font-family="{font_family}" font-size="{font_size:g}" font-weight="{font_weight}" '
                f'fill="{glyph_fill}" stroke="{stroke}" stroke-width="{stroke_width:g}" '
                f'paint-order="stroke">{glyph}</text></g>'
            )
        elif shape == "figurine":
            glyph = _SURVIVAL_UNICODE_CHESS_GLYPHS[symbol]
            parts.append(
                f'<text x="{cx:.2f}" y="{cy + 1:.2f}" text-anchor="middle" dominant-baseline="central" '
                f'font-family="DejaVu Sans, serif" font-size="38" font-weight="700" fill="{fill}" '
                f'stroke="{stroke}" stroke-width="0.7" paint-order="stroke">{glyph}</text>'
            )
        elif shape in {"monogram", "minimal"}:
            size = 29 if shape == "monogram" else 25
            weight = 800 if shape == "monogram" else 600
            parts.append(
                f'<text x="{cx:.2f}" y="{cy + 1:.2f}" text-anchor="middle" dominant-baseline="central" '
                f'font-family="DejaVu Sans, sans-serif" font-size="{size}" font-weight="{weight}" fill="{fill}" '
                f'stroke="{stroke}" stroke-width="0.8" paint-order="stroke">{letter}</text>'
            )
        else:
            if shape == "token":
                parts.append(f'<circle cx="{cx:.2f}" cy="{cy:.2f}" r="17" fill="{fill}" stroke="{stroke}" stroke-width="2" />')
            elif shape == "diamond":
                pts = f'{cx:.2f},{cy-19:.2f} {cx+18:.2f},{cy:.2f} {cx:.2f},{cy+19:.2f} {cx-18:.2f},{cy:.2f}'
                parts.append(f'<polygon points="{pts}" fill="{fill}" stroke="{stroke}" stroke-width="2" />')
            else:
                pts = f'{cx-16:.2f},{cy-17:.2f} {cx+16:.2f},{cy-17:.2f} {cx+18:.2f},{cy+5:.2f} {cx:.2f},{cy+19:.2f} {cx-18:.2f},{cy+5:.2f}'
                parts.append(f'<polygon points="{pts}" fill="{fill}" stroke="{stroke}" stroke-width="2" />')
            text_fill = "#111111" if piece.color else "#ffffff"
            parts.append(
                f'<text x="{cx:.2f}" y="{cy + 1:.2f}" text-anchor="middle" dominant-baseline="central" '
                f'font-family="DejaVu Sans, sans-serif" font-size="22" font-weight="800" fill="{text_fill}">{letter}</text>'
            )

    parts.append('</g>')
    return "".join(parts)


def _survival_render_custom_svg(
    board,
    orientation,
    board_theme="classic",
    piece_theme="classic",
    size=520,
    lastmove=None,
    arrows=None,
):
    board_theme = str(board_theme or "classic").casefold()
    piece_theme = str(piece_theme or "classic").casefold()
    light, dark = BOARD_THEMES.get(board_theme, BOARD_THEMES["classic"])
    arrows = list(arrows or [])
    if piece_theme == "classic" or piece_theme not in PIECE_SETS:
        return chess.svg.board(
            board=board,
            orientation=orientation,
            size=size,
            coordinates=True,
            lastmove=lastmove,
            arrows=arrows,
            colors={"square light": light, "square dark": dark},
        )
    svg = chess.svg.board(
        board=None,
        orientation=orientation,
        size=size,
        coordinates=True,
        lastmove=lastmove,
        arrows=arrows,
        colors={"square light": light, "square dark": dark},
    )
    return svg.replace("</svg>", _survival_piece_overlay_svg(board, orientation, piece_theme) + "</svg>")


def _survival_last_move(puzzle):
    """Return the newest displayed move, including setup and opponent replies."""
    if not isinstance(puzzle, dict):
        return None

    raw = str(puzzle.get("last_move_uci") or "").strip().casefold()
    if not raw:
        # Compatibility for saved runs made before last_move_uci existed.
        next_index = int(puzzle.get("next_solution_index", 0) or 0)
        solution = puzzle.get("solution", [])
        if next_index > 0 and isinstance(solution, list) and next_index <= len(solution):
            raw = str(solution[next_index - 1].get("uci", "") or "").strip().casefold()
        elif next_index == 0:
            raw = str(puzzle.get("setup_move_uci") or "").strip().casefold()

    try:
        return chess.Move.from_uci(raw) if raw else None
    except Exception:
        return None


def render_board(
    puzzle,
    board_theme="classic",
    piece_theme="classic",
    arrow_theme=DEFAULT_ARROW_COLOR,
):
    board = chess.Board(
        puzzle["current_fen"]
    )

    # The board POV must follow the ACTUAL side to move in current_fen.
    # This is more reliable than trusting a stored player_color value.
    orientation = (
        chess.WHITE
        if board.turn == chess.WHITE
        else chess.BLACK
    )

    stored_player_color = str(
        puzzle.get(
            "player_color",
            "",
        )
    ).casefold()

    actual_player_color = (
        "white"
        if board.turn == chess.WHITE
        else "black"
    )

    if (
        stored_player_color
        and stored_player_color != actual_player_color
    ):
        print(
            "Survival POV warning: stored player_color "
            f"{stored_player_color!r} does not match "
            f"current FEN side to move {actual_player_color!r}. "
            "Rendering from the actual board turn.",
            flush=True,
        )

    last_move = _survival_last_move(puzzle)
    arrow_key = str(arrow_theme or DEFAULT_ARROW_COLOR).casefold()
    arrow_color = ARROW_COLORS.get(
        arrow_key,
        ARROW_COLORS[DEFAULT_ARROW_COLOR],
    )["hex"]
    arrows = []
    if last_move is not None:
        arrows.append(
            chess.svg.Arrow(
                last_move.from_square,
                last_move.to_square,
                color=arrow_color,
            )
        )

    svg = _survival_render_custom_svg(
        board,
        orientation,
        board_theme=board_theme,
        piece_theme=piece_theme,
        size=520,
        lastmove=last_move,
        arrows=arrows,
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

    board_theme = "classic"
    piece_theme = "classic"
    arrow_theme = DEFAULT_ARROW_COLOR
    captain_id = run.get("captain_id")
    if captain_id:
        try:
            cosmetics = await asyncio.to_thread(
                get_cosmetic_profile,
                captain_id,
                run.get("captain_name", "Captain"),
            )
            board_theme = cosmetics.get("active_board", "classic") or "classic"
            piece_theme = cosmetics.get("active_piece", "classic") or "classic"
            arrow_theme = cosmetics.get("active_arrow", DEFAULT_ARROW_COLOR) or DEFAULT_ARROW_COLOR
        except Exception as error:
            print(f"Survival captain cosmetics lookup failed: {error}", flush=True)

    file, board = render_board(
        puzzle,
        board_theme=board_theme,
        piece_theme=piece_theme,
        arrow_theme=arrow_theme,
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
            f"Strikes: {heart_text}\n"
            f"♟️ **{side_to_move_text(board)} to move.**\n\n"
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
        # This is transient UI state, not Survival progress.
        # Do NOT push it to GitHub: a synchronous git push here can block
        # Discord's event loop and make the next user message appear ignored.
        self.pending_team = pending

    def clear_pending_team(
        self,
    ):
        # Clear only the transient prompt. The actual Survival run is
        # persisted by start_new_run().
        self.pending_team = None

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
        # Do not self-close after 5h50. The GitHub workflow controls
        # the lifecycle and the next scheduled run takes over.
        self.action_task = None

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
        requester_user = (
            requester.author
            if hasattr(
                requester,
                "author",
            )
            else getattr(
                requester,
                "user",
                requester,
            )
        )

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
                requester_user.id,
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
                str(requester_user.id),
            "captain_id":
                str(requester_user.id),
            "captain_name":
                requester_user.display_name,
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
            "shop_heart_purchased":
                False,
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

        # The Survival state file is the source of truth. The lock is only
        # an auxiliary fast-path used by the other puzzle bot, so a failure
        # writing the lock must NOT undo/fail a newly-created Survival run.
        try:
            write_lock(
                display_name,
                run["run_id"],
                run["last_activity"],
            )
        except Exception as lock_error:
            print(
                f"Could not save puzzle mode lock; "
                f"continuing with Survival state: {lock_error}",
                flush=True,
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
                "Dead runs cannot be revived; start a new run.",
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

        try:
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
        except Exception as lock_error:
            print(
                f"Could not save puzzle mode lock on resume; "
                f"continuing: {lock_error}",
                flush=True,
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
            # Network/PGN work is synchronous; keep it off Discord's event
            # loop so the bot remains responsive while the puzzle is fetched.
            puzzle_source = await asyncio.to_thread(
                choose_puzzle_for_number,
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

    async def record_survival_rating_attempt(
        self,
        run,
        puzzle,
        user,
        correct,
    ):
        """Rate each user's first real attempt on this Survival puzzle exactly once."""
        user_id = str(user.id)
        rated_users = puzzle.setdefault("rated_users", [])
        if user_id in rated_users:
            return None

        puzzle_identity = (
            f"survival:{run.get('run_id')}:{run.get('puzzle_number')}:"
            f"{puzzle.get('id', 'unknown')}"
        )
        try:
            result = await asyncio.to_thread(
                record_puzzle_attempt,
                puzzle_identity,
                user.id,
                user.display_name,
                bool(correct),
                puzzle_rating=puzzle.get("rating"),
                boss=False,
                source="survival",
            )
            rated_users.append(user_id)
            return result
        except Exception as error:
            # Survival gameplay must never stop because the personal stats
            # repository is temporarily unavailable. The idempotent stats
            # transaction can safely be retried on a later attempt.
            print(
                f"Survival Puzzle Elo record failed for {user.display_name}: {error}",
                flush=True,
            )
            return None

    async def score_completed_puzzle(
        self,
        run,
    ):
        """Award Survival coins without changing the shared points leaderboard.

        First solver earns +1 coin. Each unique later helper earns +0.5 coin.
        Transaction IDs are deterministic so save/retry races cannot pay twice.
        """
        first_id = run.get("first_solver_id")

        if first_id:
            first_name = run.get("first_solver_name", "Unknown")
            tx_id = (
                f"survival-coin:{run['run_id']}:"
                f"{run['puzzle_number']}:first:{first_id}"
            )
            try:
                await asyncio.to_thread(
                    credit_coins,
                    first_id,
                    first_name,
                    1.0,
                    tx_id,
                    source="survival-first",
                )
            except Exception as error:
                print(f"Survival first-solver coin error: {error}", flush=True)

        for helper_id, helper_name in run.get("helper_candidates", {}).items():
            if str(helper_id) == str(first_id):
                continue

            tx_id = (
                f"survival-coin:{run['run_id']}:"
                f"{run['puzzle_number']}:helper:{helper_id}"
            )
            try:
                await asyncio.to_thread(
                    credit_coins,
                    helper_id,
                    helper_name,
                    0.5,
                    tx_id,
                    source="survival-helper",
                )
            except Exception as error:
                print(f"Survival helper coin error for {helper_name}: {error}", flush=True)

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


            # Personal Puzzle Elo/stats use the user's first real attempt on
            # this Survival puzzle. Late duplicate accepted moves never reach
            # this point, so they cannot become a false wrong result.
            await self.record_survival_rating_attempt(
                run,
                puzzle,
                user,
                correct,
            )

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

            puzzle["last_move_uci"] = move.uci()
            puzzle["last_move_san"] = accepted_san

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

                puzzle["last_move_uci"] = reply_move.uci()
                puzzle["last_move_san"] = reply["san"]

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

                # Survival keeps its team leaderboard separate from shared
                # points, but successful participation now earns spendable
                # coins (+1 first solver / +0.5 helper).
                puzzle_number_completed = int(
                    run["puzzle_number"]
                )

                await self.score_completed_puzzle(run)

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
                    + "\n🪙 **Survival rewards:** first solver +1 coin • helpers +0.5 coin.\n"
                    + f"Next up: **Puzzle "
                    f"#{run['puzzle_number']}**."
                )

                await self.post_next_puzzle(
                    message.channel,
                    team_key,
                )

                return

            board_theme = "classic"
            piece_theme = "classic"
            arrow_theme = DEFAULT_ARROW_COLOR
            captain_id = run.get("captain_id")
            if captain_id:
                try:
                    cosmetics = await asyncio.to_thread(
                        get_cosmetic_profile,
                        captain_id,
                        run.get("captain_name", "Captain"),
                    )
                    board_theme = cosmetics.get("active_board", "classic") or "classic"
                    piece_theme = cosmetics.get("active_piece", "classic") or "classic"
                    arrow_theme = cosmetics.get("active_arrow", DEFAULT_ARROW_COLOR) or DEFAULT_ARROW_COLOR
                except Exception as error:
                    print(f"Survival captain cosmetics lookup failed: {error}", flush=True)

            file, display_board = render_board(
                puzzle,
                board_theme=board_theme,
                piece_theme=piece_theme,
                arrow_theme=arrow_theme,
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
                    + f"♟️ **{side_to_move_text(display_board)} to move.**\n"
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

        # Highest-priority path: after !survival the very next plain
        # message from the same user/channel is the team name.
        pending = self.pending_team

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
                and team.get("current")
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

            try:
                await self.start_new_run(
                    key,
                    team_name,
                    message,
                )
            except Exception as error:
                print(
                    f"Could not create Survival team: {error}",
                    flush=True,
                )
                traceback.print_exc()

                await message.channel.send(
                    f"❌ **Could not create Survival team {team_name}.**\n"
                    f"`{str(error)[:900]}`"
                )

            return

        # Expired prompt: discard it and continue normally.
        if (
            pending
            and epoch_now()
            > float(
                pending.get(
                    "expires",
                    0,
                )
            )
        ):
            self.clear_pending_team()

        if await self.handle_admin_command(
            message,
            lower,
            content,
        ):
            return

        if lower == "!heart":
            await self.buy_shop_heart(message)
            return

        # Captain-only run-mode commands. The implementation already existed
        # in set_run_mode(), but the old on_message router never called it.
        if lower == "!solo" or lower.startswith("!solo "):
            team_name = content[len("!solo"):].strip()

            if not team_name:
                await message.channel.send(
                    "❌ Usage: `!solo <team name>`"
                )
                return

            await self.set_run_mode(
                message,
                normalize_team_name(team_name),
                "solo",
            )
            return

        if lower == "!coop" or lower.startswith("!coop "):
            team_name = content[len("!coop"):].strip()

            if not team_name:
                await message.channel.send(
                    "❌ Usage: `!coop <team name>`"
                )
                return

            await self.set_run_mode(
                message,
                normalize_team_name(team_name),
                "coop",
            )
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
            f"**Strikes:** {run.get('strikes', 0)}/3\n"
            f"**Shop heart used:** {'Yes' if run.get('shop_heart_purchased', False) else 'No'}\n\n"
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
        shark_id = os.getenv(
            "SHARKMEISTER_USER_ID",
            SHARKMEISTER_DEFAULT_USER_ID,
        ).strip() or SHARKMEISTER_DEFAULT_USER_ID

        return (
            str(getattr(user, "id", ""))
            == shark_id
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

        if old_strikes >= THREE_STRIKES:
            await message.channel.send(
                "💀 **Dead Survival runs cannot be revived with a heart.**"
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


    async def buy_shop_heart(self, message):
        async with self.game_lock:
            active = active_current_run(self.state)
            if not active:
                # Local state can be stale after a restart/competing process.
                remote = await asyncio.to_thread(load_remote_runs)
                if remote is not None and active_current_run(remote):
                    self.state = remote
                    active = active_current_run(self.state)

            if not active:
                await message.channel.send("❌ **There is no active Survival run.**")
                return

            team_key, team = active
            run = team.get("current")
            if not isinstance(run, dict) or run.get("status") != "active":
                await message.channel.send("❌ **There is no active Survival run.**")
                return

            if not self.is_run_captain(message.author, run):
                await message.channel.send(
                    f"❌ Only captain **{run.get('captain_name', 'the captain')}** can buy the Survival heart."
                )
                return

            strikes = int(run.get("strikes", 0) or 0)
            if strikes >= THREE_STRIKES:
                await message.channel.send("💀 **A dead Survival run cannot be revived.**")
                return
            if strikes <= 0:
                await message.channel.send("❤️ **This run already has all 3 hearts.**")
                return
            if bool(run.get("shop_heart_purchased", False)):
                await message.channel.send(
                    "❌ **This Survival run already used its one purchasable heart.**"
                )
                return

            try:
                coins = await asyncio.to_thread(get_coins, message.author.id)
            except Exception as error:
                await message.channel.send(f"❌ Could not read your coins: `{str(error)[:500]}`")
                return

            if float(coins) + 1e-9 < float(SURVIVAL_HEART_COST):
                await message.channel.send(
                    f"❌ **Not enough coins.** A Survival heart costs **{format_points(SURVIVAL_HEART_COST)} coins**; "
                    f"you have **{format_points(coins)}**."
                )
                return

            tx_id = (
                f"survival-heart:{run.get('run_id', team_key)}:"
                f"{message.author.id}:{message.id}"
            )
            try:
                coins_left = await asyncio.to_thread(
                    spend_coins,
                    message.author.id,
                    message.author.display_name,
                    SURVIVAL_HEART_COST,
                    tx_id,
                    source="survival-heart",
                )
            except Exception as error:
                await message.channel.send(f"❌ **Could not buy heart:** {str(error)[:700]}")
                return

            old_strikes = strikes
            run["strikes"] = max(0, strikes - 1)
            run["shop_heart_purchased"] = True
            run["shop_heart_buyer_id"] = str(message.author.id)
            run["shop_heart_buyer_name"] = message.author.display_name
            run["shop_heart_transaction_id"] = tx_id
            run["last_activity"] = epoch_now()

            try:
                save_state(self.state, push=True)
                write_lock(
                    team.get("name", team_key),
                    run.get("run_id"),
                    run["last_activity"],
                )
            except Exception as error:
                # Best-effort compensation: restore the run locally and refund
                # the wallet with a separate idempotent transaction.
                run["strikes"] = old_strikes
                run["shop_heart_purchased"] = False
                run.pop("shop_heart_buyer_id", None)
                run.pop("shop_heart_buyer_name", None)
                run.pop("shop_heart_transaction_id", None)
                try:
                    await asyncio.to_thread(
                        credit_coins,
                        message.author.id,
                        message.author.display_name,
                        SURVIVAL_HEART_COST,
                        f"survival-heart-refund:{run.get('run_id', team_key)}:{message.author.id}:{message.id}",
                        source="survival-heart-refund",
                    )
                except Exception as refund_error:
                    print(f"Survival heart refund failed: {refund_error}", flush=True)
                await message.channel.send(
                    f"❌ **Heart purchase could not be saved:** `{str(error)[:500]}`"
                )
                return

            await message.channel.send(
                f"❤️ **{team.get('name', team_key)} bought its one shop heart!**\n"
                f"Hearts now: {'❤️' * (THREE_STRIKES - run['strikes'])}"
                f"{'🖤' * run['strikes']}\n"
                f"🪙 **{format_points(coins_left)} coins** left for {message.author.display_name}."
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
            # Reconcile from the persisted source of truth before claiming that
            # no run exists. This fixes the stale-local-state case where RP
            # could still see an active remote run after !stopsurvival.
            remote_state = await asyncio.to_thread(load_remote_runs)
            if remote_state is not None:
                remote_active = active_current_run(remote_state)
                if remote_active:
                    self.state = remote_state
                    active = remote_active

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
    Legacy compatibility hook.

    Survival no longer closes itself after 5h50. Keeping this coroutine
    harmless prevents an accidental future call from taking the bot offline.
    """
    while not self.is_closed():
        await asyncio.sleep(60 * 60)


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
