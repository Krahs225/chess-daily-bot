import discord
import os
import requests
import re

TOKEN = os.getenv("DISCORD_TOKEN")
CHANNEL_ID = 1468320170891022417

intents = discord.Intents.default()
client = discord.Client(intents=intents)

@client.event
async def on_ready():
    try:
        channel = await client.fetch_channel(CHANNEL_ID)

        r = requests.get(
            "https://api.chess.com/pub/puzzle",
            headers={"User-Agent": "DailyChessPuzzleBot/1.0"},
            timeout=10
        )

        if r.status_code != 200:
            await channel.send("❌ Kon het antwoord niet laden.")
            return

        data = r.json()
        pgn = data.get("pgn")

        if not pgn:
            await channel.send("❌ Geen oplossing gevonden.")
            return

        # ➜ NEEM ALTIJD DE LAATSTE NIET-LEGE REGEL
        lines = [line.strip() for line in pgn.splitlines() if line.strip()]
        moves_line = lines[-1]

        # Zetnummers en resultaat verwijderen
        moves = re.sub(r"\d+\.", "", moves_line)
        moves = moves.replace("1-0", "").replace("0-1", "").replace("1/2-1/2", "")
        moves = " ".join(moves.split())

        await channel.send(
            f"💡 **The correct answer is:** ||{moves}||"
        )

    except Exception as e:
        print("❌ Error:", e)

    finally:
        await client.close()

client.run(TOKEN)
