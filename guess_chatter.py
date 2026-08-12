import discord
import os
import random
import re

TOKEN = os.getenv("DISCORD_TOKEN")
CHANNEL_ID = 1536769340970373241
MIN_CHARACTERS = 20
CHAT_FILE = "combined_chats.txt"

intents = discord.Intents.default()
client = discord.Client(intents=intents)


def load_quotes():
    quotes = []

    with open(CHAT_FILE, "r", encoding="utf-8") as file:
        lines = file.readlines()

    for line in lines:
        line = line.strip()

        if not line:
            continue

        if ":" not in line:
            continue

        match = re.match(r"^.*?Twitch Terugblik 2024(.+?):\s*(.*)$", line)

        if not match:
            match = re.match(r"^.*?Niveau \d+(.+?):\s*(.*)$", line)

        if not match:
            continue

        username = match.group(1).strip()
        message = match.group(2).strip()

        if len(message) < MIN_CHARACTERS:
            continue

        quotes.append((username, message))

    return quotes


@client.event
async def on_ready():
    try:
        channel = await client.fetch_channel(CHANNEL_ID)

        quotes = load_quotes()

        if not quotes:
            await channel.send("No valid quotes found.")
            return

        username, message = random.choice(quotes)

        await channel.send(
            f"💬 **Guess the Chatter**\n\n"
            f"> {message}"
        )

    finally:
        await client.close()


client.run(TOKEN)
