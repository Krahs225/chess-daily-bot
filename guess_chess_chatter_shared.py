import asyncio
import io
import os
import random
from datetime import datetime, timezone

import chess
import chess.pgn
import chess.svg
import discord
import requests
import cairosvg

from shared_leaderboard import (
    add_points,
    full_leaderboard,
    personal_ranking,
)

TOKEN = os.getenv("DISCORD_TOKEN")
CHANNEL_ID = 1536769340970373241

# Guess the Chess Chatter runs on :10, :30, :50.
ROUND_MINUTES = {10, 30, 50}
POLL_OPTIONS = 5
POLL_DURATION_MINUTES = 15

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

# Lars is the special historical player:
# all qualifying games from 2024-10-31 onward.
LARS_START = datetime(2024, 10, 31, tzinfo=timezone.utc)

# Everyone else: 2026 only.
GAME_YEAR = 2026

# Minimum number of full moves.
MIN_PLIES = 20


def fetch_json(url):
    response = requests.get(
        url,
        headers={"User-Agent": "GuessTheChessChatter/1.0"},
        timeout=20,
    )
    response.raise_for_status()
    return response.json()


def month_range_for_player(display_name):
    if display_name == "Lars":
        return [(2024, 10)] + [
            (year, month)
            for year in (2025, 2026)
            for month in range(1, 13)
            if (year, month) >= (2024, 11)
        ]

    return [(GAME_YEAR, month) for month in range(1, 13)]


def is_qualifying_game(game):
    # Rated only.
    if str(game.get("rated", "")).lower() != "rated":
        return False

    time_class = str(game.get("time_class", "")).lower()

    # Rapid + blitz are explicitly allowed.
    if time_class not in {"rapid", "blitz"}:
        return False

    pgn = game.get("pgn", "")
    if not pgn:
        return False

    try:
        parsed = chess.pgn.read_game(io.StringIO(pgn))
        if parsed is None:
            return False

        ply_count = sum(1 for _ in parsed.mainline_moves())
        return ply_count >= MIN_PLIES
    except Exception:
        return False


def game_date_from_pgn(pgn):
    try:
        game = chess.pgn.read_game(io.StringIO(pgn))
        if game is None:
            return None
        raw = game.headers.get("UTCDate", "1900.01.01")
        raw_time = game.headers.get("UTCTime", "00:00:00")
        return datetime.strptime(
            f"{raw} {raw_time}",
            "%Y.%m.%d %H:%M:%S",
        ).replace(tzinfo=timezone.utc)
    except Exception:
        return None


def fetch_player_games(display_name, username):
    games = []

    for year, month in month_range_for_player(display_name):
        if display_name != "Lars" and year != GAME_YEAR:
            continue

        if display_name == "Lars":
            month_start = datetime(year, month, 1, tzinfo=timezone.utc)
            if month_start.year == 2024 and month == 10:
                # October is included only from Oct 31 onward.
                pass

        try:
            url = (
                f"https://api.chess.com/pub/player/"
                f"{username}/games/{year}/{month:02d}"
            )
            data = fetch_json(url)
        except Exception:
            continue

        for game in data.get("games", []):
            if not is_qualifying_game(game):
                continue

            pgn = game.get("pgn", "")
            dt = game_date_from_pgn(pgn)
            if dt is None:
                continue

            if display_name == "Lars":
                if dt < LARS_START:
                    continue
            else:
                if dt.year != GAME_YEAR:
                    continue

            game["_owner_display_name"] = display_name
            game["_owner_username"] = username
            game["_game_date"] = dt
            games.append(game)

    return games


def collect_games():
    all_games = []

    for display_name, username in PLAYERS:
        all_games.extend(
            fetch_player_games(
                display_name,
                username,
            )
        )

    return all_games


def game_owner(game):
    return game["_owner_display_name"]


def opponent_name(game, username):
    white = game.get("white", {})
    black = game.get("black", {})

    owner = game_owner(game)

    if str(white.get("username", "")).casefold() == username.casefold():
        return black.get("username", "Unknown"), black.get("rating")
    return white.get("username", "Unknown"), white.get("rating")


def build_game_board(pgn, move_index, pov_is_white):
    game = chess.pgn.read_game(io.StringIO(pgn))
    if game is None:
        raise RuntimeError("Invalid PGN.")

    board = game.board()
    moves = list(game.mainline_moves())

    move_index = max(0, min(move_index, len(moves)))
    for move in moves[:move_index]:
        board.push(move)

    orientation = chess.WHITE if pov_is_white else chess.BLACK

    svg = chess.svg.board(
        board=board,
        orientation=orientation,
        coordinates=True,
        size=600,
    )

    png = cairosvg.svg2png(
        bytestring=svg.encode("utf-8")
    )

    return discord.File(
        io.BytesIO(png),
        filename="chess_chatter_board.png",
    ), board, len(moves)


