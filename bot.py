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

        headers = {
            "User-Agent": "DailyChessPuzzleBot/1.0 (GitHub Actions)"
        }

        r = requests.get(
            "https://lichess.org/api/puzzle/daily",
            headers=headers,
            timeout=10
        )

        print("🔍 Lichess status:", r.status_code)
        data = r.json()
        print("🔍 Lichess response keys:", data.keys())

        puzzle = data.get("puzzle")

        if not puzzle or "fen" not in puzzle:
            await channel.send("❌ Could not load today's puzzle (Lichess API issue).")
            return

        board = chess.Board(puzzle["fen"])
        side = "White" if board.turn else "Black"

        embed = discord.Embed(
            title="♟️ Daily Chess Puzzle",
            description=f"**{side} to move. Find the best move!**",
            color=0x2ecc71
        )

        await channel.send(embed=embed)
        print("✅ Puzzle embed posted")

    except Exception as e:
        print("❌ Error:", e)

    finally:
        print("🔒 Closing bot")
        await client.close()

client.run(TOKEN)
