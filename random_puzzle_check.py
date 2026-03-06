import os
import discord
import asyncio
import json

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")

CHANNEL_ID = 1468320170891022417
STATE_FILE = "random_state.json"

intents = discord.Intents.default()
intents.message_content = True

client = discord.Client(intents=intents)


def load_last_command():
    if not os.path.exists(STATE_FILE):
        return 0
    with open(STATE_FILE, "r") as f:
        data = json.load(f)
        return data.get("last_command_id", 0)


def save_last_command(message_id):
    with open(STATE_FILE, "w") as f:
        json.dump({"last_command_id": message_id}, f)


@client.event
async def on_ready():

    await asyncio.sleep(3)

    channel = await client.fetch_channel(CHANNEL_ID)

    last_command_id = load_last_command()

    messages = [msg async for msg in channel.history(limit=100)]

    # sort oldest → newest
    messages.reverse()

    for message in messages:

        if message.id <= last_command_id:
            continue

        if "!randompuzzle" in message.content:

            await channel.send("checked")

            save_last_command(message.id)
            break

    await client.close()


client.run(DISCORD_TOKEN)
