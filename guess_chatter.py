import discord
import os
import random
import asyncio

TOKEN = os.getenv("DISCORD_TOKEN")
CHANNEL_ID = 1536769340970373241

QUOTE_FILE = "quotes.txt"
MIN_CHARACTERS = 10
ANSWER_DELAY_SECONDS = 3 * 60 * 60  # 3 uur


intents = discord.Intents.default()
client = discord.Client(intents=intents)


def load_quotes():
    quotes = []

    with open(QUOTE_FILE, "r", encoding="utf-8") as file:
        for line in file:
            line = line.strip()

            if not line:
                continue

            # Formaat:
            # username|bericht
            if "|" not in line:
                continue

            username, message = line.split("|", 1)

            username = username.strip()
            message = message.strip()

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
            await channel.send("❌ Geen geschikte quotes gevonden.")
            return

        username, message = random.choice(quotes)

        await channel.send(
            f"💬 **Guess the Chatter**\n\n"
            f"> {message}"
        )

        # 3 uur wachten
        await asyncio.sleep(ANSWER_DELAY_SECONDS)

        await channel.send(
            f"🔓 **Het antwoord was:** ||{username}||"
        )

    finally:
        await client.close()


client.run(TOKEN)
