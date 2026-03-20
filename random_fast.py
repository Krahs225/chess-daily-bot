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
import random

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


def fetch_puzzle_blocking(target):
    for _ in range(20):
        try:
            r = requests.get(
                "https://lichess.org/api/puzzle/next",
                headers={"Accept": "application/json"},
                timeout=10
            )
            data = r.json()

            if "puzzle" not in data:
                continue

            rating = data["puzzle"]["rating"]

            if abs(rating - target) <= 150:
                return data

        except:
            continue

    try:
        r = requests.get(
            "https://lichess.org/api/puzzle/next",
            headers={"Accept": "application/json"},
            timeout=10
        )
        return r.json()
    except:
        return None


async def get_random_puzzle():
    target = random.randint(500, 3000)
    return await asyncio.to_thread(fetch_puzzle_blocking, target)


async def post_puzzle(channel):
    data = await get_random_puzzle()

    if not data or "puzzle" not in data:
        await channel.send("Error fetching puzzle, try again.")
        return

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
        title="🎲 Random Chess Puzzle",
        description=(
            f"Rating: {rating}\n\n"
            f"{side} to move\n\n"
            f"Solution: ||{solution}||\n\n"
            f"https://lichess.org/training/{puzzle_id}"
        ),
        color=0x2ecc71
    )

    embed.set_image(url="attachment://puzzle.png")

    await channel.send(embed=embed, file=file)


@client.event
async def on_ready():
    channel = client.get_channel(CHANNEL_ID)
    last_id = load_last_id()

    if last_id == 0:
        messages = [msg async for msg in channel.history(limit=1)]
        if messages:
            last_id = messages[0].id
            save_last_id(last_id)

    while True:
        messages = [msg async for msg in channel.history(limit=25)]

        for message in messages:
            if message.id <= last_id:
                continue
            if message.author.bot:
                continue
            if message.content.strip() == "!randompuzzle":
                await post_puzzle(channel)
                last_id = message.id
                save_last_id(last_id)

        await asyncio.sleep(5)


client.run(DISCORD_TOKEN)
