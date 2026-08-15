import asyncio
import io
import os
import random
from datetime import datetime, timezone, timedelta

import cairosvg
import chess
import chess.pgn
import chess.svg
import discord
import requests



intents = discord.Intents.default()
intents.message_content = True

client = discord.Client(
    intents=intents
)

from shared_leaderboard import (
    add_points,
    full_leaderboard,
    personal_ranking,
)

TOKEN = os.getenv(
    "DISCORD_TOKEN"
)

CHANNEL_ID = 1536769340970373241

POLL_OPTIONS = 5
POLL_DURATION_MINUTES = 15

# The players supplied for Guess the Chess Chatter.
PLAYERS = [
    ("Shark", "Sharkmeister"),
    ("Lars", "Lars11111"),
    ("Mohammad", "Moh979xx"),
    ("Stepu", "T-VoltioS"),
    ("Thice", "Thice"),
    ("Adelson", "Adelson7"),
    ("Nairyaaa", "Naiiiraaa"),
    ("Pospos", "pospos12"),
    ("Pandarou", "iAmPandaro"),
    ("Sushi", "IsolatedSushi"),
]

LARS_START = datetime(
    2024,
    10,
    31,
    tzinfo=timezone.utc
)

GAME_YEAR = 2026

# At least 10 moves = 20 plies.
MIN_PLIES = 20


def fetch_json(
    url
):

    response = requests.get(
        url,
        headers={
            "User-Agent":
                "GuessTheChessChatter/2.0"
        },
        timeout=20
    )

    response.raise_for_status()

    return response.json()


def month_range_for_player(
    display_name
):

    if display_name == "Lars":

        months = [
            (
                2024,
                10
            )
        ]

        for year in (
            2025,
            2026
        ):

            for month in range(
                1,
                13
            ):

                months.append(
                    (
                        year,
                        month
                    )
                )

        return months

    return [
        (
            GAME_YEAR,
            month
        )
        for month in range(
            1,
            13
        )
    ]


def parse_pgn(
    pgn
):

    try:

        game = chess.pgn.read_game(
            io.StringIO(pgn)
        )

        return game

    except Exception:

        return None


def game_date_from_pgn(
    pgn
):

    game = parse_pgn(
        pgn
    )

    if game is None:
        return None

    raw_date = game.headers.get(
        "UTCDate",
        "1900.01.01"
    )

    raw_time = game.headers.get(
        "UTCTime",
        "00:00:00"
    )

    try:

        return datetime.strptime(
            f"{raw_date} {raw_time}",
            "%Y.%m.%d %H:%M:%S"
        ).replace(
            tzinfo=timezone.utc
        )

    except ValueError:

        return None


def is_qualifying_game(
    game
):

    rated_value = game.get(
        "rated",
        False
    )

    is_rated = (
        rated_value is True
        or str(
            rated_value
        ).casefold() in {
            "true",
            "rated",
            "1"
        }
    )

    if not is_rated:
        return False

    time_class = str(
        game.get(
            "time_class",
            ""
        )
    ).casefold()

    if time_class not in {
        "rapid",
        "blitz"
    }:

        return False

    pgn = game.get(
        "pgn",
        ""
    )

    if not pgn:
        return False

    parsed = parse_pgn(
        pgn
    )

    if parsed is None:
        return False

    plies = sum(
        1
        for _
        in parsed.mainline_moves()
    )

    return plies >= MIN_PLIES


def fetch_player_games(
    display_name,
    username
):

    games = []

    for year, month in (
        month_range_for_player(
            display_name
        )
    ):

        try:

            url = (
                "https://api.chess.com/"
                "pub/player/"
                f"{username}/games/"
                f"{year}/{month:02d}"
            )

            data = fetch_json(
                url
            )

        except Exception as error:

            print(
                f"Could not load "
                f"{username} "
                f"{year}-{month:02d}: "
                f"{error}",
                flush=True
            )

            continue

        for game in data.get(
            "games",
            []
        ):

            if not is_qualifying_game(
                game
            ):
                continue

            pgn = game.get(
                "pgn",
                ""
            )

            game_date = (
                game_date_from_pgn(
                    pgn
                )
            )

            if game_date is None:
                continue

            if display_name == "Lars":

                if game_date < (
                    LARS_START
                ):
                    continue

            else:

                if game_date.year != (
                    GAME_YEAR
                ):
                    continue

            game[
                "_owner_display_name"
            ] = display_name

            game[
                "_owner_username"
            ] = username

            game[
                "_game_date"
            ] = game_date

            games.append(
                game
            )

    return games


