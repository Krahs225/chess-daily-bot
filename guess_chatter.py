import discord
import os
import random
import re
import asyncio
import json
import subprocess
from datetime import timedelta, datetime
from pathlib import Path


# =========================================================
# SETTINGS
# =========================================================

TOKEN = os.getenv("DISCORD_TOKEN")
CHANNEL_ID = 1536769340970373241

MIN_CHARACTERS = 20
CHAT_DIR = "SOLO chats"

POLL_OPTIONS = 5

# New quote every 5 minutes
QUOTE_INTERVAL_SECONDS = 5 * 60

# Answer after 3 minutes
ANSWER_DELAY_SECONDS = 3 * 60

# Leaderboard every 10 minutes
LEADERBOARD_INTERVAL_SECONDS = 10 * 60

# Permanent leaderboard
SCORES_FILE = "guess_chatter_scores.json"

# Context shown with the answer
CONTEXT_BEFORE = 2
CONTEXT_AFTER = 2


# =========================================================
# CHATTERS
# =========================================================

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


# =========================================================
# DISCORD
# =========================================================

intents = discord.Intents.default()
intents.message_content = True
intents.polls = True

client = discord.Client(intents=intents)


# =========================================================
# GLOBAL STATE
# =========================================================

scores = {}

active_polls = {}

scores_lock = asyncio.Lock()


# =========================================================
# SCORE FILE
# =========================================================

def load_scores():

    if not os.path.exists(SCORES_FILE):

        print(
            "No existing leaderboard found. "
            "Starting with 0 points.",
            flush=True
        )

        return {}

    try:

        with open(
            SCORES_FILE,
            "r",
            encoding="utf-8"
        ) as file:

            data = json.load(file)

        print(
            f"Loaded leaderboard with "
            f"{len(data)} players.",
            flush=True
        )

        return data

    except Exception as error:

        print(
            f"Could not load leaderboard: {error}",
            flush=True
        )

        return {}


def save_scores():

    with open(
        SCORES_FILE,
        "w",
        encoding="utf-8"
    ) as file:

        json.dump(
            scores,
            file,
            indent=2,
            ensure_ascii=False
        )


# =========================================================
# SAVE SCORES TO GITHUB
# =========================================================

def push_scores_to_github():

    try:

        subprocess.run(
            [
                "git",
                "config",
                "user.name",
                "Guess Chatter Bot"
            ],
            check=True,
            capture_output=True
        )

        subprocess.run(
            [
                "git",
                "config",
                "user.email",
                "guess-chatter-bot@users.noreply.github.com"
            ],
            check=True,
            capture_output=True
        )

        subprocess.run(
            [
                "git",
                "add",
                SCORES_FILE
            ],
            check=True,
            capture_output=True
        )

        commit = subprocess.run(
            [
                "git",
                "commit",
                "-m",
                "Update Guess the Chatter leaderboard"
            ],
            capture_output=True,
            text=True
        )

        if commit.returncode != 0:
            return

        branch = os.getenv(
            "GITHUB_REF_NAME",
            "main"
        )

        subprocess.run(
            [
                "git",
                "push",
                "origin",
                f"HEAD:{branch}"
            ],
            check=True,
            capture_output=True,
            text=True
        )

        print(
            "Leaderboard saved permanently.",
            flush=True
        )

    except Exception as error:

        print(
            f"Could not save leaderboard: {error}",
            flush=True
        )


async def save_scores_permanently():

    save_scores()

    await asyncio.to_thread(
        push_scores_to_github
    )


# =========================================================
# CHATTER MATCHING
# =========================================================

def find_chatter(prefix):

    prefix = prefix.strip().lower()

    matches = []

    for display_name, username in CHATTERS.items():

        username_lower = username.lower()

        if prefix.endswith(username_lower):

            matches.append(
                (
                    len(username_lower),
                    display_name,
                    username
                )
            )

    if not matches:
        return None

    matches.sort(reverse=True)

    return matches[0][1], matches[0][2]


# =========================================================
# DATE
# =========================================================

def parse_date(date_string):

    try:

        return datetime.strptime(
            date_string,
            "%d-%m-%Y"
        ).date()

    except ValueError:

        return None


# =========================================================
# PARSE CHAT FILE
# =========================================================

