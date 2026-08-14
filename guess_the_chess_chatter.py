import discord
from discord.ext import tasks
import requests
import chess
import chess.pgn
import chess.svg
import cairosvg

from io import BytesIO, StringIO
from datetime import datetime, timezone
import random
import asyncio
import os


# =========================================================
# SETTINGS
# =========================================================

TOKEN = os.getenv("DISCORD_TOKEN")

CHANNEL_ID = 1537837944193417300

ROUND_TIME = 10 * 60

# Minimum 10 full moves.
# A game therefore needs at least 20 plies.
MIN_FULL_MOVES = 10

HEADERS = {
    "User-Agent":
        "GuessTheChessChatter/1.0"
}


# =========================================================
# CHESS.COM PLAYERS
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
# DISCORD
# =========================================================

intents = discord.Intents.default()
intents.message_content = True

client = discord.Client(
    intents=intents
)


# =========================================================
# HELP
# =========================================================

HELP_TEXT = """♟️ **Guess the Chess Chatter**

A random Chess.com Rapid game is shown.

Use the **◀️ / ▶️** buttons to go through the game.

Then choose who you think played the game.

**Commands**
`!chesschatter` — Start a round
`!cchatter` — Start a round
`!help` — Show this message
`!info` — Show this message

Games are Rapid games with at least 10 moves.
"""


# =========================================================
# HTTP
# =========================================================

