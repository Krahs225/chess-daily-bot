import discord
import os
import requests
import chess
import urllib.parse
import time

TOKEN = os.getenv("DISCORD_TOKEN")
CHANNEL_ID = 1468320170891022417  # #daily-puzzle

intents = discord.Intents.default()
client = discord.Client(intents=intents)

@client.event
async def on_ready():
    try:
        channel = await client.fetch_channel(CHANNEL_ID)

        # 🔴 FORCE Discord to treat this as new content
        await channel.send("♟️ New daily chess puzzle incoming…")

        # ── Fetch Chess.com puzzle ──
        headers = {"User-Agent": "DailyChessPuzzleBot/1.0"}
        r = requests.get("https://api.chess.com/pub/puzzle", headers=headers, timeout=10)
        data = r.json()

        fen = data.get("fen")
        title = data.get("title", "Daily Chess Puzzle")

        if not fen:
            await channel.send("❌ Failed to load puzzle.")
            return

        board = chess.Board(fen)
        side = "White" if board.turn else "Black"

        # ── Lichess board image (always works) ──
        fen_encoded = urllib.parse.quote(fen)
        unique = int(time.time())

        board_image_url = (
            f"https://lichess.org/api/board/fen/{fen_encoded}.png"
            "?color=white&piece=cburnett&size=512"
            f"&v={unique}"
        )

        embed = discord.Embed(
            title="♟️ Daily Chess Puzzle",
            description=f"**{title}**\n\n**{side} to move. Find the best move!**",
            color=0x2ecc71
        )

        embed.set_image(url=board_image_url)

        await channel.send(embed=embed)

    except Exception as e:
        print("❌ Error:", e)

    finally:
        await client.close()

client.run(TOKEN)
