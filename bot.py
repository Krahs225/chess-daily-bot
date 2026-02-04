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

        r = requests.get("https://lichess.org/api/puzzle/daily", timeout=10)
        data = r.json()
        puzzle = data.get("puzzle")

        if not puzzle or "fen" not in puzzle:
            await channel.send("❌ Could not load today's puzzle.")
            return

        board = chess.Board(puzzle["fen"])
        side = "White" if board.turn else "Black"

        embed = discord.Embed(
            title="♟️ Daily Chess Puzzle",
            description=f"**{side} to move. Find the best move!**",
            color=0x2ecc71
        )

        await channel.send(embed=embed)
        print("✅ Embed posted")

    except Exception as e:
        print("❌ Error:", e)

    finally:
        await client.close()

client.run(TOKEN)
