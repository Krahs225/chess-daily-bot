import discord
import os

TOKEN = os.getenv("DISCORD_TOKEN")
CHANNEL_ID = 1536769340970373241

intents = discord.Intents.default()
client = discord.Client(intents=intents)


@client.event
async def on_ready():
    try:
        channel = await client.fetch_channel(CHANNEL_ID)

        await channel.send("✅ Guess the Chatter test successful!")

    finally:
        await client.close()


client.run(TOKEN)
