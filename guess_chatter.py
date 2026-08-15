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
POLL_DURATION_MINUTES = 2


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


def _parse_chat_file(chat_file):
    entries = []
    current_date = None

    try:
        lines = chat_file.read_text(
            encoding="utf-8"
        ).splitlines()
    except Exception:
        return entries

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
            r"^(\d{1,2}):(\d{2})\s*(.*)$",
            line
        )

        if not time_match or current_date is None:
            continue

        hour, minute, rest = (
            time_match.groups()
        )

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

        entries.append(
            {
                "date": current_date,
                "time": (
                    f"{hour.zfill(2)}:"
                    f"{minute}"
                ),
                "username": chatter[1],
                "display_name": chatter[0],
                "message": message,
            }
        )

    return entries


def load_chatters():

    chatters = {}
    all_entries = []

    chat_path = Path(
        CHAT_DIR
    )

    if not chat_path.exists():
        return {}, []

    for chat_file in sorted(
        chat_path.glob("*.txt")
    ):

        entries = _parse_chat_file(
            chat_file
        )

        all_entries.extend(
            entries
        )

        for global_index, entry in enumerate(
            entries
        ):

            chatters.setdefault(
                entry["username"],
                []
            ).append(
                (
                    entry["message"],
                    entry["date"],
                    sum(
                        len(
                            _parse_chat_file(
                                f
                            )
                        )
                        for f in []
                    ) + global_index,
                )
            )

    # The global index above is local to the file. Rebuild it cleanly.
    rebuilt = {}
    for index, entry in enumerate(
        all_entries
    ):
        rebuilt.setdefault(
            entry["username"],
            []
        ).append(
            (
                entry["message"],
                entry["date"],
                index,
            )
        )

    return (
        {
            username: values
            for username, values
            in rebuilt.items()
            if values
        },
        all_entries,
    )


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


def days_ago(date_text):
    try:

        date_value = datetime.strptime(
            date_text,
            "%d-%m-%Y"
        ).date()

        today = datetime.now(
            timezone.utc
        ).date()

        return (
            today - date_value
        ).days

    except Exception:
        return 0


def context_for_quote(
    all_entries,
    quote_index,
    max_lines=5
):

    if not all_entries:
        return []

    if (
        quote_index < 0
        or quote_index >= len(all_entries)
    ):
        return []

    target = all_entries[
        quote_index
    ]

    same_date = [
        index
        for index, entry
        in enumerate(all_entries)
        if entry["date"] == target["date"]
    ]

    if not same_date:
        return [target]

    local_index = min(
        range(len(same_date)),
        key=lambda i:
            abs(
                same_date[i]
                - quote_index
            )
    )

    start_index = max(
        0,
        local_index - 2
    )

    end_index = min(
        len(same_date),
        start_index + max_lines
    )

    return [
        all_entries[index]
        for index
        in same_date[
            start_index:end_index
        ]
    ]


def answer_details(
    all_entries,
    correct_index,
    voters_by_answer,
    quote_date,
    quote_index
):

    correct_count = 0
    total_votes = 0

    if voters_by_answer:

        total_votes = sum(
            len(voters)
            for voters in voters_by_answer
        )

        if (
            correct_index
            < len(voters_by_answer)
        ):

            correct_count = len(
                voters_by_answer[
                    correct_index
                ]
            )

    percentage = (
        round(
            correct_count
            / total_votes
            * 100
        )
        if total_votes
        else 0
    )

    possible_chatters = len({
        entry["username"]
        for entry in all_entries
        if entry["date"] == quote_date
    })

    context = context_for_quote(
        all_entries,
        quote_index
    )

    answer_name = display_name_for(
        all_entries[quote_index]["username"]
    )

    lines = [
        f"🔓 **The answer was: {answer_name}**",
        "",
        f"📊 **{correct_count}/{total_votes}** "
        f"people got it right "
        f"(**{percentage}%**).",
        f"👥 **{possible_chatters} possible "
        f"chatters on this date.**",
    ]

    if context:

        lines.extend(
            [
                "",
                "**Context:**"
            ]
        )

        for entry in context:

            lines.append(
                f"**{entry['display_name']}:** "
                f"{entry['message']}"
            )

    return "\n".join(
        lines
    )


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

    chatters, all_entries = load_chatters()

    if len(chatters) < POLL_OPTIONS:

        await channel.send(
            "Not enough valid chatters "
            "for a 5-option poll."
        )

        return

    username = random.choice(
        list(chatters.keys())
    )

    quote, date, quote_index = random.choice(
        chatters[username]
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

    await asyncio.sleep(
        POLL_DURATION_MINUTES * 60 + 2
    )

    try:

        await poll_message.end_poll()

    except discord.HTTPException:
        pass

    try:

        voters_by_answer = []

        for answer in poll.answers:

            answer_voters = []

            async for voter in (
                answer.voters()
            ):

                if not voter.bot:
                    answer_voters.append(
                        voter
                    )

            voters_by_answer.append(
                answer_voters
            )

    except Exception as error:

        print(
            f"Guess Chatter poll result error: "
            f"{error}",
            flush=True
        )

        voters_by_answer = []

    # Restore the rich answer message:
    # answer, percentage, possible chatters,
    # age of quote, and context.
    await channel.send(
        answer_details(
            all_entries,
            correct_index,
            voters_by_answer,
            date,
            quote_index
        )
    )

    # Every correct voter gets +1.
    if (
        correct_index
        < len(voters_by_answer)
    ):

        seen = set()

        for voter in voters_by_answer[
            correct_index
        ]:

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
                f"{voter.display_name}!** 🎉\n"
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
            "🧠 **Games**\n\n"
            "💬 **Guess the Chatter**\n"
                        "A quote is shown with a 5-option poll. "
            "Vote for who said it.\n\n"
            "♟️ **Guess the Chess Chatter**\n"
            "A rated Chess.com rapid/blitz game is shown. "
            "Use the ◀ ▶ buttons to look through the game, "
            "then vote for who played it.\n\n"
            "🏆 **Leaderboard**\n"
            "`!leaderboard`, `!lb` or `!l` — show the full shared leaderboard.\n"
            "Correct guesses give **+1 point**. Both games use the same leaderboard.\n\n"
            "ℹ️ `!help` or `!info` — show this message."
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
