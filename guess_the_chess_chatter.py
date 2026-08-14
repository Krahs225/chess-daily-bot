import discord
import requests
import chess
import chess.pgn
import chess.svg
import cairosvg

from io import StringIO, BytesIO
from datetime import datetime, timezone, timedelta

import asyncio
import random
import os
import json
import traceback


# =========================================================
# SETTINGS
# =========================================================

TOKEN = os.getenv("DISCORD_TOKEN")

CHANNEL_ID = 1537837944193417300

# Elke ronde duurt 10 minuten
ROUND_SECONDS = 10 * 60

# Minimaal 10 volledige zetten
MIN_FULL_MOVES = 10

# Scorebestand
SCORES_FILE = "chess_chatter_scores.json"

HEADERS = {
    "User-Agent": "GuessTheChessChatter/6.0"
}


# =========================================================
# PLAYERS
# =========================================================

PLAYERS = {

    "Shark": {
        "username": "Sharkmeister",
        "start_date": "2026-01-01"
    },

    "Lars": {
        "username": "Lars11111",
        "start_date": "2024-10-31"
    },

    "Mohammad": {
        "username": "Moh979xx",
        "start_date": "2026-01-01"
    },

    "Stepu": {
        "username": "T-VoltioS",
        "start_date": "2026-01-01"
    },

    "Thice": {
        "username": "Thice",
        "start_date": "2026-01-01"
    },

    "Adelson": {
        "username": "Adelson7",
        "start_date": "2026-01-01"
    },

    "Nairyaaa": {
        "username": "Naiiiraaa",
        "start_date": "2026-01-01"
    },

    "Pospos": {
        "username": "pospos12",
        "start_date": "2026-01-01"
    },

    "Pandarou": {
        "username": "iAmPandaro",
        "start_date": "2026-01-01"
    },

    "Sushi": {
        "username": "IsolatedSushi",
        "start_date": "2026-01-01"
    }
}


# =========================================================
# DISCORD INTENTS
# =========================================================

intents = discord.Intents.default()

intents.message_content = True

try:
    intents.polls = True
except Exception:
    pass

try:
    intents.guild_polls = True
except Exception:
    pass


client = discord.Client(
    intents=intents
)


# =========================================================
# GLOBAL STATE
# =========================================================

scores = {}

active_round = None

round_lock = asyncio.Lock()

round_started = False


# =========================================================
# LOG
# =========================================================

def log(message):

    print(
        f"[ChessChatter] {message}",
        flush=True
    )


# =========================================================
# SCORES
# =========================================================

def load_scores():

    if not os.path.exists(
        SCORES_FILE
    ):
        return {}

    try:

        with open(
            SCORES_FILE,
            "r",
            encoding="utf-8"
        ) as f:

            return json.load(f)

    except Exception as error:

        log(
            f"Could not load scores: {error}"
        )

        return {}


def save_scores():

    try:

        with open(
            SCORES_FILE,
            "w",
            encoding="utf-8"
        ) as f:

            json.dump(
                scores,
                f,
                indent=2,
                ensure_ascii=False
            )

        log("Scores saved.")

    except Exception as error:

        log(
            f"Could not save scores: {error}"
        )


# =========================================================
# CHESS.COM API
# =========================================================

def get_json(url):

    try:

        response = requests.get(
            url,
            headers=HEADERS,
            timeout=20
        )

        if response.status_code != 200:

            log(
                f"Chess.com HTTP "
                f"{response.status_code}: {url}"
            )

            return None

        return response.json()

    except Exception as error:

        log(
            f"Request error: {error}"
        )

        return None


def get_archives(username):

    url = (
        "https://api.chess.com/pub/player/"
        f"{username}/games/archives"
    )

    data = get_json(url)

    if not data:
        return []

    return data.get(
        "archives",
        []
    )


def archive_year_month(archive):

    parts = archive.rstrip(
        "/"
    ).split("/")

    try:

        return (
            int(parts[-2]),
            int(parts[-1])
        )

    except Exception:

        return None


