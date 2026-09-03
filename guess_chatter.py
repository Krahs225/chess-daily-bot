import asyncio
import os
import random
import re
from datetime import datetime, timezone, timedelta
from pathlib import Path

import discord
import requests

from guess_leaderboard import (
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

GITHUB_ACTION_TOKEN = os.getenv(
    "GITHUB_ACTION_TOKEN"
)
GITHUB_REPOSITORY = os.getenv(
    "GITHUB_REPOSITORY",
    "Krahs225/chess-daily-bot",
)
GITHUB_REF_NAME = os.getenv(
    "GITHUB_REF_NAME",
    "main",
)

NEXT_ROUND_EVENT = asyncio.Event()
ROUND_ACTIVE = False
NEXT_REQUESTED = False

ROUND_PREFIXES = {
    "chatter": (
        "💬 **Guess the Chatter**",
        "🔥 **Guess the Chatter — DOUBLE POINTS**",
        "💀 **Guess the Chatter — HARD MODE**",
    ),
    "chess": (
        "♟️ **Guess the Chess Chatter** —",
    ),
}

ROUND_MAX_AGE_MINUTES = {
    "chatter": 10,
    "chess": 17,
}


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


# A quote/option is valid only inside this chatter's active window.
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


def _round_type_for_message(
    message
):
    content = message.content or ""

    for round_type, prefixes in ROUND_PREFIXES.items():
        if any(
            content.startswith(prefix)
            for prefix in prefixes
        ):
            return round_type

    return None


async def _poll_is_open(
    message,
    round_type,
):
    if message.poll is None:
        return False

    max_age = timedelta(
        minutes=ROUND_MAX_AGE_MINUTES[
            round_type
        ]
    )

    if (
        datetime.now(timezone.utc)
        - message.created_at
        > max_age
    ):
        return False

    try:
        fresh_message = await message.channel.fetch_message(
            message.id
        )

        if fresh_message.poll is None:
            return False

        return not fresh_message.poll.is_finalised()

    except Exception as error:
        print(
            f"Guess round state check error: {error}",
            flush=True,
        )

        return True


async def active_round_exists(
    channel,
    round_type,
):
    async for recent in channel.history(
        limit=60
    ):
        if (
            client.user is not None
            and recent.author.id
            != client.user.id
        ):
            continue

        if _round_type_for_message(
            recent
        ) != round_type:
            continue

        return await _poll_is_open(
            recent,
            round_type,
        )

    return False


async def latest_active_round_type(
    channel
):
    async for recent in channel.history(
        limit=60
    ):
        if (
            client.user is not None
            and recent.author.id
            != client.user.id
        ):
            continue

        round_type = _round_type_for_message(
            recent
        )

        if round_type is None:
            continue

        if await _poll_is_open(
            recent,
            round_type,
        ):
            return round_type

    return None


def dispatch_workflow(
    workflow_file
):
    if not GITHUB_ACTION_TOKEN:
        raise RuntimeError(
            "GITHUB_ACTION_TOKEN is missing."
        )

    url = (
        "https://api.github.com/repos/"
        f"{GITHUB_REPOSITORY}/actions/workflows/"
        f"{workflow_file}/dispatches"
    )

    response = requests.post(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": (
                f"Bearer {GITHUB_ACTION_TOKEN}"
            ),
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "GuessGamesNext/1.0",
        },
        json={
            "ref": GITHUB_REF_NAME,
        },
        timeout=20,
    )

    if response.status_code != 204:
        raise RuntimeError(
            "Could not start next workflow: "
            f"HTTP {response.status_code} "
            f"{response.text[:300]}"
        )


