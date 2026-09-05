import atexit
import math
import os
import random
import re
import shutil
import subprocess
import tempfile
import threading

import chess
import chess.engine

CHESS_PLAY_BUILD = "chess-play-v1-elo-bot-pvp-2026-09-05"
CHESS_START_ELO = 1500.0
CHESS_K = 32.0
CHESS_MIN_ELO = 100.0
CHESS_MAX_ELO = 4000.0
BOT_MIN_ELO = 1320
BOT_MAX_ELO = 3190
BOT_FULL_STRENGTH_ELO = 4000
STOCKFISH_REQUIRED_MAJOR = 19


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
    if rating == BOT_FULL_STRENGTH_ELO:
        return rating
    if not BOT_MIN_ELO <= rating <= BOT_MAX_ELO:
        raise ValueError(
            f"Bot Elo must be between {BOT_MIN_ELO} and {BOT_MAX_ELO}, "
            f"or exactly {BOT_FULL_STRENGTH_ELO} for full-strength Stockfish."
        )
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


class StockfishUnavailableError(RuntimeError):
    pass


_STOCKFISH_LOCK = threading.RLock()
_STOCKFISH_ENGINE = None
_STOCKFISH_PATH = None
_STOCKFISH_INSTALL_ATTEMPTED = False
_STOCKFISH_LAST_INSTALL_ERROR = None


def _positive_int_env(name, default, minimum=1, maximum=None):
    try:
        value = int(os.getenv(name, str(default)))
    except Exception:
        value = int(default)
    value = max(int(minimum), value)
    if maximum is not None:
        value = min(int(maximum), value)
    return value


def _positive_float_env(name, default, minimum=0.05, maximum=None):
    try:
        value = float(os.getenv(name, str(default)))
    except Exception:
        value = float(default)
    value = max(float(minimum), value)
    if maximum is not None:
        value = min(float(maximum), value)
    return value


STOCKFISH_THREADS = _positive_int_env("STOCKFISH_THREADS", 1, 1, 4)
STOCKFISH_HASH_MB = _positive_int_env("STOCKFISH_HASH_MB", 64, 16, 512)
STOCKFISH_MOVE_TIME = _positive_float_env("STOCKFISH_MOVE_TIME", 1.0, 0.1, 10.0)


def _stockfish_candidates():
    configured = str(os.getenv("STOCKFISH_PATH", "") or "").strip()
    candidates = []
    if configured:
        candidates.append(configured)

    found = shutil.which("stockfish")
    if found:
        candidates.append(found)

    candidates.extend([
        "/usr/games/stockfish",
        "/usr/bin/stockfish",
        "/usr/local/bin/stockfish",
        "/opt/homebrew/bin/stockfish",
        str(os.path.abspath("stockfish")),
        str(os.path.abspath(os.path.join("bin", "stockfish"))),
    ])

    result = []
    seen = set()
    for candidate in candidates:
        path = os.path.abspath(os.path.expanduser(str(candidate)))
        if path in seen:
            continue
        seen.add(path)
        result.append(path)
    return result


def _stockfish_major_from_text(text):
    match = re.search(r"\bStockfish\s+(\d+)(?:\.|\b)", str(text or ""), re.IGNORECASE)
    return int(match.group(1)) if match else None


def _probe_stockfish_major(path):
    try:
        probe = subprocess.run(
            [path],
            input="uci\nquit\n",
            capture_output=True,
            text=True,
            timeout=10,
        )
    except Exception:
        return None
    return _stockfish_major_from_text((probe.stdout or "") + "\n" + (probe.stderr or ""))


def _find_stockfish_binary():
    for path in _stockfish_candidates():
        if not (os.path.isfile(path) and os.access(path, os.X_OK)):
            continue
        major = _probe_stockfish_major(path)
        if major is not None and major >= STOCKFISH_REQUIRED_MAJOR:
            return path
    return None


def _github_actions_auto_install_enabled():
    if str(os.getenv("GITHUB_ACTIONS", "")).strip().casefold() != "true":
        return False
    value = str(os.getenv("STOCKFISH_AUTO_INSTALL", "1") or "1").strip().casefold()
    return value not in {"0", "false", "no", "off"}


