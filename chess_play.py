import math
import random
import re

import chess

CHESS_PLAY_BUILD = "chess-play-v1-elo-bot-pvp-2026-09-05"
CHESS_START_ELO = 1500.0
CHESS_K = 32.0
CHESS_MIN_ELO = 100.0
CHESS_MAX_ELO = 4000.0
BOT_MIN_ELO = 100
BOT_MAX_ELO = 4000


def normalize_rating_entry(entry=None, name="Unknown"):
    entry = dict(entry or {})
    try:
        elo = float(entry.get("elo", CHESS_START_ELO))
    except Exception:
        elo = CHESS_START_ELO
    elo = min(CHESS_MAX_ELO, max(CHESS_MIN_ELO, elo))
    try:
        peak = float(entry.get("peak_elo", elo))
    except Exception:
        peak = elo
    peak = max(elo, peak)
    return {
        "name": str(entry.get("name") or name or "Unknown"),
        "elo": round(elo, 3),
        "peak_elo": round(peak, 3),
        "games": max(0, int(entry.get("games", 0) or 0)),
        "wins": max(0, int(entry.get("wins", 0) or 0)),
        "draws": max(0, int(entry.get("draws", 0) or 0)),
        "losses": max(0, int(entry.get("losses", 0) or 0)),
    }


def rating_entry(ratings, user_id, display_name="Unknown"):
    uid = str(user_id)
    clean = normalize_rating_entry(ratings.get(uid), display_name)
    clean["name"] = str(display_name or clean["name"])
    ratings[uid] = clean
    return clean


def elo_expected(player_elo, opponent_elo):
    return 1.0 / (1.0 + 10.0 ** ((float(opponent_elo) - float(player_elo)) / 400.0))


def elo_after(player_elo, opponent_elo, score, k=CHESS_K):
    expected = elo_expected(player_elo, opponent_elo)
    new_elo = float(player_elo) + float(k) * (float(score) - expected)
    return min(CHESS_MAX_ELO, max(CHESS_MIN_ELO, new_elo))


def apply_single_result(ratings, user_id, display_name, opponent_elo, score):
    entry = rating_entry(ratings, user_id, display_name)
    before = float(entry["elo"])
    after = elo_after(before, opponent_elo, score)
    entry["elo"] = round(after, 3)
    entry["peak_elo"] = round(max(float(entry.get("peak_elo", before)), after), 3)
    entry["games"] += 1
    if score > 0.75:
        entry["wins"] += 1
    elif score < 0.25:
        entry["losses"] += 1
    else:
        entry["draws"] += 1
    ratings[str(user_id)] = entry
    return {
        "before": before,
        "after": after,
        "change": after - before,
        "entry": dict(entry),
    }


def apply_head_to_head_result(
    ratings,
    white_id,
    white_name,
    black_id,
    black_name,
    white_score,
):
    white = rating_entry(ratings, white_id, white_name)
    black = rating_entry(ratings, black_id, black_name)
    white_before = float(white["elo"])
    black_before = float(black["elo"])
    black_score = 1.0 - float(white_score)
    white_after = elo_after(white_before, black_before, white_score)
    black_after = elo_after(black_before, white_before, black_score)

    white["elo"] = round(white_after, 3)
    black["elo"] = round(black_after, 3)
    white["peak_elo"] = round(max(float(white.get("peak_elo", white_before)), white_after), 3)
    black["peak_elo"] = round(max(float(black.get("peak_elo", black_before)), black_after), 3)

    for entry, score in ((white, float(white_score)), (black, black_score)):
        entry["games"] += 1
        if score > 0.75:
            entry["wins"] += 1
        elif score < 0.25:
            entry["losses"] += 1
        else:
            entry["draws"] += 1

    ratings[str(white_id)] = white
    ratings[str(black_id)] = black
    return {
        "white": {
            "before": white_before,
            "after": white_after,
            "change": white_after - white_before,
            "entry": dict(white),
        },
        "black": {
            "before": black_before,
            "after": black_after,
            "change": black_after - black_before,
            "entry": dict(black),
        },
    }


def random_bot_rating(player_elo):
    return max(
        BOT_MIN_ELO,
        min(BOT_MAX_ELO, int(round(float(player_elo))) + random.randint(-200, 200)),
    )


def clamp_bot_rating(value):
    rating = int(round(float(value)))
    if not BOT_MIN_ELO <= rating <= BOT_MAX_ELO:
        raise ValueError(f"Bot Elo must be between {BOT_MIN_ELO} and {BOT_MAX_ELO}.")
    return rating


