import discord
import os
import random
import re
import asyncio
from pathlib import Path

TOKEN = os.getenv("DISCORD_TOKEN")
CHANNEL_ID = 1536769340970373241

MIN_CHARACTERS = 20
CHAT_DIR = "SOLO chats"

QUOTE_INTERVAL_SECONDS = 5 * 60
ANSWER_DELAY_SECONDS = 60

intents = discord.Intents.default()
client = discord.Client(intents=intents)


def load_quotes():
    chatters = {}

    chat_files = Path(CHAT_DIR).glob("*.txt")

    for chat_file in chat_files:
        try:
            with open(chat_file, "r", encoding="utf-8") as file:
                lines = file.readlines()
        except Exception:
            continue

        current_date = None

        for line in lines:
            line = line.strip()

            if not line:
                continue

            date_match = re.match(r"^(\d{1,2})-(\d{1,2})-(\d{4})$", line)

            if date_match:
                day, month, year = date_match.groups()
                current_date = f"{day.zfill(2)}-{month.zfill(2)}-{year}"
                continue

            match = re.match(
                r"^\d{1,2}:\d{2}.*?([A-Za-z0-9_]+):\s*(.*)$",
                line
            )

            if not match or not current_date:
                continue

            username = match.group(1).strip()
            message = match.group(2).strip()

            if len(message) < MIN_CHARACTERS:
                continue

            if username not in chatters:
                chatters[username] = []

            chatters[username].append((message, current_date))

    return chatters


async def post_quote(channel, chatters):
    username = random.choice(list(chatters.keys()))
    message, date = random.choice(chatters[username])

    await channel.send(
        f"💬 **Guess the Chatter**\n\n"
        f"> {message}\n\n"
        f"📅 **Date:** {date}"
    )

    await asyncio.sleep(ANSWER_DELAY_SECONDS)

    await channel.send(
        f"🔓 **The answer was:** ||{username}||"
    )


@client.event
async def on_ready():
    channel = await client.fetch_channel(CHANNEL_ID)

    chatters = load_quotes()

    if not chatters:
        await channel.send("No valid chatters found.")
        await client.close()
        return

    while True:
        await post_quote(channel, chatters)

        await asyncio.sleep(
            QUOTE_INTERVAL_SECONDS - ANSWER_DELAY_SECONDS
        )


client.run(TOKEN)
