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


def load_last_message():
    if not os.path.exists(STATE_FILE):
        return None
    with open(STATE_FILE, "r") as f:
        data = json.load(f)
        return data.get("last_message_id")


def save_last_message(message_id):
    with open(STATE_FILE, "w") as f:
        json.dump({"last_message_id": message_id}, f)


@client.event
async def on_ready():
    await asyncio.sleep(2)

    channel = await client.fetch_channel(CHANNEL_ID)
    last_message_id = load_last_message()

    async for message in channel.history(limit=50):

        if "!randompuzzle" in message.content:

            if message.id == last_message_id:
                break

            await channel.send("checked")

            save_last_message(message.id)
            break

    await client.close()


client.run(DISCORD_TOKEN)