_MOVE_LIKE = re.compile(
    r"^(?:"
    r"[KQRBN]?[a-h]?[1-8]?x?[a-h][1-8](?:=[QRBN])?[+#]?"
    r"|[a-h](?:x[a-h])?[18]=[QRBN][+#]?"
    r"|O-O-O[+#]?|O-O[+#]?|0-0-0[+#]?|0-0[+#]?"
    r"|[a-h][1-8][a-h][1-8][qrbn]?"
    r")$",
    re.IGNORECASE,
)


def move_like_text(text):
    value = str(text or "").strip()
    if value.startswith("!"):
        value = value[1:].strip()
    if value.casefold().startswith("move "):
        value = value[5:].strip()
    return bool(value and len(value) <= 12 and _MOVE_LIKE.fullmatch(value))


def parse_move(board, text):
    value = str(text or "").strip()
    if value.startswith("!"):
        value = value[1:].strip()
    if value.casefold().startswith("move "):
        value = value[5:].strip()
    value = value.replace("0-0-0", "O-O-O").replace("0-0", "O-O")

    try:
        move = board.parse_san(value)
        return move, board.san(move)
    except Exception:
        pass

    # Match SAN case-insensitively, just like the Puzzle Bot already does.
    # This accepts `nf3`, `BF2+`, etc. without weakening legality checks.
    submitted_key = value.casefold().rstrip("+#")
    for legal in board.legal_moves:
        san = board.san(legal)
        if san.casefold().rstrip("+#") == submitted_key:
            return legal, san

    try:
        move = chess.Move.from_uci(value.casefold())
        if move in board.legal_moves:
            return move, board.san(move)
    except Exception:
        pass

    raise ValueError("Illegal move.")


_PIECE_VALUES = {
    chess.PAWN: 100,
    chess.KNIGHT: 320,
    chess.BISHOP: 330,
    chess.ROOK: 500,
    chess.QUEEN: 900,
    chess.KING: 0,
}


def _static_eval(board, color):
    if board.is_checkmate():
        return -100000 if board.turn == color else 100000
    if board.is_stalemate() or board.is_insufficient_material():
        return 0

    score = 0.0
    for piece_type, value in _PIECE_VALUES.items():
        score += len(board.pieces(piece_type, color)) * value
        score -= len(board.pieces(piece_type, not color)) * value

    # Small positional signals. They are deliberately cheap because this bot
    # must run inside the Discord process without an external engine binary.
    if board.is_check():
        score += 25 if board.turn != color else -25

    center = (chess.D4, chess.E4, chess.D5, chess.E5)
    for square in center:
        piece = board.piece_at(square)
        if piece is not None:
            score += 12 if piece.color == color else -12

    return score


def _candidate_score(board, move, color, depth):
    child = board.copy(stack=False)
    child.push(move)
    if child.is_checkmate():
        return 100000.0
    if depth <= 1:
        return _static_eval(child, color)

    replies = list(child.legal_moves)
    if not replies:
        return _static_eval(child, color)

    # One opponent reply is enough to prevent the strongest simulated levels
    # from hanging pieces in one move, while keeping runtime predictable.
    worst = math.inf
    for reply in replies:
        grandchild = child.copy(stack=False)
        grandchild.push(reply)
        value = _static_eval(grandchild, color)
        if value < worst:
            worst = value
    return worst


def choose_bot_move(board, target_elo):
    legal = list(board.legal_moves)
    if not legal:
        return None

    rating = clamp_bot_rating(target_elo)
    if rating <= 650:
        return random.choice(legal)

    color = board.turn
    depth = 2 if rating >= 1500 else 1
    scored = []
    for move in legal:
        base = _candidate_score(board, move, color, depth)
        # Lower-rated bots receive substantially more evaluation noise.
        noise_cp = max(8.0, 300.0 - (rating - BOT_MIN_ELO) * 0.105)
        noisy = base + random.gauss(0.0, noise_cp)
        scored.append((noisy, move))

    scored.sort(key=lambda item: item[0], reverse=True)

    if rating < 900:
        width = min(len(scored), 8)
    elif rating < 1300:
        width = min(len(scored), 5)
    elif rating < 1700:
        width = min(len(scored), 3)
    elif rating < 2100:
        width = min(len(scored), 2)
    else:
        width = 1

    # Prefer better candidates but keep variety at human-like levels.
    pool = scored[:width]
    if len(pool) == 1:
        return pool[0][1]
    weights = list(range(len(pool), 0, -1))
    return random.choices([item[1] for item in pool], weights=weights, k=1)[0]