class ChessView(discord.ui.View):
    def __init__(self, pgn, pov_is_white, initial_index=0):
        super().__init__(timeout=None)
        self.pgn = pgn
        self.pov_is_white = pov_is_white
        self.index = initial_index
        self.message = None

    async def refresh(self, interaction):
        file, board, total = build_game_board(
            self.pgn,
            self.index,
            self.pov_is_white,
        )

        self.children[0].disabled = self.index <= 0
        self.children[1].disabled = self.index >= total

        embed = self.message.embeds[0].copy()
        embed.description = (
            f"**Move {self.index} / {total}**\n"
            f"POV: **{'White' if self.pov_is_white else 'Black'}**"
        )
        embed.set_image(url="attachment://chess_chatter_board.png")

        await interaction.response.edit_message(
            embed=embed,
            view=self,
            attachments=[file],
        )

    @discord.ui.button(label="◀", style=discord.ButtonStyle.secondary)
    async def back(self, interaction, button):
        if self.index > 0:
            self.index -= 1
        await self.refresh(interaction)

    @discord.ui.button(label="▶", style=discord.ButtonStyle.secondary)
    async def forward(self, interaction, button):
        file, board, total = build_game_board(
            self.pgn,
            self.index,
            self.pov_is_white,
        )

        if self.index < total:
            self.index += 1

        await self.refresh(interaction)


async def post_chess_round(channel):
    games = await asyncio.to_thread(collect_games)

    if not games:
        await channel.send(
            "❌ **Chess Chatter:** could not find a qualifying rated rapid/blitz game."
        )
        return

    game = random.choice(games)
    owner = game_owner(game)
    owner_username = game["_owner_username"]

    white = game.get("white", {})
    black = game.get("black", {})

    owner_is_white = (
        str(white.get("username", "")).casefold()
        == owner_username.casefold()
    )

    opponent_username, opponent_rating = opponent_name(
        game,
        owner_username,
    )

    pgn = game["pgn"]
    date_text = game["_game_date"].strftime("%Y-%m-%d")
    game_type = game.get("time_class", "unknown").title()

    initial_index = 0
    file, board, total = build_game_board(
        pgn,
        initial_index,
        owner_is_white,
    )

    options = PLAYERS[:]
    correct_display = owner

    wrong = [
        name
        for name, _ in options
        if name != correct_display
    ]

    # Make sure poll choices are always five distinct names.
    random_wrong = random.sample(
        wrong,
        min(POLL_OPTIONS - 1, len(wrong)),
    )
    poll_names = random_wrong + [correct_display]
    random.shuffle(poll_names)

    poll = discord.Poll(
        question="Who played this game?",
        duration=__import__("datetime").timedelta(
            minutes=POLL_DURATION_MINUTES
        ),
        multiple=False,
    )

    correct_index = None
    for i, name in enumerate(poll_names):
        poll.add_answer(text=name)
        if name == correct_display:
            correct_index = i

    embed = discord.Embed(
        title="♟️ **Guess the Chess Chatter**",
        description=(
            f"**{owner} game**\n"
            f"Opponent: **{opponent_username}** "
            f"({opponent_rating or 'unknown'} Elo)\n"
            f"Time control: **{game_type}**\n"
            f"Your POV: **{'White' if owner_is_white else 'Black'}**\n"
            f"Move **0 / {total}**"
        ),
        color=0x3498db,
    )
    embed.set_image(url="attachment://chess_chatter_board.png")

    view = ChessView(
        pgn,
        owner_is_white,
        initial_index,
    )

    message = await channel.send(
        embed=embed,
        file=file,
        view=view,
        poll=poll,
    )
    view.message = message

    await asyncio.sleep(
        POLL_DURATION_MINUTES * 60 + 3
    )

    try:
        await message.end_poll()
    except discord.HTTPException:
        pass

    voters = []
    try:
        for answer in poll.answers:
            voters_for_answer = []
            async for voter in answer.voters():
                voters_for_answer.append(voter)
            voters.append(voters_for_answer)
    except Exception as exc:
        print(f"Chess poll voter lookup failed: {exc}", flush=True)

    if voters and correct_index is not None:
        for voter in voters[correct_index]:
            if voter.bot:
                continue

            total_points = add_points(
                voter.id,
                voter.display_name,
                1,
            )

            await channel.send(
                f"✅ **Correct, {voter.display_name}!**\n"
                f"**+1 point** — you now have **{total_points:g} points.**"
            )

            ranking = personal_ranking(voter.id)
            if ranking:
                await channel.send(ranking)

    await channel.send(
        f"🔓 **The answer was:** ||{correct_display}||"
    )


async def scheduler(channel):
    last_key = None

    while True:
        now = discord.utils.utcnow()

        if now.minute in ROUND_MINUTES and now.second < 10:
            key = now.strftime("%Y-%m-%d-%H-%M")

            if key != last_key:
                last_key = key
                asyncio.create_task(
                    post_chess_round(channel)
                )

        await asyncio.sleep(5)


@client.event
async def on_message(message):
    if message.author.bot or message.channel.id != CHANNEL_ID:
        return

    command = message.content.strip().casefold()

    if command in {"!leaderboard", "!lb", "!l"}:
        await message.channel.send(
            full_leaderboard("🏆 **Shared Leaderboard**")
        )
        return

    if command in {"!help", "!info"}:
        await message.channel.send(
            "**Guess the Chess Chatter**\n"
            "`!leaderboard`, `!lb`, `!l` — shared leaderboard\n"
            "`!help`, `!info` — this message\n\n"
            "Chess Chatter runs on **:10, :30 and :50**.\n"
            "Rated **rapid + blitz** games only."
        )


@client.event
async def on_ready():
    print(f"Chess Chatter ready as {client.user}", flush=True)
    channel = await client.fetch_channel(CHANNEL_ID)

    asyncio.create_task(
        scheduler(channel)
    )


client.run(TOKEN)