def load_chatters():

    chatters = {
        username: []
        for username in CHATTERS.values()
    }

    for chat_file in Path(
        CHAT_DIR
    ).glob("*.txt"):

        try:

            with open(
                chat_file,
                "r",
                encoding="utf-8"
            ) as file:

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

                day, month, year = (
                    date_match.groups()
                )

                current_date = (
                    f"{day.zfill(2)}-"
                    f"{month.zfill(2)}-"
                    f"{year}"
                )

                continue

            # Message line
            time_match = re.match(
                r"^(\d{1,2}:\d{2})\s*(.*)$",
                line
            )

            if not time_match:
                continue

            message_time = (
                time_match.group(1)
            )

            rest = (
                time_match.group(2)
            )

            colon_index = rest.find(":")

            if colon_index == -1:
                continue

            prefix = rest[
                :colon_index
            ]

            message = rest[
                colon_index + 1:
            ].strip()

            if len(message) < MIN_CHARACTERS:
                continue

            if current_date is None:
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
                {
                    "message": message,
                    "date": current_date,
                    "time": message_time
                }
            )

    return {
        username: messages
        for username, messages in chatters.items()
        if messages
    }


# =========================================================
# ALL CHAT MESSAGES
# =========================================================

def build_all_messages(chatters):

    all_messages = []

    for username, messages in chatters.items():

        for item in messages:

            all_messages.append(
                {
                    "username": username,
                    "display_name":
                        display_name_for(username),
                    "message":
                        item["message"],
                    "date":
                        item["date"],
                    "time":
                        item["time"]
                }
            )

    def sort_key(item):

        date = parse_date(
            item["date"]
        )

        try:

            time_value = datetime.strptime(
                item["time"],
                "%H:%M"
            ).time()

        except ValueError:

            time_value = datetime.min.time()

        return (
            date if date else datetime.min.date(),
            time_value
        )

    all_messages.sort(
        key=sort_key
    )

    return all_messages


# =========================================================
# ACTIVE PERIODS
# =========================================================

def build_active_periods(chatters):

    periods = {}

    for username, messages in chatters.items():

        dates = []

        for item in messages:

            parsed = parse_date(
                item["date"]
            )

            if parsed:

                dates.append(
                    parsed
                )

        if not dates:
            continue

        periods[username] = {
            "first": min(dates),
            "last": max(dates)
        }

        print(
            f"{display_name_for(username)}: "
            f"{min(dates)} -> {max(dates)}",
            flush=True
        )

    return periods


# =========================================================
# ACTIVE CHATTERS ON QUOTE DATE
# =========================================================

def get_active_chatters(
    quote_date,
    chatters,
    active_periods
):

    parsed_quote_date = parse_date(
        quote_date
    )

    if parsed_quote_date is None:
        return []

    eligible = []

    for username in chatters.keys():

        period = active_periods.get(
            username
        )

        if not period:
            continue

        if (
            period["first"]
            <= parsed_quote_date
            <= period["last"]
        ):

            eligible.append(
                username
            )

    return eligible


# =========================================================
# DISPLAY NAME
# =========================================================

def display_name_for(username):

    for display_name, exact_username in CHATTERS.items():

        if (
            exact_username.lower()
            == username.lower()
        ):

            return display_name

    return username


# =========================================================
# FIND CONTEXT
# =========================================================

def find_context(
    all_messages,
    quote
):

    try:

        quote_index = all_messages.index(
            quote
        )

    except ValueError:

        return [], [], []

    before_start = max(
        0,
        quote_index - CONTEXT_BEFORE
    )

    before = all_messages[
        before_start:
        quote_index
    ]

    after_end = min(
        len(all_messages),
        quote_index + 1 + CONTEXT_AFTER
    )

    after = all_messages[
        quote_index + 1:
        after_end
    ]

    return before, [quote], after


# =========================================================
# FORMAT CONTEXT MESSAGE
# =========================================================

def format_context_line(item):

    return (
        f"`{item['time']}` "
        f"**{item['display_name']}:** "
        f"{item['message']}"
    )


# =========================================================
# LEADERBOARD
# =========================================================