def archive_allowed(
    archive,
    start_date
):

    ym = archive_year_month(
        archive
    )

    if ym is None:
        return False

    year, month = ym

    if year < start_date.year:
        return False

    if (
        year == start_date.year
        and month < start_date.month
    ):
        return False

    return True


def get_games(archive):

    data = get_json(
        archive
    )

    if not data:
        return []

    return data.get(
        "games",
        []
    )


# =========================================================
# GAME DATE
# =========================================================

def get_game_date(game):

    timestamp = game.get(
        "end_time"
    )

    if timestamp:

        try:

            return datetime.fromtimestamp(
                int(timestamp),
                timezone.utc
            ).date()

        except Exception:
            pass

    pgn_text = game.get(
        "pgn",
        ""
    )

    if pgn_text:

        try:

            parsed = chess.pgn.read_game(
                StringIO(pgn_text)
            )

            if parsed:

                date_text = parsed.headers.get(
                    "Date"
                )

                if date_text:

                    return datetime.strptime(
                        date_text,
                        "%Y.%m.%d"
                    ).date()

        except Exception:
            pass

    return None


# =========================================================
# GAME FILTER
# =========================================================

def is_suitable_game(
    game,
    chatter,
    start_date
):

    # -----------------------------------------------------
    # RATED ONLY
    # -----------------------------------------------------

    if game.get(
        "rated"
    ) is not True:

        return False

    # -----------------------------------------------------
    # RAPID OR BLITZ ONLY
    # -----------------------------------------------------

    time_class = str(
        game.get(
            "time_class",
            ""
        )
    ).lower()

    if time_class not in (
        "rapid",
        "blitz"
    ):

        return False

    # -----------------------------------------------------
    # DATE
    # -----------------------------------------------------

    date = get_game_date(
        game
    )

    if date is None:
        return False

    if date < start_date:
        return False

    # Lars:
    # 31 October 2024 onward
    #
    # Everyone else:
    # 2026 only

    if chatter != "Lars":

        if date.year != 2026:
            return False

    # -----------------------------------------------------
    # PGN
    # -----------------------------------------------------

    pgn_text = game.get(
        "pgn"
    )

    if not pgn_text:
        return False

    try:

        parsed = chess.pgn.read_game(
            StringIO(pgn_text)
        )

        if parsed is None:
            return False

        moves = list(
            parsed.mainline_moves()
        )

        # 10 full moves = 20 half-moves
        if len(moves) < (
            MIN_FULL_MOVES * 2
        ):

            return False

    except Exception:

        return False

    return True


# =========================================================
# FIND GAMES
# =========================================================

def find_games_for_player(
    chatter,
    config
):

    username = config[
        "username"
    ]

    start_date = datetime.strptime(
        config["start_date"],
        "%Y-%m-%d"
    ).date()

    log(
        f"Searching {chatter} "
        f"({username})..."
    )

    archives = get_archives(
        username
    )

    if not archives:

        log(
            f"No archives found for "
            f"{username}"
        )

        return []

    allowed_archives = [
        archive
        for archive in archives
        if archive_allowed(
            archive,
            start_date
        )
    ]

    allowed_archives.sort(
        reverse=True
    )

    # Lars kan terug tot oktober 2024.
    if chatter == "Lars":

        archives_to_check = (
            allowed_archives[:20]
        )

    # De rest alleen 2026.
    else:

        archives_to_check = [
            archive
            for archive in allowed_archives
            if archive_year_month(
                archive
            )[0] == 2026
        ][:12]

    random.shuffle(
        archives_to_check
    )

    suitable = []

    for archive in archives_to_check:

        games = get_games(
            archive
        )

        if not games:
            continue

        random.shuffle(
            games
        )

        for game in games:

            if is_suitable_game(
                game,
                chatter,
                start_date
            ):

                suitable.append(
                    game
                )

                if len(suitable) >= 20:

                    return suitable

    log(
        f"{chatter}: found "
        f"{len(suitable)} suitable games."
    )

    return suitable


