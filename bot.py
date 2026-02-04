import discord
import os
import requests
import chess
import urllib.parse
from io import BytesIO

TOKEN = os.getenv("DISCORD_TOKEN")
CHANNEL_ID = 1468320170891022417  # #daily-puzzle

intents = discord.Intents.default()
client = discord.Client(intents=intents)

@client.event
async def on_ready():
    try:
        channel = await client.fetch_channel(CHANNEL_ID)

        # ── Chess.com puzzle ophalen ──
        headers = {"User-Agent": "DailyChessPuzzleBot/1.0"}
        r = requests.get("https://api.chess.com/pub/puzzle", headers=headers, timeout=10)
        data = r.json()

        fen = data.get("fen")
        title = data.get("title", "Daily Chess Puzzle")

        if not fen:
            await channel.send("❌ Could not load today's puzzle.")
            return

        board = chess.Board(fen)
        side = "White" if board.turn else "Black"

        # ── Bord ophalen als PNG (Lichess renderer) ──
        fen_encoded = urllib.parse.quote(fen)
        image_url = (
            f"https://lichess.org/api/board/fen/{fen_encoded}.png"
            "?color=white&piece=cburnett&size=512"
        )

        img_response = requests.get(image_url, timeout=10)
        image_bytes = BytesIO(img_response.content)

        file = discord.File(fp=image_bytes, filename="board.png")

        # ── Embed ──
        embed = discord.Embed(
            title="♟️ Daily Chess Puzzle",
            description=f"**{title}**\n\n**{side} to move. Find the best move!**",
            color=0x2ecc71
        )

        embed.set_image(url="attachment://board.png")

        await channel.send(embed=embed, file=file)

    except Exception as e:
        print("❌ Error:", e)

    finally:
        await client.close()

client.run(TOKEN)
