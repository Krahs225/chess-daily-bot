import os
import discord

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")

CHANNEL_ID = 1468320170891022417

intents = discord.Intents.default()
client = discord.Client(intents=intents)


@client.event
async def on_ready():
    channel = client.get_channel(CHANNEL_ID)

    if channel is None:
        channel = await client.fetch_channel(CHANNEL_ID)

    await channel.send("bot reached channel")

    await client.close()


client.run(DISCORD_TOKEN)
