import discord
import os
import json
import asyncio

TOKEN = os.getenv("DISCORD_TOKEN")
CHANNEL_ID = 1468320170891022417

intents = discord.Intents.default()
client = discord.Client(intents=intents)

@client.event
async def on_ready():
    try:
        channel = await client.fetch_channel(CHANNEL_ID)

        await channel.send("⏱️ Antwoord wordt berekend…")

        await asyncio.sleep(2)

        if not os.path.exists("puzzle.json"):
            await channel.send("❌ Geen puzzeldata gevonden.")
            return

        with open("puzzle.json", "r") as f:
            data = json.load(f)

        # Gebruik UCI direct (NOOIT vastlopers)
        uci_moves = data["solution"]

        # Alleen wit-zetten tonen (0,2,4,…)
        white_moves = [uci_moves[i] for i in range(0, len(uci_moves), 2)]

        answer = " ".join(white_moves)

        await channel.send(
            f"💡 **The correct answer is:** ||{answer}||"
        )

    finally:
        await client.close()

client.run(TOKEN)
