import discord
import os
import requests
import chess
import chess.svg
from io import BytesIO

TOKEN = os.getenv("DISCORD_TOKEN")
CHANNEL_ID = 1468320170891022417

intents = discord.Intents.default()
client = discord.Client(intents=intents)

@client.event
async def on_ready():
    try:
        channel = await client.fetch_channel(CHANNEL_ID)

        r = requests.get(
            "https://api.chess.com/pub/puzzle",
            headers={"User-Agent": "DailyChessPuzzleBot/1.0"},
            timeout=10
        )

        if r.status_code != 200:
            await channel.send("❌ Kon de puzzel niet laden.")
            return

        data = r.json()
        fen = data.get("fen")
        title = data.get("title", "Daily Chess Puzzle")

        if not fen:
            await channel.send("❌ Kon de puzzel niet laden.")
            return

        board = chess.Board(fen)
        side = "White" if board.turn else "Black"

        # ✅ ORIENTATIE: zwart onder als zwart aan zet
        orientation = chess.WHITE if board.turn else chess.BLACK

        svg = chess.svg.board(
            board=board,
            orientation=orientation,
            size=500,
            coordinates=True
        )

        image_bytes = BytesIO(svg.encode("utf-8"))
        file = discord.File(fp=image_bytes, filename="puzzle.svg")

        embed = discord.Embed(
            title="♟️ Daily Chess Puzzle",
            description=f"**{title}**\n\n**{side} to move. Find the best move!**",
            color=0x2ecc71
        )

        embed.set_image(url="attachment://puzzle.svg")

        await channel.send(embed=embed, file=file)

    finally:
        await client.close()

client.run(TOKEN)