def _try_install_stockfish_on_github_actions():
    global _STOCKFISH_INSTALL_ATTEMPTED, _STOCKFISH_LAST_INSTALL_ERROR, _STOCKFISH_PATH

    if _STOCKFISH_INSTALL_ATTEMPTED:
        return
    _STOCKFISH_INSTALL_ATTEMPTED = True

    if not _github_actions_auto_install_enabled():
        return

    git = shutil.which("git")
    make = shutil.which("make")
    if git is None or make is None:
        _STOCKFISH_LAST_INSTALL_ERROR = "git/make is unavailable on this runner"
        return

    build_root = os.path.join(tempfile.gettempdir(), "stockfish19-official")
    source_root = os.path.join(build_root, "Stockfish")
    binary_path = os.path.join(source_root, "src", "stockfish")

    try:
        if os.path.isdir(build_root):
            shutil.rmtree(build_root, ignore_errors=True)
        os.makedirs(build_root, exist_ok=True)

        clone = subprocess.run(
            [
                git, "clone", "--depth", "1", "--branch", "sf_19",
                "https://github.com/official-stockfish/Stockfish.git", source_root,
            ],
            capture_output=True,
            text=True,
            timeout=180,
        )
        if clone.returncode != 0:
            detail = (clone.stderr or clone.stdout or "").strip().splitlines()
            _STOCKFISH_LAST_INSTALL_ERROR = (
                detail[-1] if detail else "could not clone official Stockfish sf_19 tag"
            )
            return

        build = subprocess.run(
            [make, "-C", os.path.join(source_root, "src"), "-j2", "build", "ARCH=x86-64-avx2"],
            capture_output=True,
            text=True,
            timeout=300,
        )
        if build.returncode != 0:
            detail = (build.stderr or build.stdout or "").strip().splitlines()
            _STOCKFISH_LAST_INSTALL_ERROR = (
                detail[-1] if detail else "could not build Stockfish 19"
            )
            return

        major = _probe_stockfish_major(binary_path)
        if major is None or major < STOCKFISH_REQUIRED_MAJOR:
            _STOCKFISH_LAST_INSTALL_ERROR = (
                f"built engine did not identify as Stockfish {STOCKFISH_REQUIRED_MAJOR}+"
            )
            return

        os.chmod(binary_path, 0o755)
        _STOCKFISH_PATH = binary_path
    except Exception as error:
        _STOCKFISH_LAST_INSTALL_ERROR = str(error)


def _resolve_stockfish_binary():
    global _STOCKFISH_PATH
    if _STOCKFISH_PATH and os.path.isfile(_STOCKFISH_PATH):
        return _STOCKFISH_PATH

    path = _find_stockfish_binary()
    if path is None:
        _try_install_stockfish_on_github_actions()
        path = _STOCKFISH_PATH or _find_stockfish_binary()

    if path is None:
        detail = ""
        if _STOCKFISH_LAST_INSTALL_ERROR:
            detail = f" Auto-install error: {_STOCKFISH_LAST_INSTALL_ERROR}."
        raise StockfishUnavailableError(
            f"Stockfish {STOCKFISH_REQUIRED_MAJOR}+ is not installed. "
            f"Set STOCKFISH_PATH to a Stockfish {STOCKFISH_REQUIRED_MAJOR}+ binary. "
            "On GitHub Actions the bot will try to build the official sf_19 tag automatically."
            + detail
        )

    _STOCKFISH_PATH = path
    return path


def _close_stockfish_engine():
    global _STOCKFISH_ENGINE
    with _STOCKFISH_LOCK:
        engine = _STOCKFISH_ENGINE
        _STOCKFISH_ENGINE = None
        if engine is not None:
            try:
                engine.quit()
            except Exception:
                try:
                    engine.close()
                except Exception:
                    pass


atexit.register(_close_stockfish_engine)


