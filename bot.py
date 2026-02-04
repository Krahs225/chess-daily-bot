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

        # 🔹 1. HARD TEST — dit MOET verschijnen
        await channel.send("🧪 Bot reached Chess.com fetch step")

        r = requests.get(
            "https://api.chess.com/pub/puzzle",
            timeout=10
        )

        print("🌐 Status:", r.status_code)
        print("📦 Raw response:", r.text[:500])

        data = r.json()

        # 🔹 2. Check of we hier komen
        await channel.send("🧪 Chess.com response received")

        fen = data.get("fen")
        title = data.get("title")

        if not fen:
            await channel.send("❌ No FEN in Chess.com response")
            return

        board = chess.Board(fen)
        side = "White" if board.turn else "Black"

        # 🔹 3. EMBED
        embed = discord.Embed(
            title="♟️ Daily Chess Puzzle",
            description=f"{title}\n\n**{side} to move.**",
            color=0x3498db
        )

        await channel.send(embed=embed)
        print("✅ Embed sent")

    except Exception as e:
        print("❌ Exception:", e)

    finally:
        print("🔒 Closing bot")
        await client.close()

client.run(TOKEN)