def get_json(url):

    try:

        response = requests.get(
            url,
            headers=HEADERS,
            timeout=20
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
# GET PLAYER ARCHIVES
# =========================================================

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


# =========================================================
# GET MONTHLY GAMES
# =========================================================

def get_monthly_games(
    archive_url
):

    data = get_json(
        archive_url
    )

    if not data:
        return []

    return data.get(
        "games",
        []
    )


# =========================================================
# DATE FILTER
# =========================================================

def game_date(game):

    # Chess.com provides end_time.
    # PGN Date is also available.

    end_time = game.get(
        "end_time"
    )

    if end_time:

        try:

            return datetime.fromtimestamp(
                int(end_time),
                timezone.utc
            ).date()

        except Exception:
            pass

    pgn_text = game.get(
        "pgn",
        ""
    )

    try:

        game_pgn = chess.pgn.read_game(
            StringIO(pgn_text)
        )

        if game_pgn:

            date_text = game_pgn.headers.get(
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
# RAPID CHECK
# =========================================================

def is_rapid(game):

    return (
        str(
            game.get(
                "time_class",
                ""
            )
        ).lower()
        == "rapid"
    )


# =========================================================
# GAME LENGTH
# =========================================================

def get_game_moves(game):

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

        if not moves:
            return None

        return moves

    except Exception as error:

        print(
            f"PGN error: {error}",
            flush=True
        )

        return None


# =========================================================
# GET ALL SUITABLE GAMES
# =========================================================

def get_suitable_games(
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

    print(
        f"Searching games for {chatter} "
        f"({username})...",
        flush=True
    )

    archives = get_archives(
        username
    )

    suitable = []

    for archive in archives:

        # Example:
        # .../games/2026/05
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

            continue

        # We only need archives that could
        # contain the requested period.
        if year < start_date.year:
            continue

        if (
            year == start_date.year
            and month < start_date.month
        ):
            continue

        # For non-Lars players we only want 2026.
        if year != 2026:
            if chatter != "Lars":
                continue

        games = get_monthly_games(
            archive
        )

        for game in games:

            if not is_rapid(game):
                continue

            date = game_date(
                game
            )

            if date is None:
                continue

            if date < start_date:
                continue

            if chatter != "Lars":

                if date.year != 2026:
                    continue

            moves = get_game_moves(
                game
            )

            if moves is None:
                continue

            # Minimum 10 full moves.
            if len(moves) < MIN_FULL_MOVES * 2:
                continue

            # Save parsed information so we don't
            # need to parse it again later.
            game_copy = dict(
                game
            )

            game_copy[
                "_moves"
            ] = moves

            game_copy[
                "_date"
            ] = date.isoformat()

            suitable.append(
                game_copy
            )

    print(
        f"{chatter}: "
        f"{len(suitable)} suitable games.",
        flush=True
    )

    return suitable


# =========================================================
# BUILD GAME
# =========================================================

def build_game(
    game,
    chatter
):

    pgn_text = game[
        "pgn"
    ]

    parsed = chess.pgn.read_game(
        StringIO(pgn_text)
    )

    if parsed is None:
        raise RuntimeError(
            "Could not parse game."
        )

    moves = list(
        parsed.mainline_moves()
    )

    board_positions = []

    board = parsed.board()

    # Position 0
    board_positions.append(
        board.copy()
    )

    # Every following position
    for move in moves:

        board.push(move)

        board_positions.append(
            board.copy()
        )

    white_name = (
        parsed.headers.get(
            "White",
            "Unknown"
        )
    )

    black_name = (
        parsed.headers.get(
            "Black",
            "Unknown"
        )
    )

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

    # Determine POV.
    #
    # Use the Chess.com username from the game,
    # rather than comparing display names.
    if (
        white_username.lower()
        == PLAYERS[chatter][
            "username"
        ].lower()
    ):

        player_color = chess.WHITE

    elif (
        black_username.lower()
        == PLAYERS[chatter][
            "username"
        ].lower()
    ):

        player_color = chess.BLACK

    else:

        # Fallback to PGN names.
        player_username = (
            PLAYERS[chatter][
                "username"
            ].lower()
        )

        if (
            player_username
            in white_name.lower()
        ):

            player_color = chess.WHITE

        else:

            player_color = chess.BLACK

    return {
        "chatter":
            chatter,

        "username":
            PLAYERS[chatter][
                "username"
            ],

        "white":
            white_name,

        "black":
            black_name,

        "white_username":
            white_username,

        "black_username":
            black_username,

        "date":
            game.get(
                "_date",
                ""
            ),

        "moves":
            moves,

        "positions":
            board_positions,

        "player_color":
            player_color,

        "url":
            game.get(
                "url",
                ""
            ),

        "result":
            parsed.headers.get(
                "Result",
                ""
            ),

        "headers":
            dict(
                parsed.headers
            )
    }


# =========================================================
# RANDOM GAME
# =========================================================

async def choose_random_game():

    all_possible = []

    for chatter, config in PLAYERS.items():

        try:

            games = await asyncio.to_thread(
                get_suitable_games,
                chatter,
                config
            )

            for game in games:

                all_possible.append(
                    (
                        chatter,
                        game
                    )
                )

        except Exception as error:

            print(
                f"Error loading {chatter}: "
                f"{error}",
                flush=True
            )

    if not all_possible:

        raise RuntimeError(
            "No suitable Rapid games found."
        )

    # Randomize both player and game.
    chatter, game = random.choice(
        all_possible
    )

    return await asyncio.to_thread(
        build_game,
        game,
        chatter
    )


# =========================================================
# RENDER BOARD
# =========================================================

async def render_board(
    game,
    move_index
):

    positions = game[
        "positions"
    ]

    if move_index < 0:
        move_index = 0

    if move_index >= len(
        positions
    ):
        move_index = len(
            positions
        ) - 1

    board = positions[
        move_index
    ]

    orientation = game[
        "player_color"
    ]

    svg = chess.svg.board(
        board=board,
        orientation=orientation,
        size=600,
        coordinates=True
    )

    png = await asyncio.to_thread(
        cairosvg.svg2png,
        bytestring=svg.encode(
            "utf-8"
        )
    )

    return BytesIO(
        png
    )


# =========================================================
# EMBED
# =========================================================

def make_embed(
    game,
    move_index
):

    total_moves = len(
        game["moves"]
    )

    if move_index == 0:

        move_text = "Starting position"

    else:

        move_number = (
            move_index
        )

        move_text = (
            f"Move {move_number} / "
            f"{total_moves}"
        )

    player_side = (
        "White"
        if game["player_color"]
        == chess.WHITE
        else "Black"
    )

    embed = discord.Embed(
        title="🧀 Guess the Chess Chatter",
        description=(
            "**Who played this game?**\n\n"
            f"⚪ White: **{game['white']}**\n"
            f"⚫ Black: **{game['black']}**\n\n"
            f"📅 {game['date']}\n"
            f"🎯 Your POV: **{player_side}**\n"
            f"▶️ **{move_text}**"
        ),
        color=0xF1C40F
    )

    embed.set_footer(
        text=(
            "Use ◀️ and ▶️ to explore the game"
        )
    )

    return embed


# =========================================================
# GAME VIEW
# =========================================================

class GameView(
    discord.ui.View
):

    def __init__(
        self,
        game,
        options
    ):

        # Keep the view alive for the round.
        super().__init__(
            timeout=ROUND_TIME
        )

        self.game = game
        self.options = options
        self.move_index = 0
        self.message = None
        self.answer_locked = False

        # Disable previous at start.
        self.previous_button.disabled = True

    # -----------------------------------------------------
    # PREVIOUS
    # -----------------------------------------------------

    @discord.ui.button(
        emoji="◀️",
        style=discord.ButtonStyle.secondary,
        custom_id="gct_previous"
    )
    async def previous_button(
        self,
        interaction,
        button
    ):

        if self.move_index <= 0:

            await interaction.response.defer()
            return

        self.move_index -= 1

        if self.move_index <= 0:

            button.disabled = True

        self.next_button.disabled = False

        await self.update_board(
            interaction
        )

    # -----------------------------------------------------
    # NEXT
    # -----------------------------------------------------

    @discord.ui.button(
        emoji="▶️",
        style=discord.ButtonStyle.secondary,
        custom_id="gct_next"
    )
    async def next_button(
        self,
        interaction,
        button
    ):

        max_index = len(
            self.game["positions"]
        ) - 1

        if self.move_index >= max_index:

            await interaction.response.defer()
            return

        self.move_index += 1

        if self.move_index >= max_index:

            button.disabled = True

        self.previous_button.disabled = False

        await self.update_board(
            interaction
        )

    # -----------------------------------------------------
    # ANSWERS
    # -----------------------------------------------------

    @discord.ui.button(
        label="A",
        style=discord.ButtonStyle.primary,
        custom_id="gct_answer_a",
        row=1
    )
    async def answer_a(
        self,
        interaction,
        button
    ):

        await self.answer(
            interaction,
            0
        )

    @discord.ui.button(
        label="B",
        style=discord.ButtonStyle.primary,
        custom_id="gct_answer_b",
        row=1
    )
    async def answer_b(
        self,
        interaction,
        button
    ):

        await self.answer(
            interaction,
            1
        )

    @discord.ui.button(
        label="C",
        style=discord.ButtonStyle.primary,
        custom_id="gct_answer_c",
        row=1
    )
    async def answer_c(
        self,
        interaction,
        button
    ):

        await self.answer(
            interaction,
            2
        )

    @discord.ui.button(
        label="D",
        style=discord.ButtonStyle.primary,
        custom_id="gct_answer_d",
        row=1
    )
    async def answer_d(
        self,
        interaction,
        button
    ):

        await self.answer(
            interaction,
            3
        )

    @discord.ui.button(
        label="E",
        style=discord.ButtonStyle.primary,
        custom_id="gct_answer_e",
        row=1
    )
    async def answer_e(
        self,
        interaction,
        button
    ):

        await self.answer(
            interaction,
            4
        )

    # -----------------------------------------------------
    # UPDATE BOARD
    # -----------------------------------------------------

    async def update_board(
        self,
        interaction
    ):

        image = await render_board(
            self.game,
            self.move_index
        )

        file = discord.File(
            image,
            filename="chess_board.png"
        )

        embed = make_embed(
            self.game,
            self.move_index
        )

        embed.set_image(
            url="attachment://chess_board.png"
        )

        await interaction.response.edit_message(
            embed=embed,
            attachments=[file],
            view=self
        )

    # -----------------------------------------------------
    # ANSWER
    # -----------------------------------------------------

    async def answer(
        self,
        interaction,
        index
    ):

        if self.answer_locked:

            await interaction.response.send_message(
                "This round has already been answered.",
                ephemeral=True
            )

            return

        if index >= len(
            self.options
        ):

            await interaction.response.send_message(
                "That answer is unavailable.",
                ephemeral=True
            )

            return

        selected = self.options[
            index
        ]

        correct = (
            selected
            == self.game[
                "chatter"
            ]
        )

        if correct:

            self.answer_locked = True

            # Disable answer buttons.
            for item in self.children:

                if getattr(
                    item,
                    "custom_id",
                    ""
                ).startswith(
                    "gct_answer_"
                ):

                    item.disabled = True

            await interaction.response.send_message(
                f"✅ **Correct!** "
                f"This game was played by "
                f"**{self.game['chatter']}**."
            )

            # Keep board controls active.
            return

        else:

            await interaction.response.send_message(
                "❌ **Wrong!** Try again.",
                ephemeral=True
            )

    # -----------------------------------------------------
    # TIMEOUT
    # -----------------------------------------------------

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
# CREATE 5 OPTIONS
# =========================================================

def make_options(
    correct
):

    others = [
        name
        for name in PLAYERS.keys()
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
# SET BUTTON LABELS
# =========================================================

def set_answer_labels(
    view
):

    answer_buttons = []

    for item in view.children:

        if getattr(
            item,
            "custom_id",
            ""
        ).startswith(
            "gct_answer_"
        ):

            answer_buttons.append(
                item
            )

    letters = [
        "A",
        "B",
        "C",
        "D",
        "E"
    ]

    for button, letter, name in zip(
        answer_buttons,
        letters,
        view.options
    ):

        button.label = (
            f"{letter}: {name}"
        )


# =========================================================
# POST ROUND
# =========================================================

async def post_round(
    channel
):

    print(
        "Choosing random Chess.com game...",
        flush=True
    )

    game = await choose_random_game()

    options = make_options(
        game["chatter"]
    )

    view = GameView(
        game,
        options
    )

    set_answer_labels(
        view
    )

    image = await render_board(
        game,
        0
    )

    file = discord.File(
        image,
        filename="chess_board.png"
    )

    embed = make_embed(
        game,
        0
    )

    embed.set_image(
        url="attachment://chess_board.png"
    )

    message = await channel.send(
        embed=embed,
        file=file,
        view=view
    )

    view.message = message

    print(
        f"Round posted: "
        f"{game['chatter']} "
        f"({game['username']})",
        flush=True
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

    content = message.content.strip()
    command = content.lower()

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

        await message.channel.send(
            "⏳ **Getting a Chess.com Rapid game...**"
        )

        try:

            await post_round(
                message.channel
            )

        except Exception as error:

            print(
                f"Round error: {error}",
                flush=True
            )

            await message.channel.send(
                "❌ Could not find a suitable "
                "Chess.com game right now."
            )


# =========================================================
# READY
# =========================================================

@client.event
async def on_ready():

    print(
        f"Logged in as {client.user}",
        flush=True
    )

    channel = await client.fetch_channel(
        CHANNEL_ID
    )

    # Automatically create a round when
    # the GitHub Action starts.
    try:

        await post_round(
            channel
        )

    except Exception as error:

        print(
            f"Automatic round error: {error}",
            flush=True
        )

        await channel.send(
            "❌ Could not create the "
            "Chess Chatter round."
        )

    # Keep process alive so Discord buttons work.
    await asyncio.sleep(
        ROUND_TIME - 20
    )

    await client.close()


# =========================================================
# START
# =========================================================

print(
    "Starting Guess the Chess Chatter...",
    flush=True
)

client.run(TOKEN)