# =========================================================
# CHOOSE RANDOM GAME
# =========================================================

def choose_game_sync():

    players = list(
        PLAYERS.items()
    )

    random.shuffle(
        players
    )

    candidates = []

    for chatter, config in players:

        try:

            games = find_games_for_player(
                chatter,
                config
            )

            for game in games:

                candidates.append(
                    (
                        chatter,
                        config,
                        game
                    )
                )

        except Exception as error:

            log(
                f"Error searching "
                f"{chatter}: {error}"
            )

    if not candidates:

        raise RuntimeError(
            "No suitable rated Rapid/Blitz "
            "games found."
        )

    selected = random.choice(
        candidates
    )

    log(
        f"Selected player: "
        f"{selected[0]}"
    )

    return selected


# =========================================================
# BUILD ROUND
# =========================================================

def build_round(
    selected
):

    chatter, config, game = selected

    parsed = chess.pgn.read_game(
        StringIO(
            game["pgn"]
        )
    )

    if parsed is None:

        raise RuntimeError(
            "Could not parse PGN."
        )

    moves = list(
        parsed.mainline_moves()
    )

    if len(moves) < (
        MIN_FULL_MOVES * 2
    ):

        raise RuntimeError(
            "Selected game is too short."
        )

    # -----------------------------------------------------
    # BUILD ALL BOARD POSITIONS
    # -----------------------------------------------------

    board = parsed.board()

    positions = [
        board.copy()
    ]

    for move in moves:

        board.push(move)

        positions.append(
            board.copy()
        )

    username = config[
        "username"
    ]

    white = game.get(
        "white",
        {}
    )

    black = game.get(
        "black",
        {}
    )

    white_username = str(
        white.get(
            "username",
            ""
        )
    )

    black_username = str(
        black.get(
            "username",
            ""
        )
    )

    # -----------------------------------------------------
    # FIND PLAYER COLOR
    # -----------------------------------------------------

    if (
        white_username.lower()
        == username.lower()
    ):

        player_color = chess.WHITE

        opponent_name = black.get(
            "username",
            "Unknown"
        )

        opponent_rating = black.get(
            "rating"
        )

    elif (
        black_username.lower()
        == username.lower()
    ):

        player_color = chess.BLACK

        opponent_name = white.get(
            "username",
            "Unknown"
        )

        opponent_rating = white.get(
            "rating"
        )

    else:

        raise RuntimeError(
            f"Could not identify "
            f"{username} in selected game."
        )

    return {

        "answer":
            chatter,

        "username":
            username,

        "positions":
            positions,

        "moves":
            moves,

        "move_count":
            len(moves),

        "player_color":
            player_color,

        "opponent_name":
            opponent_name,

        "opponent_rating":
            opponent_rating,

        "game_type":
            str(
                game.get(
                    "time_class",
                    ""
                )
            ).upper(),

        "winner_user_id":
            None,

        "winner_name":
            None,

        "answered_users":
            set(),

        "poll_message_id":
            None,

        "board_message_id":
            None,

        "rendered_boards":
            []
    }


# =========================================================
# RENDER BOARD
# =========================================================

def render_board_sync(
    board,
    orientation
):

    svg = chess.svg.board(
        board=board,
        orientation=orientation,
        size=520,
        coordinates=True
    )

    return cairosvg.svg2png(
        bytestring=svg.encode(
            "utf-8"
        )
    )


async def prerender_boards(
    game
):

    total = len(
        game["positions"]
    )

    log(
        f"Rendering {total} board positions..."
    )

    rendered = []

    batch_size = 8

    for start in range(
        0,
        total,
        batch_size
    ):

        batch = game[
            "positions"
        ][
            start:
            start + batch_size
        ]

        results = await asyncio.gather(
            *[
                asyncio.to_thread(
                    render_board_sync,
                    board,
                    game["player_color"]
                )
                for board in batch
            ]
        )

        rendered.extend(
            results
        )

    game[
        "rendered_boards"
    ] = rendered

    log(
        "Board rendering finished."
    )


