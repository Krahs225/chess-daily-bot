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
POLL_DURATION_MINUTES = 8
ROUND_SLOT_MINUTES = 20
GUESS_SLOT_OFFSET = 0
TIME_ZONE = "Europe/Amsterdam"


CHATTERS = {
    "AZ": "az3d__",
    "Ben": "benniru",
    "Geeflux": "geeflux",
    "George": "georgeonz0la",
    "Grumpymonk": "grumpymonk147",
    "Jessebrawlstars": "jessebrawlstars",
    "Kurupt": "kurupttv",
    "Martin": "martin_xploz",
    "MH": "mh050131",
    "Mohammad": "mohammad_768",
    "Mr_thice": "mr_thice",
    "Nairyaaa": "nairyaaa",
    "Pabu": "notpabu",
    "Pandarou": "pandarou",
    "Pospos": "pospos12",
    "Rubriek": "rubriek",
    "Sativahibread": "sativahibread",
    "Screamingcat": "screamingcat_02n7",
    "Sh4rkmate is the best": "sh4rkmate_is_the_best",
    "Soyadelson": "soyadelson7",
    "Stepu": "stepu6568",
    "Sushi": "isolatedsushi11",
    "Thejazzdude": "thejazzdude_",
}

# Chatter eligibility is date-based. A chatter can only be selected
# on a date inside their supplied active window.
CHATTER_ACTIVE_DATES = {
    "az3d__": ("01-06-2025", "14-08-2026"),
    "benniru": ("09-09-2025", "13-08-2026"),
    "geeflux": ("02-10-2024", "09-05-2026"),
    "georgeonz0la": ("02-02-2025", "15-08-2026"),
    "grumpymonk147": ("03-10-2024", "11-08-2026"),
    "jessebrawlstars": ("16-04-2026", "14-08-2026"),
    "kurupttv": ("02-04-2026", "03-08-2026"),
    "martin_xploz": ("29-08-2024", "17-08-2026"),
    "mh050131": ("14-05-2024", "14-05-2025"),
    "mohammad_768": ("29-11-2024", "11-08-2026"),
    "mr_thice": ("30-06-2024", "15-08-2026"),
    "nairyaaa": ("30-06-2026", "15-08-2026"),
    "notpabu": ("26-11-2024", "15-07-2026"),
    "pandarou": ("05-06-2024", "15-08-2026"),
    "pospos12": ("12-07-2025", "29-07-2026"),
    "rubriek": ("15-01-2025", "24-07-2026"),
    "sativahibread": ("31-10-2024", "10-08-2026"),
    "screamingcat_02n7": ("26-08-2024", "13-08-2026"),
    "sh4rkmate_is_the_best": ("24-05-2026", "01-08-2026"),
    "soyadelson7": ("29-07-2025", "15-08-2026"),
    "stepu6568": ("06-09-2025", "16-08-2026"),
    "isolatedsushi11": ("06-05-2024", "15-08-2026"),
    "thejazzdude_": ("12-05-2026", "16-08-2026"),
}


def chatter_active_on_date(
    username,
    date_text,
):
    active_range = CHATTER_ACTIVE_DATES.get(
        username.casefold()
    )

    if not active_range:
        return False

    try:
        date_value = datetime.strptime(
            date_text,
            "%d-%m-%Y",
        ).date()

        first_date = datetime.strptime(
            active_range[0],
            "%d-%m-%Y",
        ).date()

        last_date = datetime.strptime(
            active_range[1],
            "%d-%m-%Y",
        ).date()

        return (
            first_date
            <= date_value
            <= last_date
        )

    except Exception:
        return False



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

        if not chatter_active_on_date(
            chatter[1],
            current_date,
        ):
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


def current_local_time():
    from zoneinfo import ZoneInfo

    return datetime.now(
        ZoneInfo(
            TIME_ZONE
        )
    )


def next_guess_slot():
    now = current_local_time()

    total_minutes = (
        now.hour * 60
        + now.minute
    )

    remainder = (
        total_minutes
        - GUESS_SLOT_OFFSET
    ) % ROUND_SLOT_MINUTES

    wait_minutes = (
        ROUND_SLOT_MINUTES
        - remainder
    )

    if (
        remainder == 0
        and now.second == 0
        and now.microsecond == 0
    ):
        wait_minutes = 0

    target = (
        now
        + timedelta(
            minutes=wait_minutes
        )
    ).replace(
        second=0,
        microsecond=0
    )

    if target <= now:
        target += timedelta(
            minutes=ROUND_SLOT_MINUTES
        )

    return target


def guess_special_mode(
    moment=None
):
    """
    Exactly four special Guess Chatter rounds per local day:
    two Double Points and two Hard Mode rounds.

    Hard Mode uses only three poll options and is worth +2.
    Double Points keeps the normal five options and is worth +2.
    The four slots are deterministic for the day.
    """
    if moment is None:
        moment = current_local_time()

    slot_index = (
        moment.hour * 60
        + moment.minute
    ) // ROUND_SLOT_MINUTES

    rng = random.Random(
        moment.date().toordinal()
    )

    special_slots = rng.sample(
        range(72),
        4
    )

    if slot_index == special_slots[0]:
        return "double"

    if slot_index == special_slots[1]:
        return "double"

    if slot_index == special_slots[2]:
        return "hard"

    if slot_index == special_slots[3]:
        return "hard"

    return "normal"


