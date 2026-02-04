import discord
import os

TOKEN = os.getenv("DISCORD_TOKEN")
CHANNEL_ID = 1468320170891022417  # jouw kanaal

intents = discord.Intents.default()
client = discord.Client(intents=intents)

@client.event
async def on_ready():
    try:
        channel = await client.fetch_channel(CHANNEL_ID)

        await channel.send(
            "👋 **Testbericht**\n"
            "Dit is een automatische mededeling van de bot om te testen "
            "of geplande berichten werken.\n\n"
            "_(Dit is geen puzzle of answer)_"
        )

    finally:
        await client.close()

client.run(TOKEN)