async def post_guess(
    channel
):
    global ROUND_ACTIVE

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
        return False

    # Build valid candidates by the quote's EXACT date.
    # A wrong option can only appear when that chatter also has a
    # valid message on that same date.
    users_by_date = {}

    for candidate_username, entries in chatters.items():
        for _quote, entry_date, _index in entries:
            users_by_date.setdefault(
                entry_date,
                set(),
            ).add(
                candidate_username
            )

    eligible_quotes = []

    for candidate_username, entries in chatters.items():
        for candidate_quote, entry_date, candidate_index in entries:
            same_date_users = (
                users_by_date.get(
                    entry_date,
                    set(),
                )
                - {candidate_username}
            )

            if len(same_date_users) >= (
                option_count - 1
            ):
                eligible_quotes.append(
                    (
                        candidate_username,
                        candidate_quote,
                        entry_date,
                        candidate_index,
                    )
                )

    if not eligible_quotes:
        await channel.send(
            "Not enough same-date valid chatters "
            "for this Guess Chatter round."
        )
        return False

    (
        username,
        quote,
        date,
        quote_index,
    ) = random.choice(
        eligible_quotes
    )

    wrong_usernames = list(
        users_by_date.get(
            date,
            set(),
        )
        - {username}
    )

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

    ROUND_ACTIVE = True
    NEXT_ROUND_EVENT.clear()

    # Normal round: 8-minute answering window.
    # !n / !next wakes this wait immediately.
    try:
        await asyncio.wait_for(
            NEXT_ROUND_EVENT.wait(),
            timeout=(
                POLL_DURATION_MINUTES * 60
                + 2
            ),
        )
    except asyncio.TimeoutError:
        pass

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

    ROUND_ACTIVE = False

    return NEXT_REQUESTED


async def command_handler(
    message
):
    global NEXT_REQUESTED

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
        "!next",
        "!n",
    }:
        if not ROUND_ACTIVE:
            return

        active_type = await latest_active_round_type(
            message.channel
        )

        # If both Actions overlap, only the newest active game handles !n.
        if active_type != "chatter":
            return

        if NEXT_REQUESTED:
            return

        NEXT_REQUESTED = True
        NEXT_ROUND_EVENT.set()

        await message.channel.send(
            "⏭️ **Next!** Ending this Guess Chatter "
            "round now and checking the answers."
        )

        return

    if command in {
        "!leaderboard",
        "!lb",
        "!l"
    }:
        await message.channel.send(
            full_leaderboard(
                "🏆 **Guess Games Leaderboard**"
            )
        )

        return

    if command in {
        "!help",
        "!info",
        "!i"
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
            "⏭️ **Next round**\n"
            "`!next` or `!n` — end the active round now, "
            "reveal the answer, award points, and start the "
            "other Guess game immediately.\n\n"
            "🏆 **Guess Games Leaderboard**\n"
            "`!leaderboard`, `!lb` or `!l` — show the leaderboard shared ONLY by Guess the Chatter and Guess the Chess Chatter.\n"
            "This leaderboard is separate from Daily/Random chess puzzle points.\n"
            "Correct guesses give **+1 point** normally. Double Points and Hard Mode give **+2 points**.\n\n"
            "ℹ️ `!help`, `!info` or `!i` — show this message."
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
    if getattr(
        client,
        "_guess_round_started",
        False,
    ):
        return

    client._guess_round_started = True

    print(
        f"Guess Chatter ready as "
        f"{client.user}",
        flush=True,
    )

    try:
        channel = await client.fetch_channel(
            CHANNEL_ID
        )

        # Do not start a duplicate Guess Chatter round if a previous
        # scheduled or !next-triggered round is still active.
        if await active_round_exists(
            channel,
            "chatter",
        ):
            print(
                "Guess Chatter skipped: "
                "a Guess Chatter round is already active.",
                flush=True,
            )
            return

        # One Action run = one round.
        next_requested = await post_guess(
            channel
        )

        print(
            "Guess Chatter round finished.",
            flush=True,
        )

        if next_requested:
            if await active_round_exists(
                channel,
                "chess",
            ):
                await channel.send(
                    "♟️ **Guess the Chess Chatter is already active above.** "
                    "I won't start a duplicate round."
                )
            else:
                await asyncio.to_thread(
                    dispatch_workflow,
                    "guess_chess_chatter.yml",
                )

                await channel.send(
                    "⏭️ **Starting Guess the Chess Chatter now.**"
                )

    except Exception as error:
        print(
            f"Guess Chatter round error: "
            f"{error}",
            flush=True,
        )

        try:
            channel = client.get_channel(
                CHANNEL_ID
            )

            if channel is not None:
                await channel.send(
                    "❌ **Guess Chatter error:** "
                    f"`{str(error)[:900]}`"
                )
        except Exception:
            pass

    finally:
        await client.close()


client.run(
    TOKEN,
    reconnect=True,
)