async def post_guess(
    channel
):

    chatters, all_entries = load_chatters()

    mode = guess_special_mode()

    option_count = (
        3
        if mode == "hard"
        else POLL_OPTIONS
    )

    points_awarded = (
        2
        if mode in {
            "double",
            "hard",
        }
        else 1
    )

    if len(chatters) < option_count:
        await channel.send(
            "Not enough valid chatters "
            "for this Guess Chatter mode."
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
        option_count - 1
    ):
        await channel.send(
            "Not enough valid chatters "
            "for this Guess Chatter mode."
        )
        return

    wrong_usernames = random.sample(
        wrong_usernames,
        option_count - 1
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

    if mode == "hard":
        poll_question = (
            "💀 HARD MODE — Who said this?"
        )
        message_header = (
            "💀 **Guess the Chatter — HARD MODE**"
        )
        message_content = (
            f"{message_header}\n\n"
            f"> {quote}"
        )

    elif mode == "double":
        poll_question = (
            "🔥 DOUBLE POINTS — Who said this?"
        )
        message_header = (
            "🔥 **Guess the Chatter — DOUBLE POINTS**"
        )
        message_content = (
            f"{message_header}\n\n"
            f"> {quote}\n\n"
            f"📅 **Date:** {date}"
        )

    else:
        poll_question = "Who said this?"
        message_content = (
            "💬 **Guess the Chatter**\n\n"
            f"> {quote}\n\n"
            f"📅 **Date:** {date}"
        )

    poll = discord.Poll(
        question=poll_question,
        duration=timedelta(
            hours=1
        ),
        multiple=False,
    )

    for option in options:
        poll.add_answer(
            text=display_name_for(
                option
            )
        )

    poll_message = await channel.send(
        content=message_content,
        poll=poll,
    )

    # 8-minute answering window, followed by an answer
    # roughly 2 minutes before the next 10-minute slot.
    await asyncio.sleep(
        POLL_DURATION_MINUTES * 60
        + 2
    )

    try:
        await poll_message.end_poll()
    except Exception as error:
        print(
            f"Guess Chatter poll end error: "
            f"{error}",
            flush=True,
        )

    try:
        voters_by_answer = []

        finished_message = await channel.fetch_message(
            poll_message.id
        )

        finished_poll = (
            finished_message.poll
            if finished_message.poll is not None
            else poll
        )

        for answer in finished_poll.answers:
            answer_voters = []

            async for voter in answer.voters():
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
            flush=True,
        )
        voters_by_answer = []

    await channel.send(
        answer_details(
            all_entries,
            correct_index,
            voters_by_answer,
            date,
            quote_index,
        )
    )

    rewarded = []
    seen = set()

    if (
        correct_index
        < len(voters_by_answer)
    ):

        for voter in voters_by_answer[
            correct_index
        ]:

            if voter.id in seen:
                continue

            seen.add(
                voter.id
            )

            try:
                add_points(
                    voter.id,
                    voter.display_name,
                    points_awarded,
                    transaction_id=(
                        f"guess:{poll_message.id}:{voter.id}"
                    ),
                    source=(
                        f"guess-chatter-{mode}"
                    ),
                )

                rewarded.append(
                    voter.display_name
                )

            except Exception as error:
                print(
                    f"Guess leaderboard error "
                    f"for {voter.display_name}: "
                    f"{error}",
                    flush=True,
                )

    if rewarded:
        names = " • ".join(
            f"**{name} +{points_awarded}**"
            for name in rewarded
        )

        await channel.send(
            f"🎉 {names}"
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
            "Correct guesses give **+1 point** normally. Double Points and Hard Mode give **+2 points**. Both games use the same leaderboard.\n\n"
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


async def guess_chatter_loop():

    channel = await client.fetch_channel(
        CHANNEL_ID
    )

    while True:

        target = next_guess_slot()

        now = current_local_time()

        wait_seconds = (
            target - now
        ).total_seconds()

        if wait_seconds > 0:
            print(
                f"Next Guess Chatter round: "
                f"{target.isoformat()}",
                flush=True,
            )
            await asyncio.sleep(
                wait_seconds
            )

        round_started = current_local_time()

        try:
            print(
                "Starting Guess Chatter round "
                f"({guess_special_mode(round_started)})...",
                flush=True,
            )

            await post_guess(
                channel
            )

            print(
                "Guess Chatter round finished.",
                flush=True,
            )

        except Exception as error:
            print(
                f"Guess Chatter round error: "
                f"{error}",
                flush=True,
            )

        # Do NOT sleep a fixed 20 minutes from completion.
        # Always re-align to the next exact 20-minute Guess slot.


@client.event
async def on_ready():

    print(
        f"Guess Chatter ready as "
        f"{client.user}",
        flush=True
    )

    if not hasattr(
        client,
        "_guess_chatter_task"
    ) or client._guess_chatter_task.done():

        client._guess_chatter_task = (
            asyncio.create_task(
                guess_chatter_loop()
            )
        )


client.run(
    TOKEN
)
