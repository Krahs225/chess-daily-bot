import os
import discord
import asyncio
import json
import requests
import chess
import chess.svg
from io import BytesIO
import cairosvg
import time

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")

CHANNEL_ID = 1468320170891022417
STATE_FILE = "random_state.json"

intents = discord.Intents.default()
intents.message_content = True

client = discord.Client(intents=intents)


def load_last_command():
    if not os.path.exists(STATE_FILE):
        return 0
    with open(STATE_FILE, "r") as f:
        data = json.load(f)
        return data.get("last_command_id", 0)


def save_last_command(message_id):
    with open(STATE_FILE, "w") as f:
        json.dump({"last_command_id": message_id}, f)


async def post_random_puzzle(channel):

    r = requests.get(
        "https://lichess.org/api/puzzle/next",
        headers={"Accept": "application/json"},
        timeout=10
    )

    if r.status_code != 200:
        await channel.send("❌ Could not load random puzzle.")
        return

    data = r.json()

    fen = data["game"]["fen"]
    rating = data["puzzle"]["rating"]

    board = chess.Board(fen)

    side = "White" if board.turn else "Black"
    orientation = chess.WHITE if board.turn else chess.BLACK

    svg_board = chess.svg.board(
        board=board,
        orientation=orientation,
        size=500,
        coordinates=True
    )

    png_bytes = cairosvg.svg2png(bytestring=svg_board.encode("utf-8"))
    image = BytesIO(png_bytes)

    file = discord.File(fp=image, filename="puzzle.png")

    embed = discord.Embed(
        title="🎲 Random Chess Puzzle",
        description=f"**Rating: {rating}**\n\n**{side} to move. Find the best move!**",
        color=0x2ecc71
    )

    embed.set_image(url="attachment://puzzle.png")

    await channel.send(embed=embed, file=file)


async def check_commands(channel):

    last_command_id = load_last_command()

    messages = [msg async for msg in channel.history(limit=20)]

    for message in messages:

        if message.author.bot:
            continue

        if message.id <= last_command_id:
            continue

        if message.content.strip() == "!randompuzzle":

            await post_random_puzzle(channel)

            save_last_command(message.id)

            return


@client.event
async def on_ready():

    channel = await client.fetch_channel(CHANNEL_ID)

    start_time = time.time()

    # run ongeveer 6 minuten
    while time.time() - start_time < 360:

        await check_commands(channel)

        await asyncio.sleep(30)

    await client.close()


client.run(DISCORD_TOKEN)
