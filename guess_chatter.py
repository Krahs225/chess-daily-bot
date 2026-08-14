import discord
import os
import random
import re
import asyncio
import json
import subprocess
from datetime import timedelta, datetime, date
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

# Context
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

last_leaderboard_order = []
last_quote_of_day = None


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

        for user_id, player in data.items():

            if "points" not in player:
                player["points"] = 0

            if "correct" not in player:
                player["correct"] = 0

            if "attempts" not in player:
                player["attempts"] = 0

            if "streak" not in player:
                player["streak"] = 0

            if "best_streak" not in player:
                player["best_streak"] = 0

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
# CHAT PARSER
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
                r"^(\d{1,2}:\d{2})\s*(.*)$",
                line
            )

            if not time_match:
                continue

            message_time = time_match.group(1)
            rest = time_match.group(2)

            colon_index = rest.find(":")

            if colon_index == -1:
                continue

            prefix = rest[:colon_index]

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
# ALL MESSAGES
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

        parsed_date = parse_date(
            item["date"]
        )

        try:

            parsed_time = datetime.strptime(
                item["time"],
                "%H:%M"
            ).time()

        except ValueError:

            parsed_time = datetime.min.time()

        return (
            parsed_date
            if parsed_date
            else date.min,
            parsed_time
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
                dates.append(parsed)

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
# ACTIVE CHATTERS ON DATE
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

            eligible.append(username)

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
# CONTEXT
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

    before = all_messages[
        max(
            0,
            quote_index - 2
        ):
        quote_index
    ]

    after = all_messages[
        quote_index + 1:
        quote_index + 3
    ]

    return before, [quote], after


def format_context_line(item):

    return (
        f"`{item['time']}` "
        f"**{item['display_name']}:** "
        f"{item['message']}"
    )


# =========================================================
# TIME MACHINE
# =========================================================

def days_ago(date_string):

    parsed = parse_date(
        date_string
    )

    if not parsed:
        return None

    return (
        date.today() - parsed
    ).days


def time_machine_text(date_string):

    days = days_ago(
        date_string
    )

    if days is None:
        return ""

    if days == 0:

        return (
            "🕰️ **This quote was today.**"
        )

    if days == 1:

        return (
            "🕰️ **This quote was 1 day ago.**"
        )

    return (
        f"🕰️ **This quote was "
        f"{days:,} days ago.**"
    )


# =========================================================
# LEADERBOARD
# =========================================================

def get_ordered_scores():

    return sorted(
        scores.items(),
        key=lambda item: (
            item[1].get("points", 0),
            item[1].get("best_streak", 0)
        ),
        reverse=True
    )


def make_leaderboard():

    if not scores:

        return (
            "🏆 **Guess the Chatter — Leaderboard**\n\n"
            "No points yet!"
        )

    ordered = get_ordered_scores()

    lines = [
        "🏆 **Guess the Chatter — Leaderboard**",
        ""
    ]

    for rank, (_, player) in enumerate(
        ordered,
        start=1
    ):

        name = player.get(
            "name",
            "Unknown"
        )

        points = player.get(
            "points",
            0
        )

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


async def post_leaderboard(
    channel
):

    global last_leaderboard_order

    ordered = get_ordered_scores()

    new_order = [
        user_id
        for user_id, _ in ordered
    ]

    if (
        last_leaderboard_order
        and new_order
        and new_order[0]
        != last_leaderboard_order[0]
    ):

        new_leader = ordered[0][1].get(
            "name",
            "Unknown"
        )

        await channel.send(
            f"👑 **NEW #1!** "
            f"{new_leader} has taken "
            f"the lead!"
        )

    last_leaderboard_order = new_order

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
                "points": 0,
                "correct": 0,
                "attempts": 0,
                "streak": 0,
                "best_streak": 0
            }

        player = scores[
            user_id
        ]

        player["name"] = display_name

        player["points"] = (
            player.get("points", 0)
            + 1
        )

        player["correct"] = (
            player.get("correct", 0)
            + 1
        )

        player["attempts"] = (
            player.get("attempts", 0)
            + 1
        )

        player["streak"] = (
            player.get("streak", 0)
            + 1
        )

        player["best_streak"] = max(
            player.get("best_streak", 0),
            player["streak"]
        )

        await save_scores_permanently()

        return {
            "points": player["points"],
            "streak": player["streak"],
            "best_streak":
                player["best_streak"]
        }


# =========================================================
# RECORD WRONG ANSWER
# =========================================================

async def record_wrong_answer(
    user
):

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
                "points": 0,
                "correct": 0,
                "attempts": 0,
                "streak": 0,
                "best_streak": 0
            }

        player = scores[
            user_id
        ]

        player["name"] = display_name

        player["attempts"] = (
            player.get("attempts", 0)
            + 1
        )

        player["streak"] = 0

        await save_scores_permanently()


# =========================================================
# STATS
# =========================================================

