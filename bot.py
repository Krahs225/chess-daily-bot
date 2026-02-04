import discord
import os
import requests
import chess

TOKEN = os.getenv("DISCORD_TOKEN")
CHANNEL_ID = 1468320170891022417  # #daily-puzzle

intents = discord.Intents.default()
client = discord.Client(intents=intents)

@client.event
async def on_ready():
    print("🤖 Bot logged in")

    try:
        channel = await client.fetch_channel(CHANNEL_ID)

        r = requests.get(
            "https://api.chess.com/pub/puzzle",
            timeout=10
        )
        data = r.json()

        fen = data.get("fen")
        title = data.get("title")

        if not fen:
            await channel.send("❌ Could not load today's Chess.com puzzle.")
            return

        board = chess.Board(fen)
        side = "White" if board.turn else "Black"

        embed = discord.Embed(
            title="♟️ Daily Chess Puzzle",
            description=(
                f"**{title}**\n\n"
                f"**{side} to move. Find the best move!**"
            ),
            color=0x3498db
        )

        await channel.send(embed=embed)
        print("✅ Puzzle posted")

    except Exception as e:
        print("❌ Error:", e)

    finally:
        await client.close()

client.run(TOKEN)
