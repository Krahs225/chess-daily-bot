import discord
import os
import requests
import chess

TOKEN = os.getenv("DISCORD_TOKEN")
CHANNEL_ID = 1468320170891022417

intents = discord.Intents.default()
client = discord.Client(intents=intents)

@client.event
async def on_ready():
    print("🤖 Bot logged in")

    try:
        channel = await client.fetch_channel(CHANNEL_ID)

        await channel.send("🧪 Fetching Chess.com daily puzzle...")

        headers = {
            "User-Agent": "DailyChessPuzzleBot/1.0"
        }

        r = requests.get(
            "https://api.chess.com/pub/puzzle",
            headers=headers,
            timeout=10
        )

        print("🌐 Status:", r.status_code)
        print("📦 Content-Type:", r.headers.get("Content-Type"))

        if r.status_code != 200:
            await channel.send("❌ Chess.com API returned non-200 status")
            return

        try:
            data = r.json()
        except Exception as e:
            print("❌ JSON decode error:", e)
            print("📦 Raw text:", r.text[:300])
            await channel.send("❌ Chess.com response was not valid JSON")
            return

        fen = data.get("fen")
        title = data.get("title", "Daily Puzzle")

        if not fen:
            await channel.send("❌ No FEN found in Chess.com response")
            return

        board = chess.Board(fen)
        side = "White" if board.turn else "Black"

        embed = discord.Embed(
            title="♟️ Daily Chess Puzzle",
            description=f"**{title}**\n\n**{side} to move. Find the best move!**",
            color=0x3498db
        )

        await channel.send(embed=embed)
        print("✅ Puzzle posted")

    except Exception as e:
        print("❌ Fatal error:", e)

    finally:
        print("🔒 Closing bot")
        await client.close()

client.run(TOKEN)
