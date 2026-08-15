import asyncio
import os
import random
import re
from datetime import timedelta
from pathlib import Path

import discord

from shared_leaderboard import (
    add_points,
    full_leaderboard,
    get_score,
    personal_ranking,
)

TOKEN = os.getenv("DISCORD_TOKEN")
CHANNEL_ID = 1536769340970373241

MIN_CHARACTERS = 20
CHAT_DIR = "SOLO chats"
POLL_OPTIONS = 5

# Guess the Chatter runs on :00, :20, :40.
ROUND_MINUTES = {0, 20, 40}
POLL_DURATION_MINUTES = 15

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
intents.message_content = True
client = discord.Client(intents=intents)


def find_chatter(prefix):
    prefix = prefix.strip().casefold()
    matches = []

    for display_name, username in CHATTERS.items():
        username_lower = username.casefold()
        if prefix.endswith(username_lower):
            matches.append((len(username_lower), display_name, username))

    if not matches:
        return None

    matches.sort(reverse=True)
    return matches[0][1], matches[0][2]


def load_chatters():
    chatters = {username: [] for username in CHATTERS.values()}

    for chat_file in Path(CHAT_DIR).glob("*.txt"):
        try:
            lines = chat_file.read_text(encoding="utf-8").splitlines()
        except Exception:
            continue

        current_date = None

        for raw_line in lines:
            line = raw_line.strip()
            if not line:
                continue

            date_match = re.fullmatch(
                r"(\d{1,2})-(\d{1,2})-(\d{4})",
                line,
            )
            if date_match:
                day, month, year = date_match.groups()
                current_date = f"{day.zfill(2)}-{month.zfill(2)}-{year}"
                continue

            time_match = re.match(r"^\d{1,2}:\d{2}\s*(.*)$", line)
            if not time_match:
                continue

            rest = time_match.group(1)
            colon_index = rest.find(":")
            if colon_index == -1:
                continue

            prefix = rest[:colon_index]
            message = rest[colon_index + 1 :].strip()

            if len(message) < MIN_CHARACTERS or current_date is None:
                continue

            chatter = find_chatter(prefix)
            if not chatter:
                continue

            _, username = chatter
            chatters[username].append((message, current_date))

    return {username: values for username, values in chatters.items() if values}


def display_name_for(username):
    for display_name, exact_username in CHATTERS.items():
        if exact_username.casefold() == username.casefold():
            return display_name
    return username


async def send_score_result(channel, user, points):
    total = add_points(user.id, user.display_name, points)

    if points == 1:
        await channel.send(
            f"✅ **Correct, {user.display_name}!**\n"
            f"**+1 point** — you now have **{total:g} points.**"
        )

    ranking = personal_ranking(user.id)
    if ranking:
        await channel.send(ranking)


async def post_guess(channel, chatters):
    if len(chatters) < POLL_OPTIONS:
        await channel.send(
            "Not enough valid chatters for a 5-option poll."
        )
        return

    username = random.choice(list(chatters.keys()))
    quote, date = random.choice(chatters[username])
    correct_display_name = display_name_for(username)

    wrong_usernames = [
        name for name in chatters.keys() if name != username
    ]
    wrong_usernames = random.sample(
        wrong_usernames,
        POLL_OPTIONS - 1,
    )

    options = wrong_usernames + [username]
    random.shuffle(options)

    poll = discord.Poll(
        question="Who said this?",
        duration=timedelta(minutes=POLL_DURATION_MINUTES),
        multiple=False,
    )

    answer_index = None
    for index, option in enumerate(options):
        poll.add_answer(text=display_name_for(option))
        if option == username:
            answer_index = index

    message_content = (
        f"💬 **Guess the Chatter**\n\n"
        f"> {quote}\n\n"
        f"📅 **Date:** {date}"
    )

    poll_message = await channel.send(
        content=message_content,
        poll=poll,
    )

    # Poll stays open; the API ends it at its configured duration.
    # We check the answer after the end.
    await asyncio.sleep(POLL_DURATION_MINUTES * 60 + 3)

    try:
        await poll_message.end_poll()
    except discord.HTTPException:
        pass

    voters = []
    try:
        for answer in poll.answers:
            voters_for_answer = []
            async for voter in answer.voters():
                voters_for_answer.append(voter)
            voters.append(voters_for_answer)
    except Exception as exc:
        print(f"Could not read poll voters: {exc}", flush=True)
        voters = []

    # If voter access is unavailable, reveal only.
    if voters and answer_index is not None:
        correct_users = voters[answer_index]
        for voter in correct_users:
            if voter.bot:
                continue
            await send_score_result(channel, voter, 1)

    await channel.send(
        f"🔓 **The answer was:** ||{correct_display_name}||"
    )


async def daily_scheduler(channel):
    """
    This bot only posts on :00, :20, :40 local GitHub runner time.
    The other bot is responsible for :10, :30, :50.
    """
    last_round_key = None

    while True:
        now = discord.utils.utcnow()
        minute = now.minute
        second = now.second

        if minute in ROUND_MINUTES and second < 10:
            round_key = now.strftime("%Y-%m-%d-%H-%M")
            if round_key != last_round_key:
                last_round_key = round_key
                asyncio.create_task(
                    post_guess(channel, load_chatters())
                )

        await asyncio.sleep(5)


@client.event
async def on_message(message):
    if message.author.bot or message.channel.id != CHANNEL_ID:
        return

    command = message.content.strip().casefold()

    if command in {"!leaderboard", "!lb", "!l"}:
        await message.channel.send(
            full_leaderboard("🏆 **Shared Leaderboard**")
        )
        return

    if command in {"!help", "!info"}:
        await message.channel.send(
            "**Guess the Chatter**\n"
            "`!leaderboard`, `!lb`, `!l` — shared leaderboard\n"
            "`!help`, `!info` — this message\n\n"
            "Guess the Chatter runs on **:00, :20 and :40**."
        )


@client.event
async def on_ready():
    print(f"Guess Chatter ready as {client.user}", flush=True)

    channel = await client.fetch_channel(CHANNEL_ID)

    if not load_chatters():
        await channel.send("No valid chatters found.")
        await client.close()
        return

    asyncio.create_task(daily_scheduler(channel))


client.run(TOKEN)
