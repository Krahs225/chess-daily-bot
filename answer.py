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
    try:
        channel = await client.fetch_channel(CHANNEL_ID)

        headers = {"User-Agent": "DailyChessPuzzleBot/1.0"}
        r = requests.get("https://api.chess.com/pub/puzzle", headers=headers, timeout=10)

        if r.status_code != 200:
            await channel.send("❌ Kon het antwoord niet laden.")
            return

        data = r.json()
        fen = data.get("fen")
        solution = data.get("solution")

        if not fen or not solution:
            await channel.send("❌ Kon het antwoord niet laden.")
            return

        board = chess.Board(fen)
        move = chess.Move.from_uci(solution[0])
        san = board.san(move)

        await channel.send(f"💡 **The correct answer is:** ||{san}||")

    except Exception as e:
        print("❌ Error:", e)
    finally:
        await client.close()

client.run(TOKEN)
