import discord
import os
import chess
import json

TOKEN = os.getenv("DISCORD_TOKEN")
CHANNEL_ID = 1468320170891022417

intents = discord.Intents.default()
client = discord.Client(intents=intents)

@client.event
async def on_ready():
    try:
        channel = await client.fetch_channel(CHANNEL_ID)

        if not os.path.exists("puzzle.json"):
            await channel.send("❌ Geen puzzeldata gevonden.")
            return

        with open("puzzle.json", "r") as f:
            data = json.load(f)

        board = chess.Board(data["fen"])
        san_moves = []

        for i, uci in enumerate(data["solution"]):
            move = chess.Move.from_uci(uci)
            san = board.san(move)
            board.push(move)

            if i % 2 == 0:  # alleen wit
                san_moves.append(san)

        answer = " ".join(san_moves)

        await channel.send(
            f"💡 **The correct answer is:** ||{answer}||"
        )

    finally:
        await client.close()

client.run(TOKEN)