def collect_games():

    games = []

    for display_name, username in PLAYERS:

        games.extend(
            fetch_player_games(
                display_name,
                username
            )
        )

    return games


def opponent_for_game(
    game
):

    owner_username = game[
        "_owner_username"
    ]

    white = game.get(
        "white",
        {}
    )

    black = game.get(
        "black",
        {}
    )

    if (
        str(
            white.get(
                "username",
                ""
            )
        ).casefold()
        == owner_username.casefold()
    ):

        return (
            black.get(
                "username",
                "Unknown"
            ),
            black.get(
                "rating"
            )
        )

    return (
        white.get(
            "username",
            "Unknown"
        ),
        white.get(
            "rating"
        )
    )


def make_board_file(
    pgn,
    move_index,
    owner_is_white
):

    game = parse_pgn(
        pgn
    )

    if game is None:
        raise RuntimeError(
            "Could not parse PGN."
        )

    board = game.board()

    moves = list(
        game.mainline_moves()
    )

    move_index = max(
        0,
        min(
            move_index,
            len(moves)
        )
    )

    for move in moves[
        :move_index
    ]:

        board.push(
            move
        )

    orientation = (
        chess.WHITE
        if owner_is_white
        else chess.BLACK
    )

    svg = chess.svg.board(
        board=board,
        orientation=orientation,
        coordinates=True,
        size=600
    )

    png = cairosvg.svg2png(
        bytestring=svg.encode(
            "utf-8"
        )
    )

    return (
        discord.File(
            io.BytesIO(png),
            filename="chess_chatter_board.png"
        ),
        len(moves)
    )


class ChessView(
    discord.ui.View
):

    def __init__(
        self,
        pgn,
        owner_is_white,
        total_moves
    ):

        super().__init__(
            timeout=None
        )

        self.pgn = pgn
        self.owner_is_white = (
            owner_is_white
        )

        self.total_moves = (
            total_moves
        )

        self.move_index = 0

        self.message = None

    async def redraw(
        self,
        interaction
    ):

        file, total = make_board_file(
            self.pgn,
            self.move_index,
            self.owner_is_white
        )

        self.total_moves = total

        back_button = self.children[0]
        forward_button = self.children[1]

        back_button.disabled = (
            self.move_index <= 0
        )

        forward_button.disabled = (
            self.move_index >= total
        )

        embed = (
            self.message.embeds[0]
            .copy()
        )

        embed.description = (
            f"**Move "
            f"{self.move_index} / "
            f"{total}**\n"
            f"POV: **"
            f"{'White' if self.owner_is_white else 'Black'}"
            f"**"
        )

        embed.set_image(
            url="attachment://chess_chatter_board.png"
        )

        await interaction.response.edit_message(
            embed=embed,
            view=self,
            attachments=[
                file
            ]
        )

    @discord.ui.button(
        label="◀",
        style=discord.ButtonStyle.secondary
    )
    async def back(
        self,
        interaction,
        button
    ):

        if self.move_index > 0:

            self.move_index -= 1

        await self.redraw(
            interaction
        )

    @discord.ui.button(
        label="▶",
        style=discord.ButtonStyle.secondary
    )
    async def forward(
        self,
        interaction,
        button
    ):

        if (
            self.move_index
            < self.total_moves
        ):

            self.move_index += 1

        await self.redraw(
            interaction
        )