# =========================================================
# EMBED
# =========================================================

def make_embed(
    game,
    move_index
):

    if move_index == 0:

        move_text = (
            "Starting position"
        )

    else:

        move_text = (
            f"Move {move_index} / "
            f"{game['move_count']}"
        )

    player_side = (
        "White"
        if game["player_color"]
        == chess.WHITE
        else "Black"
    )

    opponent_rating = (
        game["opponent_rating"]
        if game["opponent_rating"]
        is not None
        else "?"
    )

    embed = discord.Embed(
        title="Guess the Chess Chatter",
        description=(
            "**Who played this game?**\n\n"
            "White: **[REDACTED]**\n"
            "Black: **[REDACTED]**\n\n"
            f"Opponent: **"
            f"{game['opponent_name']}"
            f" ({opponent_rating})**\n"
            f"Your POV: **{player_side}**\n"
            f"**{move_text}**"
        ),
        color=0x5865F2
    )

    embed.set_footer(
        text=(
            f"{game['game_type']} • "
            "Use ◀️ / ▶️ to navigate"
        )
    )

    return embed


# =========================================================
# BOARD BUTTONS
# =========================================================

class BoardView(
    discord.ui.View
):

    def __init__(
        self,
        game
    ):

        super().__init__(
            timeout=ROUND_SECONDS
        )

        self.game = game

        self.move_index = 0

        self.message = None

        self.update_buttons()

    def update_buttons(
        self
    ):

        self.previous.disabled = (
            self.move_index <= 0
        )

        self.next.disabled = (
            self.move_index
            >= self.game["move_count"]
        )

    async def update_board(
        self,
        interaction
    ):

        image = self.game[
            "rendered_boards"
        ][
            self.move_index
        ]

        file = discord.File(
            BytesIO(image),
            filename="board.png"
        )

        embed = make_embed(
            self.game,
            self.move_index
        )

        embed.set_image(
            url="attachment://board.png"
        )

        self.update_buttons()

        await interaction.response.edit_message(
            embed=embed,
            attachments=[file],
            view=self
        )

    @discord.ui.button(
        emoji="◀️",
        style=discord.ButtonStyle.secondary,
        custom_id="gct_previous"
    )
    async def previous(
        self,
        interaction,
        button
    ):

        if self.move_index <= 0:

            await interaction.response.defer()

            return

        self.move_index -= 1

        await self.update_board(
            interaction
        )

    @discord.ui.button(
        emoji="▶️",
        style=discord.ButtonStyle.secondary,
        custom_id="gct_next"
    )
    async def next(
        self,
        interaction,
        button
    ):

        if (
            self.move_index
            >= self.game["move_count"]
        ):

            await interaction.response.defer()

            return

        self.move_index += 1

        await self.update_board(
            interaction
        )

    async def on_timeout(
        self
    ):

        for item in self.children:

            item.disabled = True

        if self.message:

            try:

                await self.message.edit(
                    view=self
                )

            except Exception:
                pass


# =========================================================
# POLL OPTIONS
# =========================================================

def make_options(
    correct
):

    others = [
        name
        for name in PLAYERS
        if name != correct
    ]

    options = random.sample(
        others,
        4
    )

    options.append(
        correct
    )

    random.shuffle(
        options
    )

    return options


# =========================================================
# CREATE POLL
# =========================================================

def make_poll(
    options
):

    # IMPORTANT:
    #
    # Discord/discord.py needs a timedelta.
    #
    # We use 1 hour technically because Discord
    # does not accept a 10-minute poll duration
    # through this API.
    #
    # The bot manually closes it after 10 minutes.

    poll = discord.Poll(
        question="Who played this game?",
        duration=timedelta(hours=1),
        multiple=False
    )

    for option in options:

        poll.add_answer(
            text=option
        )

    return poll


