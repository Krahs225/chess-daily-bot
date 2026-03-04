import os
import discord

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")

DISCORD_CHANNEL_ID = 1468320170891022417

intents = discord.Intents.default()
intents.message_content = True
client = discord.Client(intents=intents)


@client.event
async def on_ready():
    channel = await client.fetch_channel(DISCORD_CHANNEL_ID)

    async for message in channel.history(limit=50):
        if "!randompuzzle" in message.content:
            await channel.send("checked")
            break

    await client.close()


client.run(DISCORD_TOKEN)
