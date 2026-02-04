import discord
import os
import requests
import chess
import time

TOKEN = os.getenv("DISCORD_TOKEN")
CHANNEL_ID = 1468320170891022417

intents = discord.Intents.default()
client = discord.Client(intents=intents)

def get_daily_puzzle():
    headers = {
        "User-Agent": "DailyChessPuzzleBot/1.0 (GitHub Actions)"
    }

    for attempt in range(1, 6):
        try:
            print(f"🔁 Fetch attempt {attempt}")
            r = requests.get(
                "https://lichess.org/api/puzzle/daily",
                headers=headers,
                timeout=10
            )
            print("🌐 Status:", r.status_code)

            data = r.json()
            puzzle = data.get("puzzle")

            if puzzle and "fen" in puzzle and "solution" in puzzle:
                print("✅ Puzzle loaded")
                return puzzle

            print("⚠️ Puzzle data incomplete")

        except Exception as e:
            print("❌ Request error:", e)

        time.sleep(2)

    return None


@client.event
async def on_ready():
    print("🤖 Bot logged in")

    try:
        channel = await client.fetch_channel(CHANNEL_ID)

        puzzle = get_daily_puzzle()

        if not puzzle:
            await channel.send("❌ Could not load today's puzzle after multiple tries.")
            return

        board = chess.Board(puzzle["fen"])
        side = "White" if board.turn else "Black"

        embed = discord.Embed(
            title="♟️ Daily Chess Puzzle",
            description=f"**{side} to move. Find the best move!**",
            color=0x2ecc71
        )

        await channel.send(embed=embed)
        print("📨 Puzzle posted")

    except Exception as e:
        print("❌ Fatal error:", e)

    finally:
        print("🔒 Closing bot")
        await client.close()

client.run(TOKEN)