# =========================================================
# SCORE HELPERS
# =========================================================

def ensure_player(
    user
):

    user_id = str(
        user.id
    )

    if user_id not in scores:

        scores[user_id] = {
            "name":
                user.display_name,

            "points":
                0
        }

    else:

        scores[user_id][
            "name"
        ] = user.display_name

    return user_id


def get_score(
    user_id
):

    return scores.get(
        str(user_id),
        {}
    ).get(
        "points",
        0
    )


# =========================================================
# PERSONAL RANKING
# =========================================================

def personal_leaderboard(
    user_id
):

    players = []

    for uid, data in scores.items():

        players.append({

            "id":
                str(uid),

            "name":
                data.get(
                    "name",
                    "Unknown"
                ),

            "points":
                data.get(
                    "points",
                    0
                )
        })

    players.sort(
        key=lambda p: (
            -p["points"],
            p["name"].lower()
        )
    )

    position = None

    for index, player in enumerate(
        players
    ):

        if player["id"] == str(
            user_id
        ):

            position = index

            break

    if position is None:

        return ""

    start = max(
        0,
        position - 1
    )

    end = min(
        len(players),
        position + 2
    )

    lines = [
        "",
        "📊 **Your ranking**"
    ]

    for index in range(
        start,
        end
    ):

        player = players[
            index
        ]

        if player["id"] == str(
            user_id
        ):

            lines.append(
                f"**#{index + 1} "
                f"{player['name']} — "
                f"{player['points']} "
                f"points ← you**"
            )

        else:

            lines.append(
                f"#{index + 1} "
                f"{player['name']} — "
                f"{player['points']} points"
            )

    return "\n".join(
        lines
    )


# =========================================================
# FULL LEADERBOARD
# =========================================================

def full_leaderboard():

    if not scores:

        return (
            "🏆 **Guess the Chess Chatter "
            "Leaderboard**\n\n"
            "No points yet."
        )

    players = sorted(
        scores.values(),
        key=lambda p: (
            -p.get(
                "points",
                0
            ),
            p.get(
                "name",
                "Unknown"
            ).lower()
        )
    )

    lines = [
        "🏆 **Guess the Chess Chatter "
        "Leaderboard**",
        ""
    ]

    for rank, player in enumerate(
        players,
        1
    ):

        lines.append(
            f"**{rank}.** "
            f"{player.get('name', 'Unknown')} — "
            f"**{player.get('points', 0)} points**"
        )

    return "\n".join(
        lines
    )


# =========================================================
# PROCESS VOTE
# =========================================================

async def process_vote(
    user,
    answer
):

    global active_round

    if active_round is None:

        return

    game = active_round

    if game.get(
        "poll_message_id"
    ) is None:

        return

    user_id = ensure_player(
        user
    )

    # -----------------------------------------------------
    # ONLY ONE ANSWER PER USER
    # -----------------------------------------------------

    if user_id in game[
        "answered_users"
    ]:

        return

    game[
        "answered_users"
    ].add(
        user_id
    )

    try:

        selected_name = (
            answer.media.text
        )

    except Exception as error:

        log(
            f"Could not read poll answer: "
            f"{error}"
        )

        return

    log(
        f"Vote: {user.display_name} "
        f"-> {selected_name}"
    )

    # -----------------------------------------------------
    # WRONG
    # -----------------------------------------------------

    if selected_name != game[
        "answer"
    ]:

        try:

            await user.send(
                "❌ **Wrong!**"
            )

        except Exception:

            pass

        return

    # -----------------------------------------------------
    # FIRST CORRECT PERSON ONLY
    # -----------------------------------------------------

    async with round_lock:

        if game[
            "winner_user_id"
        ] is not None:

            return

        game[
            "winner_user_id"
        ] = user_id

        game[
            "winner_name"
        ] = user.display_name

        scores[user_id][
            "points"
        ] = scores[user_id].get(
            "points",
            0
        ) + 1

        save_scores()

    points = get_score(
        user_id
    )

    ranking = personal_leaderboard(
        user_id
    )

    result = (
        f"✅ **Correct, "
        f"{user.display_name}!**\n"
        f"**+1 point** — you now have "
        f"**{points} points.**"
        f"\n{ranking}"
    )

    # DM first
    try:

        await user.send(
            result
        )

    except Exception:

        # If DMs disabled, send in channel.
        channel = client.get_channel(
            CHANNEL_ID
        )

        if channel:

            await channel.send(
                result
            )


