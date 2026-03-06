import os
import discord
import asyncio

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")

CHANNEL_ID = 1468320170891022417

intents = discord.Intents.default()
intents.message_content = True

client = discord.Client(intents=intents)


@client.event
async def on_ready():

    print("Bot connected")

    await asyncio.sleep(3)

    channel = await client.fetch_channel(CHANNEL_ID)

    print("Channel found, reading messages...")

    messages = [msg async for msg in channel.history(limit=5)]

    messages.reverse()

    for message in messages:

        if message.author.bot:
            continue

        print("Repeating:", message.content)

        await channel.send(message.content)

    print("Finished test")

    await client.close()


client.run(DISCORD_TOKEN)