async def send_stats(
    message
):

    user_id = str(
        message.author.id
    )

    player = scores.get(
        user_id
    )

    if not player:

        await message.channel.send(
            f"📊 **{message.author.display_name}**\n\n"
            "You haven't scored any points yet!"
        )

        return

    points = player.get(
        "points",
        0
    )

    correct = player.get(
        "correct",
        0
    )

    attempts = player.get(
        "attempts",
        0
    )

    streak = player.get(
        "streak",
        0
    )

    best_streak = player.get(
        "best_streak",
        0
    )

    accuracy = (
        (correct / attempts) * 100
        if attempts > 0
        else 0
    )

    await message.channel.send(
        f"📊 **{message.author.display_name}**\n\n"
        f"🏆 Points: **{points}**\n"
        f"✅ Correct: **{correct}**\n"
        f"🎯 Accuracy: **{accuracy:.0f}%**\n"
        f"🔥 Current streak: **{streak}**\n"
        f"🏅 Best streak: **{best_streak}**"
    )


# =========================================================
# QUOTE OF THE DAY
# =========================================================

async def post_quote_of_day(
    channel,
    all_messages
):

    if not all_messages:
        return

    quote = random.choice(
        all_messages
    )

    await channel.send(
        "🌟 **Quote of the Day**\n\n"
        f"> {quote['message']}\n\n"
        f"— **{quote['display_name']}**, "
        f"{quote['date']} at "
        f"{quote['time']}\n\n"
        f"{time_machine_text(quote['date'])}"
    )

    print(
        "Quote of the Day posted.",
        flush=True
    )


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
            quote_date = item["date"]

            eligible = get_active_chatters(
                quote_date,
                chatters,
                active_periods
            )

            if len(eligible) < POLL_OPTIONS:
                continue

            quote = {
                "username":
                    username,

                "display_name":
                    display_name_for(username),

                "message":
                    message,

                "date":
                    quote_date,

                "time":
                    item["time"]
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
    quote_date = quote["date"]
    quote_time = quote["time"]

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
            text=display_name_for(option)
        )

    message_content = (
        f"💬 **Guess the Chatter**\n\n"
        f"> {message}\n\n"
        f"📅 **Date:** {quote_date}\n"
        f"🕒 **Time:** {quote_time}"
    )

    poll_message = await channel.send(
        content=message_content,
        poll=poll
    )

    correct_answer_id = None

    for answer in poll.answers:

        if (
            answer.text
            == correct_display_name
        ):

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
        f"Poll created: "
        f"{poll_message.id} | "
        f"{quote_date} {quote_time} | "
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
    # PROCESS VOTES
    # =====================================================

    poll_data = active_polls.get(
        poll_message.id
    )

    if not poll_data:
        return

    correct_answer_id = (
        poll_data[
            "correct_answer_id"
        ]
    )

    votes = poll_data[
        "votes"
    ]

    correct_count = 0
    total_count = len(votes)

    for user_id, answer_id in (
        votes.items()
    ):

        try:

            user = client.get_user(
                int(user_id)
            )

            if user is None:

                user = await client.fetch_user(
                    int(user_id)
                )

        except Exception:

            continue

        if (
            answer_id
            == correct_answer_id
        ):

            correct_count += 1

            stats = await add_point(
                user
            )

            if stats["streak"] >= 3:

                await channel.send(
                    f"🔥 **{user.display_name} "
                    f"is on a {stats['streak']}"
                    f"-streak!**"
                )

        else:

            await record_wrong_answer(
                user
            )

    # =====================================================
    # PERCENTAGE / CLOSE CALL
    # =====================================================

    percentage = (
        round(
            (
                correct_count
                / total_count
            ) * 100
        )
        if total_count > 0
        else 0
    )

    close_call = ""

    if (
        total_count >= 2
        and correct_count > 0
        and correct_count < total_count
        and percentage <= 60
    ):

        close_call = (
            "\n🔥 **Close call!**"
        )

    if (
        total_count >= 3
        and correct_count == 1
    ):

        close_call = (
            "\n💀 **Nobody saw that coming.** "
            "Only 1 person got it right."
        )

    # =====================================================
    # CONTEXT
    # =====================================================

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

    context_lines.append(
        format_context_line(
            quote_for_context
        )
    )

    for item in after:

        context_lines.append(
            format_context_line(item)
        )

    # =====================================================
    # ANSWER
    # =====================================================

    # NO SPOILER TAGS:
    # The answer is immediately visible.

    answer_text = (
        f"🔓 **The answer was: "
        f"{correct_display_name}**\n\n"
        f"📊 **{correct_count}/{total_count}** "
        f"people got it right "
        f"(**{percentage}%**)."
        f"{close_call}\n\n"
        f"{time_machine_text(quote_date)}\n\n"
        f"💬 **Context:**\n"
        + "\n".join(context_lines)
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

    if (
        current_answer
        == payload.answer_id
    ):

        del poll_data[
            "votes"
        ][user_id]

    print(
        f"Vote removed: user={user_id}",
        flush=True
    )


# =========================================================
# MESSAGE COMMANDS
# =========================================================

@client.event
async def on_message(
    message
):

    if message.author.bot:
        return

    if message.channel.id != CHANNEL_ID:
        return

    content = message.content.strip().lower()

    if content == "!stats":

        await send_stats(
            message
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

    active_periods = build_active_periods(
        chatters
    )

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
    # QUOTE OF DAY
    # =====================================================

    await post_quote_of_day(
        channel,
        all_messages
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
