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
        data = json.load(f)
        return data.get("last_id", 0)


def save_last_id(message_id):
    with open(STATE_FILE, "w") as f:
        json.dump({"last_id": message_id}, f)


async def post_puzzle(channel):

    r = requests.get(
        "https://lichess.org/api/puzzle/next",
        headers={"Accept": "application/json"},
        timeout=10
    )

    data = r.json()

    rating = data["puzzle"]["rating"]
    initial_ply = data["puzzle"]["initialPly"]
    pgn = data["game"]["pgn"]

    solution_moves = data["puzzle"]["solution"]

    # 🧠 Bouw correcte board state
    game = chess.pgn.read_game(StringIO(pgn))
    board = game.board()
    moves = list(game.mainline_moves())

    # speel tot puzzle start
    for move in moves[:initial_ply]:
        board.push(move)

    # engine zet uitvoeren (BELANGRIJK)
    engine_move = chess.Move.from_uci(solution_moves[0])
    board.push(engine_move)

    # juiste oplossing (speler zet)
    solution = solution_moves[1]

    side = "White" if board.turn else "Black"
    orientation = chess.WHITE if board.turn else chess.BLACK

    svg_board = chess.svg.board(
        board=board,
        orientation=orientation,
        size=500,
        coordinates=True
    )

    png_bytes = cairosvg.svg2png(bytestring=svg_board.encode())
    image = BytesIO(png_bytes)

    file = discord.File(fp=image, filename="puzzle.png")

    embed = discord.Embed(
        title="🎲 Random Chess Puzzle",
        description=f"Rating: {rating}\n\n{side} to move\n\nSolution: ||{solution}||",
        color=0x2ecc71
    )

    embed.set_image(url="attachment://puzzle.png")

    await channel.send(embed=embed, file=file)


@client.event
async def on_ready():
    channel = client.get_channel(CHANNEL_ID)

    last_id = load_last_id()

    while True:
        messages = [msg async for msg in channel.history(limit=25)]

        for message in messages:
            if message.id <= last_id:
                continue

            if message.content.strip() == "!randompuzzle":
                await post_puzzle(channel)
                save_last_id(message.id)
                return  # runner stoppen na 1 reactie

        await asyncio.sleep(5)


client.run(DISCORD_TOKEN)
