import discord
import os
import requests
import chess
import urllib.parse

TOKEN = os.getenv("DISCORD_TOKEN")
CHANNEL_ID = 1468320170891022417

intents = discord.Intents.default()
client = discord.Client(intents=intents)

@client.event
async def on_ready():
    print("🤖 Bot logged in")

    try:
        channel = await client.fetch_channel(CHANNEL_ID)

        headers = {"User-Agent": "DailyChessPuzzleBot/1.0"}
        r = requests.get(
            "https://api.chess.com/pub/puzzle",
            headers=headers,
            timeout=10
        )
        data = r.json()

        fen = data.get("fen")
        title = data.get("title", "Daily Puzzle")

        if not fen:
            await channel.send("❌ Could not load today's puzzle.")
            return

        board = chess.Board(fen)
        side = "White" if board.turn else "Black"

        # 🔹 Encode FEN voor URL
        fen_encoded = urllib.parse.quote(fen)

        board_image_url = (
            f"https://api.chess.com/pub/board/{fen_encoded}.png"
        )

        embed = discord.Embed(
            title="♟️ Daily Chess Puzzle",
            description=f"**{title}**\n\n**{side} to move. Find the best move!**",
            color=0x3498db
        )

        embed.set_image(url=board_image_url)

        await channel.send(embed=embed)
        print("✅ Puzzle with board posted")

    except Exception as e:
        print("❌ Error:", e)

    finally:
        await client.close()

client.run(TOKEN)
