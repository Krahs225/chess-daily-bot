import discord
import os
import requests
import chess
import time

TOKEN = os.getenv("DISCORD_TOKEN")
CHANNEL_ID = 1468320170891022417

intents = discord.Intents.default()
client = discord.Client(intents=intents)

def fetch_puzzle_with_retry():
    headers = {"User-Agent": "DailyChessPuzzleBot/1.0"}

    for _ in range(3):  # max 3 pogingen
        r = requests.get("https://api.chess.com/pub/puzzle", headers=headers, timeout=10)
        if r.status_code == 200:
            data = r.json()
            if data.get("fen") and data.get("solution"):
                return data
        time.sleep(2)

    return None

@client.event
async def on_ready():
    try:
        channel = await client.fetch_channel(CHANNEL_ID)

        data = fetch_puzzle_with_retry()
        if not data:
            await channel.send("❌ Kon het antwoord niet laden.")
            return

        fen = data["fen"]
        solution = data["solution"]

        board = chess.Board(fen)
        move = chess.Move.from_uci(solution[0])
        san = board.san(move)

        await channel.send(f"💡 **The correct answer is:** ||{san}||")

    except Exception as e:
        print("❌ Error:", e)

    finally:
        await client.close()

client.run(TOKEN)
