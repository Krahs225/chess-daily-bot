import discord
import os
import requests
import chess
import urllib.parse
import time

TOKEN = os.getenv("DISCORD_TOKEN")
CHANNEL_ID = 1468320170891022417  # jouw #daily-puzzle

intents = discord.Intents.default()
client = discord.Client(intents=intents)

@client.event
async def on_ready():
    try:
        channel = await client.fetch_channel(CHANNEL_ID)

        # ── Chess.com daily puzzle ──
        headers = {"User-Agent": "DailyChessPuzzleBot/1.0"}
        r = requests.get("https://api.chess.com/pub/puzzle", headers=headers, timeout=10)
        if r.status_code != 200:
            await channel.send("❌ Kon de dagelijkse puzzel niet laden.")
            return

        data = r.json()
        fen = data.get("fen")
        title = data.get("title", "Daily Chess Puzzle")

        if not fen:
            await channel.send("❌ Kon de dagelijkse puzzel niet laden.")
            return

        board = chess.Board(fen)
        side = "White" if board.turn else "Black"

        # ── Externe FEN → image (betrouwbaar) ──
        fen_encoded = urllib.parse.quote(fen)
        cache_bust = int(time.time())

        # chessboardimage.com rendert FEN → PNG
        board_image_url = (
            f"https://chessboardimage.com/{fen_encoded}.png"
            f"?size=512&coordinates=true&v={cache_bust}"
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
