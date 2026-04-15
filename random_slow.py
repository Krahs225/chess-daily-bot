import os
import discord
import asyncio
import json
import requests
import chess
import chess.svg
import chess.pgn
from io import BytesIO, StringIO
import cairosvg

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")

CHANNEL_ID = 1468320170891022417
STATE_FILE = "random_state.json"

intents = discord.Intents.default()
intents.message_content = True

client = discord.Client(intents=intents)


def load_last_id():
    if not os.path.exists(STATE_FILE):
        return 0
    with open(STATE_FILE, "r") as f:
        return json.load(f).get("last_id", 0)


def save_last_id(message_id):
    with open(STATE_FILE, "w") as f:
        json.dump({"last_id": message_id}, f)


def uci_to_san_sequence(board, moves_uci):
    temp = board.copy()
    san = []
    for m in moves_uci:
        move = chess.Move.from_uci(m)
        san.append(temp.san(move))
        temp.push(move)
    return " ".join(san)


def fetch_puzzle():
    while True:
        try:
            r = requests.get(
                "https://lichess.org/api/puzzle/next",
                headers={"Accept": "application/json"},
                timeout=5
            )
            data = r.json()

            rating = data["puzzle"]["rating"]

            if 1000 <= rating <= 3000:
                return data
        except:
            continue


async def post_puzzle(channel):
    data = await asyncio.to_thread(fetch_puzzle)

    rating = data["puzzle"]["rating"]
    initial_ply = data["puzzle"]["initialPly"]
    pgn = data["game"]["pgn"]
    solution_moves = data["puzzle"]["solution"]
    puzzle_id = data["puzzle"]["id"]

    game = chess.pgn.read_game(StringIO(pgn))
    board = game.board()
    node = game

    for _ in range(initial_ply):
        if node.variations:
            node = node.variations[0]
            board.push(node.move)

    if node.variations:
        node = node.variations[0]
        board.push(node.move)

    solution = uci_to_san_sequence(board, solution_moves)

    side = "White" if board.turn else "Black"
    orientation = chess.WHITE if board.turn else chess.BLACK

    svg = chess.svg.board(board=board, orientation=orientation, size=500)
    png = cairosvg.svg2png(bytestring=svg.encode())

    file = discord.File(BytesIO(png), filename="puzzle.png")

    embed = discord.Embed(
        title="🎲 Random Chess Puzzle (backup)",
        description=(
            f"Rating: {rating}\n\n"
            f"{side} to move\n\n"
            f"Solution: ||{solution}||\n\n"
            f"https://lichess.org/training/{puzzle_id}"
        ),
        color=0xe67e22
    )

    embed.set_image(url="attachment://puzzle.png")

    await channel.send(embed=embed, file=file)


@client.event
async def on_ready():
    channel = client.get_channel(CHANNEL_ID)

    messages = [msg async for msg in channel.history(limit=1)]
    last_id = messages[0].id if messages else 0
    save_last_id(last_id)

    while True:
        messages = [msg async for msg in channel.history(limit=25)]

        for message in messages:
            if message.id <= last_id:
                continue

            if message.author.bot:
                continue

            if message.content.strip() == "!randompuzzle":

                # ⏳ WACHT zodat fast bot eerst kan reageren
                await asyncio.sleep(15)

                # 👀 check of er al een bot heeft gereageerd
                recent = [msg async for msg in channel.history(limit=5)]

                for msg in recent:
                    if msg.author.bot:
                        last_id = message.id
                        save_last_id(last_id)
                        break
                else:
                    await post_puzzle(channel)
                    last_id = message.id
                    save_last_id(last_id)

        await asyncio.sleep(5)


client.run(DISCORD_TOKEN)
