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
    print("🤖 Bot logged in")

    try:
        channel = await client.fetch_channel(CHANNEL_ID)

        # ── Chess.com puzzle ophalen ──
        headers = {"User-Agent": "DailyChessPuzzleBot/1.0"}
        r = requests.get(
            "https://api.chess.com/pub/puzzle",
            headers=headers,
            timeout=10
        )

        if r.status_code != 200:
            await channel.send("❌ Could not load today's puzzle.")
            return

        data = r.json()

        fen = data.get("fen")
        title = data.get("title", "Daily Chess Puzzle")

        if not fen:
            await channel.send("❌ Could not load today's puzzle.")
            return

        board = chess.Board(fen)
        side = "White" if board.turn else "Black"

        # ── Lichess board image (PNG) ──
        fen_encoded = urllib.parse.quote(fen)
        timestamp = int(time.time())  # cache-busting

        board_image_url = (
            f"https://lichess.org/api/board/fen/{fen_encoded}.png"
            "?color=white&piece=cburnett&size=512"
            f"&v={timestamp}"
        )

        # ── Discord embed ──
        embed = discord.Embed(
            title="♟️ Daily Chess Puzzle",
            description=f"**{title}**\n\n**{side} to move. Find the best move!**",
            color=0x2ecc71
        )

        embed.set_image(url=board_image_url)

        await channel.send(embed=embed)
        print("✅ Puzzle with board posted")

    except Exception as e:
        print("❌ Error:", e)

    finally:
        print("🔒 Closing bot")
        await client.close()

client.run(TOKEN)