# =========================================================
# POLL EVENTS
# =========================================================

@client.event
async def on_poll_vote_add(
    user,
    answer
):

    try:

        await process_vote(
            user,
            answer
        )

    except Exception:

        log(
            "POLL VOTE ERROR:"
        )

        traceback.print_exc()


@client.event
async def on_poll_vote_remove(
    user,
    answer
):

    # Do not remove points.
    pass


# =========================================================
# HELP / INFO
# =========================================================

HELP_TEXT = """♟️ **Guess the Chess Chatter**

A random **rated Rapid or Blitz** Chess.com game is shown.

Use **◀️ / ▶️** to go through the game.

Then vote in the poll for who you think played it.

**Commands**
`!chesschatter` — Start a round
`!cchatter` — Start a round
`!help` — Show this message
`!info` — Show this message

**Game rules**
• Rated games only
• Rapid or Blitz
• Minimum 10 full moves
• Lars: games from 31 October 2024 onward
• Everyone else: games from 2026

**Points**
The first correct voter gets **+1 point**.
"""


# =========================================================
# POST ROUND
# =========================================================

async def post_round(
    channel
):

    global active_round

    log(
        "Finding suitable rated game..."
    )

    selected = await asyncio.to_thread(
        choose_game_sync
    )

    game = build_round(
        selected
    )

    # -----------------------------------------------------
    # PRE-RENDER ALL BOARDS
    # -----------------------------------------------------

    await prerender_boards(
        game
    )

    # -----------------------------------------------------
    # CREATE POLL
    # -----------------------------------------------------

    options = make_options(
        game["answer"]
    )

    poll = make_poll(
        options
    )

    # -----------------------------------------------------
    # BOARD VIEW
    # -----------------------------------------------------

    view = BoardView(
        game
    )

    # -----------------------------------------------------
    # FIRST BOARD
    # -----------------------------------------------------

    image = game[
        "rendered_boards"
    ][0]

    board_file = discord.File(
        BytesIO(image),
        filename="board.png"
    )

    embed = make_embed(
        game,
        0
    )

    embed.set_image(
        url="attachment://board.png"
    )

    # -----------------------------------------------------
    # SET ACTIVE
    # -----------------------------------------------------

    active_round = game

    try:

        # =================================================
        # MESSAGE 1:
        # BOARD ONLY
        # =================================================

        board_message = await channel.send(
            embed=embed,
            file=board_file,
            view=view
        )

        view.message = board_message

        game[
            "board_message_id"
        ] = board_message.id

        # =================================================
        # MESSAGE 2:
        # POLL ONLY
        #
        # IMPORTANT:
        # NO ATTACHMENT HERE.
        #
        # This is exactly the same structure as the
        # working Guess the Chatter poll.
        # =================================================

        poll_message = await channel.send(
            poll=poll
        )

        game[
            "poll_message_id"
        ] = poll_message.id

    except Exception:

        active_round = None

        raise

    log(
        f"ROUND STARTED | "
        f"answer={game['answer']} | "
        f"opponent={game['opponent_name']} "
        f"({game['opponent_rating']}) | "
        f"type={game['game_type']} | "
        f"moves={game['move_count']}"
    )


