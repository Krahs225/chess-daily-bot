import discord
import os
import requests
import json

TOKEN = os.getenv("DISCORD_TOKEN")
CHANNEL_ID = 1468320170891022417

intents = discord.Intents.default()
client = discord.Client(intents=intents)

@client.event
async def on_ready():
    try:
        channel = await client.fetch_channel(CHANNEL_ID)

        # Haal puzzle DIRECT van Chess.com
        r = requests.get(
            "https://api.chess.com/pub/puzzle",
            headers={"User-Agent": "DailyChessPuzzleBot/1.0"},
            timeout=10
        )

        if r.status_code != 200:
            await channel.send("❌ Chess.com gaf geen 200 status")
            return

        data = r.json()

        # Post rauwe data (ingekort)
        await channel.send(
            "🧪 **DEBUG – ruwe puzzle data:**\n"
            f"```json\n{json.dumps(data, indent=2)[:1800]}\n```"
        )

    except Exception as e:
        await channel.send(f"❌ Exception: {e}")

    finally:
        await client.close()

client.run(TOKEN)