def make_leaderboard():

    if not scores:

        return (
            "🏆 **Guess the Chatter — Leaderboard**\n\n"
            "No points yet!"
        )

    ordered = sorted(
        scores.items(),
        key=lambda item:
            item[1]["points"],
        reverse=True
    )

    lines = [
        "🏆 **Guess the Chatter — Leaderboard**",
        ""
    ]

    for rank, (_, player) in enumerate(
        ordered,
        start=1
    ):

        name = player["name"]
        points = player["points"]

        if rank == 1:
            prefix = "🥇"
        elif rank == 2:
            prefix = "🥈"
        elif rank == 3:
            prefix = "🥉"
        else:
            prefix = f"**{rank}.**"

        word = (
            "point"
            if points == 1
            else "points"
        )

        lines.append(
            f"{prefix} {name} — "
            f"**{points} {word}**"
        )

    return "\n".join(lines)


async def post_leaderboard(channel):

    await channel.send(
        make_leaderboard()
    )

    print(
        "Leaderboard posted.",
        flush=True
    )


# =========================================================
# ADD POINT
# =========================================================

async def add_point(user):

    user_id = str(
        user.id
    )

    display_name = (
        user.display_name
    )

    async with scores_lock:

        if user_id not in scores:

            scores[user_id] = {
                "name": display_name,
                "points": 0
            }

        scores[user_id]["name"] = (
            display_name
        )

        scores[user_id]["points"] += 1

        print(
            f"+1 point: {display_name} "
            f"= {scores[user_id]['points']}",
            flush=True
        )

        await save_scores_permanently()


# =========================================================
# POST GUESS
# =========================================================

async def post_guess(
    channel,
    chatters,
    active_periods,
    all_messages
):

    possible_quotes = []

    for username, messages in chatters.items():

        for item in messages:

            message = item["message"]
            date = item["date"]

            eligible = get_active_chatters(
                date,
                chatters,
                active_periods
            )

            if len(eligible) >= POLL_OPTIONS:

                quote = {
                    "username": username,
                    "display_name":
                        display_name_for(username),
                    "message": message,
                    "date": date,
                    "time": item["time"]
                }

                possible_quotes.append(
                    (
                        quote,
                        eligible
                    )
                )

    if not possible_quotes:

        await channel.send(
            "⚠️ Not enough time-period-matched "
            "chatters for a 5-option poll."
        )

        return

    quote, eligible = random.choice(
        possible_quotes
    )

    username = quote["username"]
    message = quote["message"]
    date = quote["date"]
    time_value = quote["time"]

    correct_display_name = (
        display_name_for(username)
    )

    wrong_usernames = [
        name
        for name in eligible
        if name != username
    ]

    wrong_usernames = random.sample(
        wrong_usernames,
        POLL_OPTIONS - 1
    )

    options = (
        wrong_usernames
        + [username]
    )

    random.shuffle(options)

    # =====================================================
    # POLL
    # =====================================================

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
        f"💬 **Guess the Chatter**\n\n"
        f"> {message}\n\n"
        f"📅 **Date:** {date}\n"
        f"🕒 **Time:** {time_value}"
    )

    poll_message = await channel.send(
        content=message_content,
        poll=poll
    )

    correct_answer_id = None

    for answer in poll.answers:

        if answer.text == correct_display_name:

            correct_answer_id = (
                answer.id
            )

            break

    active_polls[
        poll_message.id
    ] = {
        "correct_answer_id":
            correct_answer_id,

        "correct_display_name":
            correct_display_name,

        "votes": {},

        "quote":
            quote
    }

    print(
        f"Poll created: {poll_message.id} | "
        f"quote date={date} | "
        f"quote time={time_value} | "
        f"correct={correct_display_name}",
        flush=True
    )

    # =====================================================
    # WAIT 3 MINUTES
    # =====================================================

    await asyncio.sleep(
        ANSWER_DELAY_SECONDS
    )

    try:

        await poll_message.end_poll()

    except discord.HTTPException:

        pass

    # =====================================================
    # AWARD POINTS
    # =====================================================

    poll_data = active_polls.get(
        poll_message.id
    )

    if poll_data:

        correct_answer_id = (
            poll_data[
                "correct_answer_id"
            ]
        )

        votes = poll_data[
            "votes"
        ]

        for user_id, answer_id in (
            votes.items()
        ):

            if answer_id != correct_answer_id:
                continue

            try:

                user = client.get_user(
                    int(user_id)
                )

                if user is None:

                    user = await client.fetch_user(
                        int(user_id)
                    )

                await add_point(
                    user
                )

            except Exception as error:

                print(
                    f"Could not award point "
                    f"to {user_id}: {error}",
                    flush=True
                )

        # =================================================
        # CONTEXT
        # =================================================

        quote_for_context = {
            "username":
                quote["username"],
            "display_name":
                quote["display_name"],
            "message":
                quote["message"],
            "date":
                quote["date"],
            "time":
                quote["time"]
        }

        before, _, after = find_context(
            all_messages,
            quote_for_context
        )

        context_lines = []

        for item in before:

            context_lines.append(
                format_context_line(item)
            )

        # The actual quote
        context_lines.append(
            format_context_line(
                quote_for_context
            )
        )

        for item in after:

            context_lines.append(
                format_context_line(item)
            )

        # =================================================
        # ANSWER MESSAGE
        # =================================================

        answer_text = (
            f"🔓 **The answer was:** "
            f"||{correct_display_name}||"
        )

        if context_lines:

            answer_text += (
                "\n\n"
                "💬 **Context:**\n"
                + "\n".join(
                    context_lines
                )
            )

        await channel.send(
            answer_text
        )

        del active_polls[
            poll_message.id
        ]


