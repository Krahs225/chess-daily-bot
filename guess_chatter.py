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
POLL_OPTIONS = 5


# Display name -> exact Twitch username
CHATTERS = {
    "AZ": "az3d__",
    "Ben": "bcutter1",
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
}


intents = discord.Intents.default()
client = discord.Client(intents=intents)


def find_chatter(prefix):
    prefix = prefix.strip().lower()

    matches = []

    for display_name, username in CHATTERS.items():
        username_lower = username.lower()

        if prefix.endswith(username_lower):
            matches.append((len(username_lower), display_name, username))

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

            # Date line
            date_match = re.fullmatch(
                r"(\d{1,2})-(\d{1,2})-(\d{4})",
                line
            )

            if date_match:
                day, month, year = date_match.groups()
                current_date = f"{day.zfill(2)}-{month.zfill(2)}-{year}"
                continue

            # Message must start with a time
            time_match = re.match(
                r"^\d{1,2}:\d{2}\s*(.*)$",
                line
            )

            if not time_match:
                continue

            rest = time_match.group(1)

            # Find the first colon that separates sender from message
            colon_index = rest.find(":")

            if colon_index == -1:
                continue

            prefix = rest[:colon_index]
            message = rest[colon_index + 1:].strip()

            if len(message) < MIN_CHARACTERS:
                continue

            chatter = find_chatter(prefix)

            if not chatter:
                continue

            _, username = chatter

            if current_date is None:
                continue

            chatters[username].append(
                (message, current_date)
            )

    # Remove chatters with no valid messages
    chatters = {
        username: messages
        for username, messages in chatters.items()
        if messages
    }

    return chatters


def display_name_for(username):
    for display_name, exact_username in CHATTERS.items():
        if exact_username.lower() == username.lower():
            return display_name

    return username


async def post_guess(channel, chatters):
    if len(chatters) < POLL_OPTIONS:
        await channel.send(
            "Not enough valid chatters for a 5-option poll."
        )
        return

    # Pick a random chatter first
    username = random.choice(list(chatters.keys()))

    # Then pick a random message from that chatter
    message, date = random.choice(chatters[username])

    correct_display_name = display_name_for(username)

    # Pick 4 wrong chatters
    wrong_usernames = [
        name for name in chatters.keys()
        if name != username
    ]

    wrong_usernames = random.sample(
        wrong_usernames,
        POLL_OPTIONS - 1
    )

    options = wrong_usernames + [username]
    random.shuffle(options)

    await channel.send(
        f"💬 **Guess the Chatter**\n\n"
        f"> {message}\n\n"
        f"📅 **Date:** {date}"
    )

    poll = discord.Poll(
        question="Who said this?",
        duration=1,
        multiple=False
    )

    for option in options:
        poll.add_answer(
            text=display_name_for(option)
        )

    await channel.send(poll=poll)

    await asyncio.sleep(ANSWER_DELAY_SECONDS)

    await channel.send(
        f"🔓 **The answer was:** ||{correct_display_name}||"
    )


@client.event
async def on_ready():
    channel = await client.fetch_channel(CHANNEL_ID)

    chatters = load_chatters()

    if not chatters:
        await channel.send("No valid chatters found.")
        await client.close()
        return

    while True:
        await post_guess(channel, chatters)

        await asyncio.sleep(
            QUOTE_INTERVAL_SECONDS - ANSWER_DELAY_SECONDS
        )


client.run(TOKEN)
