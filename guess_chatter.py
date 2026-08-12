import discord
import os
import random
import re
import asyncio

TOKEN = os.getenv("DISCORD_TOKEN")
CHANNEL_ID = 1536769340970373241
MIN_CHARACTERS = 20
CHAT_FILE = "COMBINED CHATS.txt"
ANSWER_DELAY_SECONDS = 10

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

        match = re.match(
            r"^.*?Twitch Terugblik 2024(?P<username>[A-Za-z0-9_]+):\s*(?P<message>.*)$",
            line
        )

        if not match:
            match = re.match(
                r"^.*?Niveau \d+(?P<username>[A-Za-z0-9_]+):\s*(?P<message>.*)$",
                line
            )

        if not match:
            continue

        username = match.group("username").strip()
        message = match.group("message").strip()

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

        await asyncio.sleep(ANSWER_DELAY_SECONDS)

        await channel.send(
            f"🔓 **The answer was:** ||{username}||"
        )

    finally:
        await client.close()


client.run(TOKEN)
