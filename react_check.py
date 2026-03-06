import os
import discord
import asyncio
import time

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")

CHANNEL_ID = 1468320170891022417

intents = discord.Intents.default()
intents.message_content = True

client = discord.Client(intents=intents)


async def check_messages(channel):

    messages = [msg async for msg in channel.history(limit=10)]

    for message in messages:

        if message.author.bot:
            continue

        if message.content.strip() == "!react":
            await channel.send("answer")
            return


@client.event
async def on_ready():

    channel = await client.fetch_channel(CHANNEL_ID)

    start_time = time.time()

    # runner blijft 2 minuten actief
    while time.time() - start_time < 120:

        await check_messages(channel)

        await asyncio.sleep(10)

    await client.close()


client.run(DISCORD_TOKEN)