# =========================================================
# POLL VOTES
# =========================================================

@client.event
async def on_raw_poll_vote_add(
    payload
):

    poll_data = active_polls.get(
        payload.message_id
    )

    if poll_data is None:
        return

    user_id = str(
        payload.user_id
    )

    # Store latest vote
    poll_data[
        "votes"
    ][user_id] = payload.answer_id

    print(
        f"Vote added: user={user_id} "
        f"answer={payload.answer_id}",
        flush=True
    )


@client.event
async def on_raw_poll_vote_remove(
    payload
):

    poll_data = active_polls.get(
        payload.message_id
    )

    if poll_data is None:
        return

    user_id = str(
        payload.user_id
    )

    current_answer = (
        poll_data[
            "votes"
        ].get(user_id)
    )

    if current_answer == payload.answer_id:

        del poll_data[
            "votes"
        ][user_id]

    print(
        f"Vote removed: user={user_id}",
        flush=True
    )


# =========================================================
# MAIN
# =========================================================

@client.event
async def on_ready():

    if getattr(
        client,
        "started",
        False
    ):
        return

    client.started = True

    global scores

    scores = load_scores()

    try:

        channel = await client.fetch_channel(
            CHANNEL_ID
        )

    except Exception as error:

        print(
            f"Could not find channel: {error}",
            flush=True
        )

        return

    chatters = load_chatters()

    if not chatters:

        await channel.send(
            "No valid chatters found."
        )

        await client.close()

        return

    # Build exact active periods
    active_periods = build_active_periods(
        chatters
    )

    # Build chronological list of every message
    all_messages = build_all_messages(
        chatters
    )

    print(
        f"Loaded {len(chatters)} chatters.",
        flush=True
    )

    print(
        f"Loaded {len(all_messages)} messages.",
        flush=True
    )

    print(
        "Guess the Chatter is running.",
        flush=True
    )

    # =====================================================
    # LEADERBOARD LOOP
    # =====================================================

    async def leaderboard_loop():

        while True:

            await asyncio.sleep(
                LEADERBOARD_INTERVAL_SECONDS
            )

            try:

                await post_leaderboard(
                    channel
                )

            except Exception as error:

                print(
                    f"Leaderboard error: {error}",
                    flush=True
                )

    asyncio.create_task(
        leaderboard_loop()
    )

    # =====================================================
    # QUOTE LOOP
    # =====================================================

    while True:

        start_time = (
            asyncio.get_running_loop()
            .time()
        )

        try:

            await post_guess(
                channel,
                chatters,
                active_periods,
                all_messages
            )

        except Exception as error:

            print(
                f"Guess the Chatter error: {error}",
                flush=True
            )

        elapsed = (
            asyncio.get_running_loop()
            .time()
            - start_time
        )

        remaining = (
            QUOTE_INTERVAL_SECONDS
            - elapsed
        )

        if remaining > 0:

            await asyncio.sleep(
                remaining
            )


# =========================================================
# START
# =========================================================

client.run(TOKEN)