async def post_chess_round(
    channel
):

    games = await asyncio.to_thread(
        collect_games
    )

    if not games:

        await channel.send(
            "❌ **Chess Chatter:** "
            "could not find a qualifying "
            "rated rapid/blitz game."
        )

        return

    game = random.choice(
        games
    )

    owner = game[
        "_owner_display_name"
    ]

    owner_username = game[
        "_owner_username"
    ]

    white = game.get(
        "white",
        {}
    )

    owner_is_white = (
        str(
            white.get(
                "username",
                ""
            )
        ).casefold()
        == owner_username.casefold()
    )

    opponent, opponent_rating = (
        opponent_for_game(
            game
        )
    )

    pgn = game[
        "pgn"
    ]

    game_date = game[
        "_game_date"
    ]

    game_type = str(
        game.get(
            "time_class",
            "unknown"
        )
    ).title()

    file, total_moves = (
        make_board_file(
            pgn,
            0,
            owner_is_white
        )
    )

    wrong_options = [
        name
        for name, _
        in PLAYERS
        if name != owner
    ]

    wrong_options = random.sample(
        wrong_options,
        POLL_OPTIONS - 1
    )

    options = (
        wrong_options
        + [owner]
    )

    random.shuffle(
        options
    )

    correct_index = options.index(
        owner
    )

    poll = discord.Poll(
        question="Who played this game?",
        duration=timedelta(
            hours=1
        ),
        multiple=False
    )

    for option in options:

        poll.add_answer(
            text=option
        )

    embed = discord.Embed(
        title=(
            "♟️ **Guess the Chess Chatter**"
        ),
        description=(
            f"Opponent: **{opponent}** "
            f"({opponent_rating or 'unknown'} Elo)\n"
            f"Type: **{game_type}**\n"
            f"Your POV: **"
            f"{'White' if owner_is_white else 'Black'}"
            f"**\n"
            f"Move **0 / {total_moves}**"
        ),
        color=0x3498db
    )

    embed.set_image(
        url="attachment://chess_chatter_board.png"
    )

    view = ChessView(
        pgn,
        owner_is_white,
        total_moves
    )

    poll_message = await channel.send(
        content=(
            "♟️ **Guess the Chess Chatter** — "
            "vote in the poll above."
        ),
        poll=poll
    )

    board_message = await channel.send(
        embed=embed,
        file=file,
        view=view
    )

    view.message = board_message

    await asyncio.sleep(
        POLL_DURATION_MINUTES * 60 + 3
    )

    try:
        await poll_message.end_poll()
    except discord.HTTPException:
        pass

    try:

        voters_by_answer = []

        for answer in poll.answers:

            answer_voters = []

            async for voter in (
                answer.voters()
            ):

                answer_voters.append(
                    voter
                )

            voters_by_answer.append(
                answer_voters
            )

    except Exception as error:

        voters_by_answer = []

        print(
            f"Chess poll result "
            f"error: {error}",
            flush=True
        )

    if (
        voters_by_answer
        and correct_index
        < len(voters_by_answer)
    ):

        seen = set()

        for voter in voters_by_answer[
            correct_index
        ]:

            if voter.bot:
                continue

            if voter.id in seen:
                continue

            seen.add(
                voter.id
            )

            total = add_points(
                voter.id,
                voter.display_name,
                1
            )

            await channel.send(
                f"✅ **Correct, "
                f"{voter.display_name}!**\n"
                f"**+1 point** — you now have "
                f"**{total:g} points.**"
            )

            ranking = (
                personal_ranking(
                    voter.id
                )
            )

            if ranking:
                await channel.send(
                    ranking
                )

    await channel.send(
        f"🔓 **The answer was:** "
        f"||{owner}||"
    )


async def chess_chatter_loop():

    channel = await client.fetch_channel(
        CHANNEL_ID
    )

    while True:

        started = asyncio.get_running_loop().time()

        try:

            print(
                "Starting Chess Chatter round...",
                flush=True
            )

            await post_chess_round(
                channel
            )

            print(
                "Chess Chatter round finished.",
                flush=True
            )

        except Exception as error:

            print(
                f"Guess Chess Chatter round error: "
                f"{error}",
                flush=True
            )

        elapsed = (
            asyncio.get_running_loop().time()
            - started
        )

        wait_seconds = max(
            5,
            20 * 60 - elapsed
        )

        await asyncio.sleep(
            wait_seconds
        )


@client.event
async def on_ready():

    print(
        f"Guess Chess Chatter ready as "
        f"{client.user}",
        flush=True
    )

    if not hasattr(
        client,
        "_chess_chatter_task"
    ) or client._chess_chatter_task.done():

        client._chess_chatter_task = (
            asyncio.create_task(
                chess_chatter_loop()
            )
        )


client.run(
    TOKEN
)