def _open_stockfish_engine():
    global _STOCKFISH_ENGINE
    path = _resolve_stockfish_binary()
    try:
        engine = chess.engine.SimpleEngine.popen_uci(path, timeout=15.0)
    except Exception as error:
        raise StockfishUnavailableError(
            f"Could not start Stockfish at '{path}': {error}"
        ) from error

    engine_name = str(engine.id.get("name") or "Stockfish")
    engine_major = _stockfish_major_from_text(engine_name)
    if engine_major is None or engine_major < STOCKFISH_REQUIRED_MAJOR:
        try:
            engine.quit()
        except Exception:
            pass
        raise StockfishUnavailableError(
            f"Stockfish {STOCKFISH_REQUIRED_MAJOR}+ is required, but this binary reports '{engine_name}'."
        )

    required = {"UCI_LimitStrength", "UCI_Elo"}
    missing = sorted(required.difference(engine.options.keys()))
    if missing:
        try:
            engine.quit()
        except Exception:
            pass
        raise StockfishUnavailableError(
            "This Stockfish build does not expose the required UCI options: "
            + ", ".join(missing)
        )

    config = {"UCI_LimitStrength": True}
    if "Threads" in engine.options:
        option = engine.options["Threads"]
        max_threads = int(option.max or STOCKFISH_THREADS)
        config["Threads"] = min(STOCKFISH_THREADS, max_threads)
    if "Hash" in engine.options:
        option = engine.options["Hash"]
        min_hash = int(option.min or 1)
        max_hash = int(option.max or STOCKFISH_HASH_MB)
        config["Hash"] = min(max(STOCKFISH_HASH_MB, min_hash), max_hash)
    engine.configure(config)
    _STOCKFISH_ENGINE = engine
    return engine


def _get_stockfish_engine():
    global _STOCKFISH_ENGINE
    if _STOCKFISH_ENGINE is None:
        return _open_stockfish_engine()
    return _STOCKFISH_ENGINE


def stockfish_engine_info():
    with _STOCKFISH_LOCK:
        engine = _get_stockfish_engine()
        elo_option = engine.options["UCI_Elo"]
        minimum = int(elo_option.min if elo_option.min is not None else BOT_MIN_ELO)
        maximum = int(elo_option.max if elo_option.max is not None else BOT_MAX_ELO)
        name = str(engine.id.get("name") or "Stockfish")
        return {
            "name": name,
            "major": _stockfish_major_from_text(name),
            "path": str(_STOCKFISH_PATH or ""),
            "min_elo": minimum,
            "max_elo": maximum,
            "full_strength_elo": BOT_FULL_STRENGTH_ELO,
            "move_time": STOCKFISH_MOVE_TIME,
        }


def _stockfish_play_once(board, rating):
    engine = _get_stockfish_engine()
    rating = int(rating)
    elo_option = engine.options["UCI_Elo"]
    minimum = int(elo_option.min if elo_option.min is not None else BOT_MIN_ELO)
    maximum = int(elo_option.max if elo_option.max is not None else BOT_MAX_ELO)

    if rating == BOT_FULL_STRENGTH_ELO:
        config = {"UCI_LimitStrength": False}
        if "Skill Level" in engine.options:
            config["Skill Level"] = int(engine.options["Skill Level"].max or 20)
        engine.configure(config)
    elif minimum <= rating <= maximum:
        engine.configure({
            "UCI_LimitStrength": True,
            "UCI_Elo": rating,
        })
    else:
        raise ValueError(
            f"This Stockfish build supports calibrated UCI Elo {minimum}-{maximum}; "
            f"use {BOT_FULL_STRENGTH_ELO} for full strength. Requested {rating}."
        )

    result = engine.play(
        board,
        chess.engine.Limit(time=STOCKFISH_MOVE_TIME),
        ponder=False,
    )
    return result.move


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
    if board.is_game_over(claim_draw=True):
        return None

    rating = clamp_bot_rating(target_elo)
    with _STOCKFISH_LOCK:
        # A persistent UCI engine is reused between moves. If the process dies,
        # restart it once and retry the exact same position/rating.
        try:
            return _stockfish_play_once(board, rating)
        except (
            chess.engine.EngineTerminatedError,
            chess.engine.EngineError,
            BrokenPipeError,
            OSError,
        ):
            _close_stockfish_engine()
            return _stockfish_play_once(board, rating)
