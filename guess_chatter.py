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
    personal_ranking,
)

TOKEN = os.getenv(
    "DISCORD_TOKEN"
)

CHANNEL_ID = 1536769340970373241

MIN_CHARACTERS = 20
CHAT_DIR = "SOLO chats"

POLL_OPTIONS = 5
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

client = discord.Client(
    intents=intents
)


def find_chatter(
    prefix
):
    prefix = prefix.strip().casefold()

    matches = []

    for display_name, username in CHATTERS.items():

        username_lower = (
            username.casefold()
        )

        if prefix.endswith(
            username_lower
        ):

            matches.append(
                (
                    len(username_lower),
                    display_name,
                    username
                )
            )

    if not matches:
        return None

    matches.sort(
        reverse=True
    )

    return (
        matches[0][1],
        matches[0][2]
    )


def load_chatters():

    chatters = {
        username: []
        for username in CHATTERS.values()
    }

    chat_path = Path(
        CHAT_DIR
    )

    if not chat_path.exists():
        return {}

    for chat_file in chat_path.glob(
        "*.txt"
    ):

        try:
            lines = chat_file.read_text(
                encoding="utf-8"
            ).splitlines()
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

                day, month, year = (
                    date_match.groups()
                )

                current_date = (
                    f"{day.zfill(2)}-"
                    f"{month.zfill(2)}-"
                    f"{year}"
                )

                continue

            time_match = re.match(
                r"^\d{1,2}:\d{2}\s*(.*)$",
                line
            )

            if not time_match:
                continue

            rest = (
                time_match.group(1)
            )

            colon_index = (
                rest.find(":")
            )

            if colon_index == -1:
                continue

            prefix = (
                rest[:colon_index]
            )

            message_text = (
                rest[colon_index + 1:]
                .strip()
            )

            if (
                len(message_text)
                < MIN_CHARACTERS
                or current_date is None
            ):
                continue

            chatter = find_chatter(
                prefix
            )

            if not chatter:
                continue

            _, username = chatter

            chatters[
                username
            ].append(
                (
                    message_text,
                    current_date
                )
            )

    return {
        username: values
        for username, values
        in chatters.items()
        if values
    }


def display_name_for(
    username
):

    for display_name, exact_username in CHATTERS.items():

        if (
            exact_username.casefold()
            == username.casefold()
        ):
            return display_name

    return username


async def wait_and_finish_poll(
    poll_message
):

    await asyncio.sleep(
        POLL_DURATION_MINUTES * 60 + 3
    )

    try:
        await poll_message.end_poll()
    except discord.HTTPException:
        pass


async def post_guess(
    channel
):

    chatters = load_chatters()

    if len(chatters) < POLL_OPTIONS:

        await channel.send(
            "Not enough valid chatters "
            "for a 5-option poll."
        )

        return

    username = random.choice(
        list(chatters.keys())
    )

    quote, date = random.choice(
        chatters[username]
    )

    correct_display_name = (
        display_name_for(username)
    )

    wrong_usernames = [
        name
        for name in chatters.keys()
        if name != username
    ]

    if len(wrong_usernames) < (
        POLL_OPTIONS - 1
    ):

        await channel.send(
            "Not enough valid chatters "
            "for a 5-option poll."
        )

        return

    wrong_usernames = random.sample(
        wrong_usernames,
        POLL_OPTIONS - 1
    )

    options = (
        wrong_usernames
        + [username]
    )

    random.shuffle(
        options
    )

    correct_index = options.index(
        username
    )

    poll = discord.Poll(
        question="Who said this?",
        duration=timedelta(
            hours=1
        ),
        multiple=False
    )

    for option in options:

        poll.add_answer(
            text=display_name_for(
                option
            )
        )

    message_content = (
        "💬 **Guess the Chatter**\n\n"
        f"> {quote}\n\n"
        f"📅 **Date:** {date}"
    )

    poll_message = await channel.send(
        content=message_content,
        poll=poll
    )

    await wait_and_finish_poll(
        poll_message
    )

    # Read the final poll results.
    try:

        voters_by_answer = []

        for answer in poll.answers:

            answer_voters = []

            async for voter in (
                answer.voters()
            ):

                answer_voters.append(
                    voter
                )

            voters_by_answer.append(
                answer_voters
            )

    except Exception as error:

        voters_by_answer = []

        print(
            f"Guess Chatter poll "
            f"result error: {error}",
            flush=True
        )

    # Every correct voter gets +1.
    if (
        voters_by_answer
        and correct_index
        < len(voters_by_answer)
    ):

        seen = set()

        for voter in voters_by_answer[
            correct_index
        ]:

            if voter.bot:
                continue

            if voter.id in seen:
                continue

            seen.add(
                voter.id
            )

            total = add_points(
                voter.id,
                voter.display_name,
                1
            )

            await channel.send(
                f"✅ **Correct, "
                f"{voter.display_name}!**\n"
                f"**+1 point** — you now have "
                f"**{total:g} points.**"
            )

            ranking = personal_ranking(
                voter.id
            )

            if ranking:
                await channel.send(
                    ranking
                )

    await channel.send(
        f"🔓 **The answer was:** "
        f"||{correct_display_name}||"
    )


async def command_handler(
    message
):

    if (
        message.author.bot
        or message.channel.id
        != CHANNEL_ID
    ):
        return

    command = (
        message.content
        .strip()
        .casefold()
    )

    if command in {
        "!leaderboard",
        "!lb",
        "!l"
    }:

        await message.channel.send(
            full_leaderboard(
                "🏆 **Shared Leaderboard**"
            )
        )

        return

    if command in {
        "!help",
        "!info"
    }:

        await message.channel.send(
            "**Guess the Chatter**\n"
            "`!leaderboard`, `!lb`, `!l` "
            "— shared leaderboard\n"
            "`!help`, `!info` — this message"
        )


@client.event
async def on_message(
    message
):

    try:

        await command_handler(
            message
        )

    except Exception as error:

        print(
            f"Guess Chatter command error: "
            f"{error}",
            flush=True
        )

        try:

            await message.channel.send(
                "❌ **Bot error:** "
                f"`{str(error)[:1000]}`"
            )

        except Exception:
            pass


@client.event
async def on_ready():

    print(
        f"Guess Chatter ready as "
        f"{client.user}",
        flush=True
    )

    try:

        channel = await client.fetch_channel(
            CHANNEL_ID
        )

        # This Action is deliberately a
        # one-round process. The GitHub Action
        # schedule starts the next round.
        await post_guess(
            channel
        )

    except Exception as error:

        print(
            f"Guess Chatter startup error: "
            f"{error}",
            flush=True
        )

    finally:

        await client.close()


client.run(
    TOKEN
)
