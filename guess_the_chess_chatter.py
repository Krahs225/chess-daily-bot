import discord
import requests
import chess
import chess.pgn
import chess.svg
import cairosvg

from io import StringIO, BytesIO
from datetime import datetime, timezone
import asyncio
import random
import os
import json
import time


# =========================================================
# SETTINGS
# =========================================================

TOKEN = os.getenv("DISCORD_TOKEN")

CHANNEL_ID = 1537837944193417300

ROUND_MINUTES = 10
ROUND_SECONDS = ROUND_MINUTES * 60

MIN_FULL_MOVES = 10

SCORES_FILE = "chess_chatter_scores.json"

HEADERS = {
    "User-Agent": "GuessTheChessChatter/2.0"
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

# Native Discord Poll vote events.
try:
    intents.polls = True
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


# =========================================================
# JSON
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

        print(
            f"Could not load scores: {error}",
            flush=True
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

    except Exception as error:

        print(
            f"Could not save scores: {error}",
            flush=True
        )


# =========================================================
# CHESS.COM REQUEST
# =========================================================

def get_json(url):

    try:

        response = requests.get(
            url,
            headers=HEADERS,
            timeout=15
        )

        if response.status_code != 200:

            print(
                f"HTTP {response.status_code}: {url}",
                flush=True
            )

            return None

        return response.json()

    except Exception as error:

        print(
            f"Request error: {error}",
            flush=True
        )

        return None


# =========================================================
# GET ARCHIVES
# =========================================================

def get_archives(
    username
):

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


# =========================================================
# ARCHIVE IS ALLOWED
# =========================================================

def archive_is_allowed(
    archive,
    start_date
):

    parts = archive.rstrip(
        "/"
    ).split("/")

    try:

        year = int(
            parts[-2]
        )

        month = int(
            parts[-1]
        )

    except Exception:

        return False

    start_year = start_date.year
    start_month = start_date.month

    if year < start_year:
        return False

    if (
        year == start_year
        and month < start_month
    ):
        return False

    return True


# =========================================================
# GET MONTHLY GAMES
# =========================================================

def get_games(
    archive
):

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

def get_game_date(
    game
):

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
# GAME TYPE
# =========================================================

def allowed_game_type(
    game
):

    time_class = str(
        game.get(
            "time_class",
            ""
        )
    ).lower()

    return time_class in (
        "rapid",
        "blitz"
    )


# =========================================================
# PARSE GAME
# =========================================================

def parse_game(
    game
):

    pgn_text = game.get(
        "pgn"
    )

    if not pgn_text:
        return None

    try:

        parsed = chess.pgn.read_game(
            StringIO(pgn_text)
        )

        if parsed is None:
            return None

        moves = list(
            parsed.mainline_moves()
        )

        # At least 10 full moves.
        if len(moves) < MIN_FULL_MOVES * 2:
            return None

        return parsed, moves

    except Exception as error:

        print(
            f"PGN parse error: {error}",
            flush=True
        )

        return None


# =========================================================
# GET SUITABLE GAME
#
# Faster than downloading every game from every player.
# We randomly choose a player + eligible month and then
# look for a suitable game.
# =========================================================

def choose_game_sync():

    player_names = list(
        PLAYERS.keys()
    )

    random.shuffle(
        player_names
    )

    for chatter in player_names:

        config = PLAYERS[
            chatter
        ]

        username = config[
            "username"
        ]

        start_date = datetime.strptime(
            config["start_date"],
            "%Y-%m-%d"
        ).date()

        archives = get_archives(
            username
        )

        eligible_archives = [
            archive
            for archive in archives
            if archive_is_allowed(
                archive,
                start_date
            )
        ]

        random.shuffle(
            eligible_archives
        )

        # Try a limited number of archives.
        # This keeps the round startup fast.
        for archive in eligible_archives[
            :8
        ]:

            games = get_games(
                archive
            )

            random.shuffle(
                games
            )

            for game in games:

                if not allowed_game_type(
                    game
                ):
                    continue

                date = get_game_date(
                    game
                )

                if date is None:
                    continue

                if date < start_date:
                    continue

                # Lars can use anything from
                # 31 October 2024 onward.
                #
                # Everyone else must be in 2026.
                if chatter != "Lars":

                    if date.year != 2026:
                        continue

                parsed_data = parse_game(
                    game
                )

                if parsed_data is None:
                    continue

                parsed, moves = parsed_data

                return (
                    chatter,
                    config,
                    game,
                    parsed,
                    moves
                )

    raise RuntimeError(
        "Could not find a suitable game."
    )


# =========================================================
# BUILD ROUND DATA
# =========================================================

def build_round(
    selected
):

    chatter, config, game, parsed, moves = (
        selected
    )

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

    white_username = (
        game.get(
            "white",
            {}
        ).get(
            "username",
            ""
        )
    )

    black_username = (
        game.get(
            "black",
            {}
        ).get(
            "username",
            ""
        )
    )

    if (
        white_username.lower()
        == username.lower()
    ):

        player_color = chess.WHITE

    elif (
        black_username.lower()
        == username.lower()
    ):

        player_color = chess.BLACK

    else:

        # Fallback.
        white_name = parsed.headers.get(
            "White",
            ""
        )

        black_name = parsed.headers.get(
            "Black",
            ""
        )

        if username.lower() in (
            white_name.lower()
        ):

            player_color = chess.WHITE

        elif username.lower() in (
            black_name.lower()
        ):

            player_color = chess.BLACK

        else:

            # Very unusual fallback.
            player_color = chess.WHITE

    return {
        "answer":
            chatter,

        "username":
            username,

        "positions":
            positions,

        "moves":
            moves,

        "player_color":
            player_color,

        "move_count":
            len(moves),

        "white":
            parsed.headers.get(
                "White",
                "Unknown"
            ),

        "black":
            parsed.headers.get(
                "Black",
                "Unknown"
            ),

        "result":
            parsed.headers.get(
                "Result",
                ""
            ),

        "game_type":
            str(
                game.get(
                    "time_class",
                    ""
                )
            ).lower(),

        "game_url":
            game.get(
                "url",
                ""
            ),

        "winner_user_id":
            None,

        "winner_name":
            None,

        "answered_users":
            set(),

        "started_at":
            time.monotonic(),

        "poll_message_id":
            None
    }


# =========================================================
# RENDER ONE BOARD
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

    png = cairosvg.svg2png(
        bytestring=svg.encode(
            "utf-8"
        )
    )

    return png


# =========================================================
# PRE-RENDER ALL BOARDS
# =========================================================

async def prerender_boards(
    game
):

    orientation = game[
        "player_color"
    ]

    boards = game[
        "positions"
    ]

    print(
        f"Rendering {len(boards)} board positions...",
        flush=True
    )

    # Render in batches to avoid hammering CPU.
    rendered = []

    batch_size = 8

    for start in range(
        0,
        len(boards),
        batch_size
    ):

        batch = boards[
            start:
            start + batch_size
        ]

        results = await asyncio.gather(
            *[
                asyncio.to_thread(
                    render_board_sync,
                    board,
                    orientation
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

    print(
        "All boards rendered.",
        flush=True
    )


# =========================================================
# EMBED
# =========================================================

def make_embed(
    game,
    move_index
):

    total = game[
        "move_count"
    ]

    if move_index == 0:

        move_text = (
            "Starting position"
        )

    else:

        move_text = (
            f"Move {move_index} / {total}"
        )

    embed = discord.Embed(
        title="Guess the Chess Chatter",
        description=(
            "**Who played this game?**\n\n"
            "White: **[REDACTED]**\n"
            "Black: **[REDACTED]**\n\n"
            f"Your POV: **"
            f"{'White' if game['player_color'] == chess.WHITE else 'Black'}"
            f"**\n"
            f"**{move_text}**"
        ),
        color=0x5865F2
    )

    return embed


# =========================================================
# BOARD VIEW
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

    async def update_message(
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
        custom_id="chess_previous"
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

        await self.update_message(
            interaction
        )

    @discord.ui.button(
        emoji="▶️",
        style=discord.ButtonStyle.secondary,
        custom_id="chess_next"
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

        await self.update_message(
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

    selected = random.sample(
        others,
        4
    )

    options = selected + [
        correct
    ]

    random.shuffle(
        options
    )

    return options


# =========================================================
# CREATE NATIVE POLL
# =========================================================

def make_poll(
    options
):

    poll = discord.Poll(
        question="Who played this game?",
        duration=1,
        allow_multiselect=False
    )

    for option in options:

        poll.add_answer(
            text=option
        )

    return poll


# =========================================================
# SCORES
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
# PERSONAL LEADERBOARD
# =========================================================

def personal_leaderboard(
    user_id
):

    players = []

    for uid, data in scores.items():

        players.append(
            {
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
            }
        )

    players.sort(
        key=lambda x: (
            -x["points"],
            x["name"].lower()
        )
    )

    index = None

    for i, player in enumerate(
        players
    ):

        if player["id"] == str(
            user_id
        ):

            index = i
            break

    if index is None:
        return ""

    start = max(
        0,
        index - 1
    )

    end = min(
        len(players),
        index + 2
    )

    lines = [
        "",
        "📊 **Your ranking**"
    ]

    for i in range(
        start,
        end
    ):

        player = players[i]

        if player["id"] == str(
            user_id
        ):

            lines.append(
                f"**#{i + 1} "
                f"{player['name']} — "
                f"{player['points']} "
                f"points ← you**"
            )

        else:

            lines.append(
                f"#{i + 1} "
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
        key=lambda x: (
            -x.get(
                "points",
                0
            ),
            x.get(
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

        lines.append(
            f"{prefix} {name} — "
            f"**{points} points**"
        )

    return "\n".join(
        lines
    )


# =========================================================
# AWARD FIRST CORRECT ANSWER
# =========================================================

async def process_vote(
    user,
    answer
):

    global active_round

    if active_round is None:
        return

    game = active_round

    # Ignore votes from outside the active round.
    if (
        game["poll_message_id"]
        is None
    ):
        return

    user_id = ensure_player(
        user
    )

    # Prevent duplicate scoring.
    if user_id in game[
        "answered_users"
    ]:

        return

    game[
        "answered_users"
    ].add(
        user_id
    )

    selected_name = None

    try:

        selected_name = (
            answer.media.text
        )

    except Exception:
        pass

    if not selected_name:
        return

    # =====================================================
    # WRONG ANSWER
    # =====================================================

    if selected_name != game[
        "answer"
    ]:

        try:

            await user.send(
                "❌ **Wrong answer!** "
                "Your vote was not the correct chatter."
            )

        except Exception:
            pass

        return

    # =====================================================
    # FIRST CORRECT ANSWER
    # =====================================================

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

    try:

        await user.send(
            f"✅ **Correct!**\n"
            f"You got **+1 point**.\n"
            f"You now have **{points} points.**"
            f"\n{ranking}"
        )

    except Exception:

        # If DMs are disabled, post a short
        # response in the channel instead.
        channel = client.get_channel(
            CHANNEL_ID
        )

        if channel:

            await channel.send(
                f"✅ **{user.display_name} got it!** "
                f"**+1 point** — now at "
                f"**{points} points**.\n"
                f"{ranking}"
            )


# =========================================================
# POLL VOTE EVENT
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

    except Exception as error:

        print(
            f"Poll vote error: {error}",
            flush=True
        )


# =========================================================
# POLL VOTE REMOVAL
# =========================================================

@client.event
async def on_poll_vote_remove(
    user,
    answer
):

    # We intentionally do nothing here.
    #
    # This is important:
    # changing/removing a vote must never
    # remove a point that was already awarded.
    pass


# =========================================================
# HELP
# =========================================================

HELP_TEXT = """♟️ **Guess the Chess Chatter**

A random Chess.com **Rapid or Blitz** game is shown.

Use **◀️ / ▶️** to move through the game.

Then vote in the poll for who you think played it.

**Commands**
`!chesschatter` — Start a round
`!cchatter` — Start a round
`!help` — Show this message
`!info` — Show this message

Games have at least 10 full moves.

Correct answer = **+1 point**.

Only the **first correct voter** gets the point.
"""


# =========================================================
# POST ROUND
# =========================================================

async def post_round(
    channel
):

    global active_round

    print(
        "Finding Chess.com game...",
        flush=True
    )

    selected = await asyncio.to_thread(
        choose_game_sync
    )

    game = build_round(
        selected
    )

    # -----------------------------------------------------
    # IMPORTANT:
    # Render everything BEFORE sending the round.
    # This makes navigation as fast as possible.
    # -----------------------------------------------------

    await prerender_boards(
        game
    )

    options = make_options(
        game["answer"]
    )

    view = BoardView(
        game
    )

    image = game[
        "rendered_boards"
    ][0]

    file = discord.File(
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

    poll = make_poll(
        options
    )

    active_round = game

    message = await channel.send(
        embed=embed,
        file=file,
        view=view,
        poll=poll
    )

    view.message = message

    game[
        "poll_message_id"
    ] = message.id

    print(
        f"Round posted. Answer: "
        f"{game['answer']}",
        flush=True
    )


# =========================================================
# END ROUND
# =========================================================

async def end_round(
    channel
):

    global active_round

    game = active_round

    if game is None:
        return

    # End native poll.
    message_id = game.get(
        "poll_message_id"
    )

    if message_id:

        try:

            message = await channel.fetch_message(
                message_id
            )

            if message.poll:

                await message.end_poll()

        except Exception as error:

            print(
                f"Could not end poll: {error}",
                flush=True
            )

    winner = game.get(
        "winner_name"
    )

    if winner:

        result_text = (
            f"🏆 **Round winner: "
            f"{winner}**\n"
            f"The correct chatter was "
            f"**{game['answer']}**."
        )

    else:

        result_text = (
            f"⏱️ **Round over!**\n"
            f"The correct chatter was "
            f"**{game['answer']}**."
        )

    await channel.send(
        result_text
    )

    await channel.send(
        full_leaderboard()
    )

    active_round = None


# =========================================================
# ROUND LOOP
# =========================================================

async def round_loop(
    channel
):

    while True:

        try:

            # End previous round.
            if active_round is not None:

                await end_round(
                    channel
                )

            # Start new round.
            await post_round(
                channel
            )

            # Keep it alive for 10 minutes.
            await asyncio.sleep(
                ROUND_SECONDS
            )

        except Exception as error:

            print(
                f"Round loop error: {error}",
                flush=True
            )

            try:

                await channel.send(
                    "❌ Could not create the "
                    "next Chess Chatter round. "
                    "Trying again..."
                )

            except Exception:
                pass

            await asyncio.sleep(
                20
            )


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

    if command in (
        "!help",
        "!info"
    ):

        await message.channel.send(
            HELP_TEXT
        )

        return

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
            "⏳ Getting a Chess.com game..."
        )

        try:

            await post_round(
                message.channel
            )

        except Exception as error:

            print(
                f"Manual round error: {error}",
                flush=True
            )

            await message.channel.send(
                "❌ Could not find a suitable "
                "Chess.com game."
            )


# =========================================================
# READY
# =========================================================

@client.event
async def on_ready():

    global scores

    scores = load_scores()

    print(
        f"Logged in as {client.user}",
        flush=True
    )

    print(
        f"discord.py version: "
        f"{discord.__version__}",
        flush=True
    )

    channel = await client.fetch_channel(
        CHANNEL_ID
    )

    # Start the automatic 10-minute cycle.
    asyncio.create_task(
        round_loop(channel)
    )


# =========================================================
# START
# =========================================================

print(
    "Starting Guess the Chess Chatter...",
    flush=True
)

client.run(TOKEN)