# =========================================================
# END ROUND
# =========================================================

async def end_round(
    channel
):

    global active_round

    if active_round is None:

        return

    game = active_round

    log(
        "Ending round..."
    )

    # -----------------------------------------------------
    # END POLL
    # -----------------------------------------------------

    poll_message_id = game.get(
        "poll_message_id"
    )

    if poll_message_id:

        try:

            poll_message = (
                await channel.fetch_message(
                    poll_message_id
                )
            )

            if poll_message.poll:

                await poll_message.end_poll()

                log(
                    "Poll ended successfully."
                )

        except Exception as error:

            log(
                f"Could not end poll: "
                f"{error}"
            )

    # -----------------------------------------------------
    # RESULT
    # -----------------------------------------------------

    winner = game.get(
        "winner_name"
    )

    if winner:

        await channel.send(
            f"🏆 **{winner}** got it first!\n"
            f"The correct chatter was "
            f"**{game['answer']}**."
        )

    else:

        await channel.send(
            f"⏱️ **Round over!**\n"
            f"The correct chatter was "
            f"**{game['answer']}**."
        )

    # -----------------------------------------------------
    # FULL LEADERBOARD
    # -----------------------------------------------------

    await channel.send(
        full_leaderboard()
    )

    active_round = None


# =========================================================
# AUTOMATIC ROUND
# =========================================================

async def automatic_round(
    channel
):

    global active_round

    try:

        # Create round
        await post_round(
            channel
        )

        # Keep it alive for exactly 10 minutes.
        await asyncio.sleep(
            ROUND_SECONDS
        )

        # End poll + show result.
        await end_round(
            channel
        )

    except Exception as error:

        log(
            "AUTOMATIC ROUND ERROR:"
        )

        traceback.print_exc()

        active_round = None

        try:

            await channel.send(
                "❌ **Chess Chatter error**\n"
                f"```{str(error)[:1200]}```"
            )

        except Exception:
            pass

    finally:

        await client.close()


# =========================================================
# COMMANDS
# =========================================================

@client.event
async def on_message(
    message
):

    if message.author.bot:

        return

    if message.channel.id != CHANNEL_ID:

        return

    command = message.content.strip().lower()

    # -----------------------------------------------------
    # HELP
    # -----------------------------------------------------

    if command in (
        "!help",
        "!info"
    ):

        await message.channel.send(
            HELP_TEXT
        )

        return

    # -----------------------------------------------------
    # MANUAL START
    # -----------------------------------------------------

    if command in (
        "!chesschatter",
        "!cchatter"
    ):

        if active_round is not None:

            await message.channel.send(
                "A Chess Chatter round is "
                "already active."
            )

            return

        await message.channel.send(
            "⏳ Finding a rated "
            "Rapid/Blitz game..."
        )

        try:

            await post_round(
                message.channel
            )

        except Exception as error:

            log(
                "MANUAL ROUND ERROR:"
            )

            traceback.print_exc()

            await message.channel.send(
                "❌ Could not create the "
                "Chess Chatter round.\n"
                f"```{str(error)[:1000]}```"
            )


# =========================================================
# READY
# =========================================================

@client.event
async def on_ready():

    global scores
    global round_started

    scores = load_scores()

    log(
        f"Logged in as {client.user}"
    )

    log(
        f"discord.py version: "
        f"{discord.__version__}"
    )

    if round_started:

        return

    round_started = True

    try:

        channel = await client.fetch_channel(
            CHANNEL_ID
        )

    except Exception as error:

        log(
            f"Could not fetch channel: "
            f"{error}"
        )

        traceback.print_exc()

        await client.close()

        return

    log(
        f"Channel found: {channel.name}"
    )

    asyncio.create_task(
        automatic_round(
            channel
        )
    )


# =========================================================
# START
# =========================================================

log(
    "Starting Guess the Chess Chatter..."
)

client.run(
    TOKEN
)
