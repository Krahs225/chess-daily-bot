import discord
import os
import requests
import chess
import chess.svg
from io import BytesIO

TOKEN = os.getenv("DISCORD_TOKEN")
CHANNEL_ID = 1468320170891022417  # #daily-puzzle

intents = discord.Intents.default()

client = discord.Client(intents=intents)

@client.event
async def on_ready():
    channel = await client.fetch_channel(CHANNEL_ID)

    data = requests.get("https://lichess.org/api/puzzle/daily").json()
    fen = data["puzzle"]["fen"]

    board = chess.Board(fen)
    side = "White" if board.turn else "Black"

    svg = chess.svg.board(board=board, size=500)
    image = BytesIO(svg.encode("utf-8"))
    file = discord.File(fp=image, filename="puzzle.svg")

    embed = discord.Embed(
        title="♟️ Daily Chess Puzzle",
        description=f"**{side} to move. Find the best move!**",
        color=0x2ecc71
    )
    embed.set_image(url="attachment://puzzle.svg")

    await channel.send(embed=embed, file=file)
    await client.close()

client.run(TOKEN)
