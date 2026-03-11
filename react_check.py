import os
import discord
import asyncio
import time
import json

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")

CHANNEL_ID = 1468320170891022417
STATE_FILE = "react_state.json"

intents = discord.Intents.default()
intents.message_content = True

client = discord.Client(intents=intents)


def load_last_id():
    if not os.path.exists(STATE_FILE):
        return 0
    with open(STATE_FILE, "r") as f:
        data = json.load(f)
        return data.get("last_id", 0)


def save_last_id(message_id):
    with open(STATE_FILE, "w") as f:
        json.dump({"last_id": message_id}, f)


async def check_messages(channel):

    messages = [msg async for msg in channel.history(limit=15)]
    messages.reverse()  # oud → nieuw

    for message in messages:

        if message.author.bot:
            continue

        if message.content.strip().lower() != "!react":
            continue

        last_id = load_last_id()

        if message.id <= last_id:
            return

        await channel.send("answer")

        save_last_id(message.id)

        return


@client.event
async def on_ready():

    channel = await client.fetch_channel(CHANNEL_ID)

    # Bij start: laatste bericht opslaan zodat oude !react niet triggeren
    last_id = load_last_id()

    if last_id == 0:
        messages = [msg async for msg in channel.history(limit=1)]
        if messages:
            save_last_id(messages[0].id)

    start_time = time.time()

    # runner 45 minuten actief
    while time.time() - start_time < 2700:

        await check_messages(channel)

        await asyncio.sleep(5)

    await client.close()


client.run(DISCORD_TOKEN)
