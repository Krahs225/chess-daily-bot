import discord
import os
import random
import re
import asyncio
from datetime import timedelta
from pathlib import Path

TOKEN = os.getenv("DISCORD_TOKEN")
CHANNEL_ID = 1536769340970373241

MIN_CHARACTERS = 20
CHAT_DIR = "SOLO chats"
POLL_OPTIONS = 5
ANSWER_DELAY_SECONDS = 60

CHATTERS = {
    "AZ": "az3d__",
    "Ben": "benniru",
    "Cash": "cashojf3r",
    "Cien": "cien_223",
    "Geeflux": "geeflux",
    "George": "georgeonz0la",
    "Grumpymonk": "grumpymonk147",
    "Jessebrawlstars": "jessebrawlstars",
    "Kurupt": "kurupttv",
    "Martin": "martin_xploz",
    "Melvin": "mevlin13",
    "MH": "mh050131",
    "Mohammad": "mohammad_768",
    "Mr_thice": "mr_thice",
    "Nairyaaa": "nairyaaa",
    "Pabu": "notpabu",
    "Pandarou": "pandarou",
    "Pindametdemensie": "pindametdemensie",
    "Pospos": "pospos12",
    "Rubriek": "rubriek",
    "Sativahibread": "sativahibread",
    "Screamingcat": "screamingcat_02n7",
    "Sh4rkmate is the best": "sh4rkmate_is_the_best",
    "Soyadelson": "soyadelson7",
    "Stefan": "stefan_chesslol",
    "Stepu": "stepu6568",
    "Sushi": "isolatedsushi11",
    "Thejazzdude": "thejazzdude_",
    "Kohl": "kohlkrow",
    "Kingdev": "king_keegdev",
}

intents = discord.Intents.default()
client = discord.Client(intents=intents)


def find_chatter(prefix):
    prefix = prefix.strip().lower()

    matches = []

    for display_name, username in CHATTERS.items():
        username_lower = username.lower()

        if prefix.endswith(username_lower):
            matches.append(
                (len(username_lower), display_name, username)
            )

    if not matches:
        return None

    matches.sort(reverse=True)
    return matches[0][1], matches[0][2]


def load_chatters():
    chatters = {
        username: []
        for username in CHATTERS.values()
    }

    for chat_file in Path(CHAT_DIR).glob("*.txt"):
        try:
            with open(chat_file, "r", encoding="utf-8") as file:
                lines = file.readlines()
        except Exception:
            continue

        current_date = None

        for raw_line in lines:
            line = raw_line.strip()

            if not line:
                continue

            date_match = re.fullmatch(
                r"(\d{1,2})-(\d{1,2})-(\d{4})",
                line
            )

            if date_match:
                day, month, year = date_match.groups()
                current_date = f"{day.zfill(2)}-{month.zfill(2)}-{year}"
                continue

            time_match = re.match(
                r"^\d{1,2}:\d{2}\s*(.*)$",
                line
            )

            if not time_match:
                continue

            rest = time_match.group(1)

            colon_index = rest.find(":")

            if colon_index == -1:
                continue

            prefix = rest[:colon_index]
            message = rest[colon_index + 1:].strip()

            if len(message) < MIN_CHARACTERS:
                continue

            if current_date is None:
                continue

            chatter = find_chatter(prefix)

            if not chatter:
                continue

            _, username = chatter

            chatters[username].append(
                (message, current_date)
            )

    return {
        username: messages
        for username, messages in chatters.items()
        if messages
    }


def display_name_for(username):
    for display_name, exact_username in CHATTERS.items():
        if exact_username.lower() == username.lower():
            return display_name

    return username


@client.event
async def on_ready():
    try:
        channel = await client.fetch_channel(CHANNEL_ID)

        chatters = load_chatters()

        if len(chatters) < POLL_OPTIONS:
            await channel.send(
                "Not enough valid chatters for a 5-option poll."
            )
            return

        username = random.choice(list(chatters.keys()))
        message, date = random.choice(chatters[username])

        correct_display_name = display_name_for(username)

        wrong_usernames = [
            name
            for name in chatters.keys()
            if name != username
        ]

        wrong_usernames = random.sample(
            wrong_usernames,
            POLL_OPTIONS - 1
        )

        options = wrong_usernames + [username]
        random.shuffle(options)

        poll = discord.Poll(
            question="Who said this?",
            duration=timedelta(hours=1),
            multiple=False
        )

        for option in options:
            poll.add_answer(
                text=display_name_for(option)
            )

        message_content = (
            f"💬 **Guess the Chatter**\n\n"
            f"> {message}\n\n"
            f"📅 **Date:** {date}"
        )

        poll_message = await channel.send(
            content=message_content,
            poll=poll
        )

        await asyncio.sleep(ANSWER_DELAY_SECONDS)

        try:
            await poll_message.end_poll()
        except discord.HTTPException:
            pass

        await channel.send(
            f"🔓 **The answer was:** ||{correct_display_name}||"
        )

    finally:
        await client.close()


client.run(TOKEN)
