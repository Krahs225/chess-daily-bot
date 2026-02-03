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
    channel = await client.fetch_channel(CHANNEL_ID)

    data = requests.get("https://lichess.org/api/puzzle/daily").json()
    fen = data["puzzle"]["fen"]
    solution = data["puzzle"]["solution"][0]

    board = chess.Board(fen)
    move = chess.Move.from_uci(solution)
    san = board.san(move)

    await channel.send(f"💡 **Answer:** ||{san}||")
    await client.close()

client.run(TOKEN)
