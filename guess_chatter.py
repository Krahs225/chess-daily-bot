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

NORMAL_POLL_OPTIONS = 5
HARD_POLL_OPTIONS = 6

# One quote every 5 minutes
QUOTE_INTERVAL_SECONDS = 5 * 60

# Reveal answer after 3 minutes
ANSWER_DELAY_SECONDS = 3 * 60

# Leaderboard every 10 minutes
LEADERBOARD_INTERVAL_SECONDS = 10 * 60

# Permanent leaderboard
SCORES_FILE = "guess_chatter_scores.json"

# Weekly statistics
WEEKLY_FILE = "guess_chatter_weekly.json"

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
# CHATTER DESCRIPTIONS
# =========================================================

CHATTER_DESCRIPTIONS = {

    "AZ":
        "Competitive and pretty opinionated. "
        "Usually writes short, confident messages "
        "and likes arguing the point.",

    "Ben":
        "Mostly appears for GeoGuessr. "
        "Writes quickly and casually, usually keeping "
        "messages short.",

    "Cash":
        "Very direct and doesn't really sugarcoat things. "
        "Messages are usually short and straight to the point.",

    "Cien":
        "More laid-back and conversational. "
        "Tends to write naturally rather than in a very "
        "distinctive format.",

    "Geeflux":
        "Direct, blunt and occasionally a little savage. "
        "Usually writes short messages without much filtering.",

    "George":
        "Small in height, but not in personality. "
        "Writes casually and often joins in with jokes "
        "and reactions.",

    "Grumpymonk":
        "The Swedish chatter. Usually relaxed and "
        "conversational, with a fairly straightforward "
        "writing style.",

    "Jessebrawlstars":
        "The Dutch chatter. Writes casually and tends "
        "to keep things simple and direct.",

    "Kurupt":
        "Plays all sorts of games, especially Poker and CS2. "
        "Messages often revolve around whatever game is happening.",

    "Martin":
        "Completely random. Messages can jump from one "
        "topic to another with absolutely no warning.",

    "Melvin":
        "Usually relaxed and conversational. Messages feel "
        "natural and spontaneous rather than overly structured.",

    "MH":
        "Into some seriously questionable anime. Writes casually "
        "and often mixes normal conversation with random anime references.",

    "Mohammad":
        "Has a fairly calm conversational style. Often reacts "
        "to what's happening rather than writing huge messages.",

    "Mr_thice":
        "Also known as Mr_thick. Writes casually, uses things "
        "like xD, and often reacts to whatever is happening in the moment.",

    "Nairyaaa":
        "Somehow manages to turn almost everything into a question. "
        "The writing style is very curious and question-heavy.",

    "Pabu":
        "Has been to Peru. Usually writes casually and reacts "
        "naturally to the conversation.",

    "Pandarou":
        "The German chatter. Has a fairly relaxed, "
        "conversational writing style.",

    "Pindametdemensie":
        "Dutch and pretty straightforward. Usually keeps "
        "messages casual and easy to read.",

    "Pospos":
        "Mostly appears for GeoGuessr. Tends to write "
        "short, game-focused messages.",

    "Rubriek":
        "Another GeoGuessr regular. Usually keeps messages "
        "concise and focused on what's happening in-game.",

    "Sativahibread":
        "More of a casual conversational chatter. Messages "
        "tend to blend into the ongoing conversation naturally.",

    "Screamingcat":
        "Loves Australia. Usually writes casually, with "
        "occasional enthusiastic reactions.",

    "Sh4rkmate is the best":
        "The name is already a personality trait. "
        "Writes with maximum confidence in the self.",

    "Soyadelson":
        "Very energetic and chaotic. Uses jokes, teasing "
        "and exaggerated messages a lot.",

    "Stefan":
        "Usually relaxed and conversational. Tends to "
        "respond naturally to whatever is happening.",

    "Stepu":
        "Casual and unpredictable. Often jumps into "
        "conversations without needing much setup.",

    "Sushi":
        "Sarcastic, analytical and occasionally completely "
        "unhinged. Often writes longer thoughts, jokes, irony "
        "and random observations; the logs show a lot of dry "
        "humour and wordplay.",

    "Thejazzdude":
        "The Dutch jazz guy. Relaxed and conversational, "
        "with a fairly casual writing style.",

    "Kohl":
        "Very conversational and curious. Uses lots of short "
        "messages, questions, reactions and sudden topic changes; "
        "also likes joking around and experimenting with wording.",

    "Kingdev":
        "Loves coding. The style is generally technical, "
        "direct and focused when talking about something being worked on.",
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
weekly_data = {}

active_polls = {}

scores_lock = asyncio.Lock()

last_leaderboard_order = []

# Number of rounds in the current run
run_round_number = 0

# Special rounds are selected when the run starts
hard_mode_rounds = set()
double_points_round = None


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
# WEEKLY DATA
# =========================================================

def get_current_week_key():

    today = date.today()
    iso = today.isocalendar()

    return f"{iso.year}-W{iso.week:02d}"


def load_weekly_data():

    current_week = get_current_week_key()

    if not os.path.exists(WEEKLY_FILE):

        return {
            "week": current_week,
            "players": {},
            "last_reported_week": None
        }

    try:

        with open(
            WEEKLY_FILE,
            "r",
            encoding="utf-8"
        ) as file:

            data = json.load(file)

        if data.get("week") != current_week:

            return {
                "week": current_week,
                "players": {},
                "last_reported_week":
                    data.get("last_reported_week")
            }

        return data

    except Exception as error:

        print(
            f"Could not load weekly data: {error}",
            flush=True
        )

        return {
            "week": current_week,
            "players": {},
            "last_reported_week": None
        }


def save_weekly_data():

    with open(
        WEEKLY_FILE,
        "w",
        encoding="utf-8"
    ) as file:

        json.dump(
            weekly_data,
            file,
            indent=2,
            ensure_ascii=False
        )


# =========================================================
# SAVE TO GITHUB
# =========================================================

def push_data_to_github():

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
                SCORES_FILE,
                WEEKLY_FILE
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

    except Exception as error:

        print(
            f"Could not save leaderboard data: {error}",
            flush=True
        )


async def save_all_data():

    save_scores()
    save_weekly_data()

    await asyncio.to_thread(
        push_data_to_github
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
# CHATTER COMMAND NORMALIZATION
# =========================================================

def normalize_command_name(name):

    return re.sub(
        r"[^a-z0-9]",
        "",
        name.lower()
    )


CHATTER_COMMANDS = {
    normalize_command_name(name): name
    for name in CHATTERS.keys()
}


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
            quote_index - CONTEXT_BEFORE
        ):
        quote_index
    ]

    after = all_messages[
        quote_index + 1:
        quote_index + 1 + CONTEXT_AFTER
    ]

    return before, [quote], after


def format_context_line(item):

    return (
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
# ANCIENT QUOTE
# =========================================================

def ancient_quote_text(date_string):

    days = days_ago(
        date_string
    )

    if days is None:
        return ""

    if days >= 730:

        years = days / 365.25

        return (
            f"🏺 **ANCIENT QUOTE** — "
            f"over {years:.1f} years old!"
        )

    return ""


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


# =========================================================
# ADD POINT
# =========================================================

async def add_point(
    user,
    points_to_add=1
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

        player["points"] = (
            player.get("points", 0)
            + points_to_add
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

        # ================================================
        # WEEKLY DATA
        # ================================================

        current_week = get_current_week_key()

        if (
            weekly_data.get("week")
            != current_week
        ):

            weekly_data["week"] = current_week
            weekly_data["players"] = {}

        if user_id not in weekly_data["players"]:

            weekly_data["players"][user_id] = {
                "name": display_name,
                "points": 0,
                "best_streak": 0
            }

        weekly_player = (
            weekly_data["players"][user_id]
        )

        weekly_player["name"] = (
            display_name
        )

        weekly_player["points"] = (
            weekly_player.get("points", 0)
            + points_to_add
        )

        weekly_player["best_streak"] = max(
            weekly_player.get(
                "best_streak",
                0
            ),
            player["streak"]
        )

        # ================================================
        # 5-STREAK BONUS
        # ================================================

        streak_bonus = False

        if player["streak"] == 5:

            player["points"] += 1
            weekly_player["points"] += 1

            streak_bonus = True

        await save_all_data()

        return {
            "points":
                player["points"],

            "streak":
                player["streak"],

            "best_streak":
                player["best_streak"],

            "streak_bonus":
                streak_bonus
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

        await save_all_data()


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
# HELP / INFO
# =========================================================

def make_help_message():

    return (
        "🦈 **Guess the Chatter**\n\n"

        "**How to play**\n"
        "💬 A quote from the chat appears in a poll.\n"
        "🗳️ Choose who you think said it.\n"
        "✅ Correct = **+1 point**\n"
        "❌ Wrong = **0 points** and your streak resets.\n"
        "🔥 Build streaks by getting answers correct.\n"
        "🏆 Points are permanent and count toward "
        "the all-time leaderboard.\n\n"

        "**Special rounds**\n"
        "🧠 Hard Mode can appear during the run.\n"
        "🎰 Double Points Round can appear during the run.\n\n"

        "**Commands**\n"
        "`!help` / `!info` — Show this guide\n"
        "`!stats` — Show your statistics\n"
        "`!name` — Show that chatter's description\n\n"

        "**Example:**\n"
        "`!kohl` → Kohl's personality and writing style"
    )


async def send_help(
    message
):

    await message.channel.send(
        make_help_message()
    )


async def send_chatter_info(
    message,
    command_name
):

    normalized = normalize_command_name(
        command_name
    )

    display_name = CHATTER_COMMANDS.get(
        normalized
    )

    if not display_name:

        await message.channel.send(
            "❌ I don't know that chatter. "
            "Try `!help`."
        )

        return

    description = CHATTER_DESCRIPTIONS.get(
        display_name,
        "No description available yet."
    )

    await message.channel.send(
        f"👤 **{display_name}**\n"
        f"{description}"
    )


# =========================================================
# WEEKLY REPORT
# =========================================================

async def post_weekly_report(
    channel
):

    current_week = get_current_week_key()

    if (
        weekly_data.get(
            "last_reported_week"
        )
        == current_week
    ):

        return

    players = weekly_data.get(
        "players",
        {}
    )

    if not players:

        weekly_data[
            "last_reported_week"
        ] = current_week

        save_weekly_data()

        return

    ordered_points = sorted(
        players.items(),
        key=lambda item:
            item[1].get(
                "points",
                0
            ),
        reverse=True
    )

    ordered_streaks = sorted(
        players.items(),
        key=lambda item:
            item[1].get(
                "best_streak",
                0
            ),
        reverse=True
    )

    lines = [
        "📅 **WEEKLY GUESS THE CHATTER REPORT**",
        "",
        "📈 **Most Improved**"
    ]

    if ordered_points:

        top_user = ordered_points[0][1]

        top_name = top_user.get(
            "name",
            "Unknown"
        )

        top_points = top_user.get(
            "points",
            0
        )

        lines.append(
            f"🏆 **{top_name}** — "
            f"+{top_points} points this week"
        )

    else:

        lines.append(
            "No points this week."
        )

    lines.extend(
        [
            "",
            "🔥 **Top 5 Longest Streaks**"
        ]
    )

    medals = [
        "🥇",
        "🥈",
        "🥉",
        "4️⃣",
        "5️⃣"
    ]

    shown = 0

    for _, player in ordered_streaks:

        streak = player.get(
            "best_streak",
            0
        )

        if streak <= 0:
            continue

        name = player.get(
            "name",
            "Unknown"
        )

        lines.append(
            f"{medals[shown]} "
            f"**{name}** — "
            f"{streak} streak"
        )

        shown += 1

        if shown >= 5:
            break

    if shown == 0:

        lines.append(
            "No streaks yet."
        )

    await channel.send(
        "\n".join(lines)
    )

    weekly_data[
        "last_reported_week"
    ] = current_week

    save_weekly_data()

    await asyncio.to_thread(
        push_data_to_github
    )


# =========================================================
# SPECIAL ROUND SETUP
# =========================================================

def setup_special_rounds():

    global hard_mode_rounds
    global double_points_round

    # There are approximately 72 rounds in a 6-hour run
    # when one round is posted every 5 minutes.

    total_rounds = 72

    all_rounds = list(
        range(
            1,
            total_rounds + 1
        )
    )

    selected = random.sample(
        all_rounds,
        3
    )

    hard_mode_rounds = {
        selected[0],
        selected[1]
    }

    double_points_round = selected[2]

    # If the double-points round accidentally overlaps
    # with hard mode, move it to another round.
    while (
        double_points_round
        in hard_mode_rounds
    ):

        double_points_round = random.choice(
            [
                r
                for r in all_rounds
                if r not in hard_mode_rounds
            ]
        )

    print(
        f"Hard Mode rounds: "
        f"{sorted(hard_mode_rounds)}",
        flush=True
    )

    print(
        f"Double Points round: "
        f"{double_points_round}",
        flush=True
    )


# =========================================================
# POST GUESS
# =========================================================

async def post_guess(
    channel,
    chatters,
    active_periods,
    all_messages,
    is_hard_mode=False,
    is_double_points=False
):

    possible_quotes = []

    # =====================================================
    # HARD MODE
    # =====================================================

    if is_hard_mode:

        for username, messages in chatters.items():

            for item in messages:

                if len(
                    item["message"]
                ) < MIN_CHARACTERS:
                    continue

                possible_quotes.append(
                    {
                        "username":
                            username,

                        "display_name":
                            display_name_for(
                                username
                            ),

                        "message":
                            item["message"],

                        "date":
                            item["date"],

                        "time":
                            item["time"]
                    }
                )

        if not possible_quotes:

            return

        quote = random.choice(
            possible_quotes
        )

        eligible = list(
            chatters.keys()
        )

        random.shuffle(
            eligible
        )

        correct_username = (
            quote["username"]
        )

        eligible = [
            username
            for username in eligible
            if username != correct_username
        ][:HARD_POLL_OPTIONS - 1]

        eligible.append(
            correct_username
        )

        random.shuffle(
            eligible
        )

        possible_chatter_count = (
            len(eligible)
        )

    # =====================================================
    # NORMAL MODE
    # =====================================================

    else:

        for username, messages in chatters.items():

            for item in messages:

                message = item["message"]
                quote_date = item["date"]

                eligible = get_active_chatters(
                    quote_date,
                    chatters,
                    active_periods
                )

                if len(
                    eligible
                ) < NORMAL_POLL_OPTIONS:
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
                "⚠️ Not enough "
                "time-period-matched chatters "
                "for a 5-option poll."
            )

            return

        quote, eligible = random.choice(
            possible_quotes
        )

        correct_username = (
            quote["username"]
        )

        wrong_usernames = [
            name
            for name in eligible
            if name != correct_username
        ]

        wrong_usernames = random.sample(
            wrong_usernames,
            NORMAL_POLL_OPTIONS - 1
        )

        eligible = (
            wrong_usernames
            + [correct_username]
        )

        random.shuffle(
            eligible
        )

        possible_chatter_count = (
            len(
                get_active_chatters(
                    quote["date"],
                    chatters,
                    active_periods
                )
            )
        )

    # =====================================================
    # VARIABLES
    # =====================================================

    correct_display_name = (
        display_name_for(
            correct_username
        )
    )

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

    for option in eligible:

        poll.add_answer(
            text=display_name_for(option)
        )

    # =====================================================
    # ROUND LABEL
    # =====================================================

    if is_hard_mode:

        round_label = (
            "🧠 **HARD MODE — 2 POINTS**\n"
            "⚠️ **NO DATE**\n\n"
        )

    elif is_double_points:

        round_label = (
            "🎰 **DOUBLE POINTS ROUND**\n\n"
        )

    else:

        round_label = ""

    # =====================================================
    # MESSAGE
    # =====================================================

    message_content = (
        f"{round_label}"
        f"💬 **Guess the Chatter**\n\n"
        f"> {quote['message']}"
    )

    if not is_hard_mode:

        message_content += (
            f"\n\n📅 **Date:** "
            f"{quote['date']}"
        )

    poll_message = await channel.send(
        content=message_content,
        poll=poll
    )

    # =====================================================
    # FIND CORRECT ANSWER ID
    # =====================================================

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

        "votes":
            {},

        "quote":
            quote,

        "possible_chatter_count":
            possible_chatter_count,

        "is_hard_mode":
            is_hard_mode,

        "is_double_points":
            is_double_points
    }

    print(
        f"Poll created: "
        f"{poll_message.id} | "
        f"hard={is_hard_mode} | "
        f"double={is_double_points}",
        flush=True
    )

    # =====================================================
    # WAIT
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

            points = 1

            if is_hard_mode:

                points = 2

            elif is_double_points:

                points = 2

            stats = await add_point(
                user,
                points
            )

            if stats["streak"] >= 3:

                await channel.send(
                    f"🔥 **{user.display_name} "
                    f"is on a {stats['streak']}"
                    f"-streak!**"
                )

            if stats["streak_bonus"]:

                await channel.send(
                    f"🔥 **5-STREAK BONUS!** "
                    f"{user.display_name} gets "
                    f"an extra point!"
                )

        else:

            await record_wrong_answer(
                user
            )

    # =====================================================
    # PERCENTAGE
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
    # EXTRA INFO
    # =====================================================

    extra_lines = []

    if not is_hard_mode:

        extra_lines.append(
            f"👥 **{possible_chatter_count} "
            f"possible chatters on this date.**"
        )

        ancient_text = ancient_quote_text(
            quote["date"]
        )

        if ancient_text:

            extra_lines.append(
                ancient_text
            )

        time_text = time_machine_text(
            quote["date"]
        )

        if time_text:

            extra_lines.append(
                time_text
            )

    extra_text = ""

    if extra_lines:

        extra_text = (
            "\n"
            + "\n".join(extra_lines)
            + "\n"
        )

    # =====================================================
    # ANSWER
    # =====================================================

    answer_text = (
        f"🔓 **The answer was: "
        f"{correct_display_name}**\n\n"
        f"📊 **{correct_count}/{total_count}** "
        f"people got it right "
        f"(**{percentage}%**)."
        f"{close_call}"
        f"{extra_text}\n"
        f"💬 **Context:**\n"
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

    poll_data[
        "votes"
    ][user_id] = payload.answer_id


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

    content = message.content.strip()

    if not content.startswith("!"):
        return

    command = content[
        1:
    ].strip()

    if not command:
        return

    command_lower = command.lower()

    # ================================================
    # HELP
    # ================================================

    if command_lower in (
        "help",
        "info"
    ):

        await send_help(
            message
        )

        return

    # ================================================
    # STATS
    # ================================================

    if command_lower == "stats":

        await send_stats(
            message
        )

        return

    # ================================================
    # CHATTER INFO
    # ================================================

    await send_chatter_info(
        message,
        command
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
    global weekly_data

    scores = load_scores()
    weekly_data = load_weekly_data()

    # ================================================
    # SPECIAL ROUNDS
    # ================================================

    setup_special_rounds()

    # ================================================
    # CHANNEL
    # ================================================

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

    # ================================================
    # CHAT DATA
    # ================================================

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

    # ================================================
    # INFO ONCE PER RUN
    # ================================================

    await channel.send(
        make_help_message()
    )

    # ================================================
    # LEADERBOARD LOOP
    # ================================================

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

    # ================================================
    # WEEKLY REPORT LOOP
    # ================================================

    async def weekly_report_loop():

        while True:

            await asyncio.sleep(
                LEADERBOARD_INTERVAL_SECONDS
            )

            try:

                await post_weekly_report(
                    channel
                )

            except Exception as error:

                print(
                    f"Weekly report error: {error}",
                    flush=True
                )

    asyncio.create_task(
        weekly_report_loop()
    )

    # ================================================
    # QUOTE LOOP
    # ================================================

    global run_round_number

    while True:

        run_round_number += 1

        is_hard_mode = (
            run_round_number
            in hard_mode_rounds
        )

        is_double_points = (
            run_round_number
            == double_points_round
        )

        # Hard Mode and Double Points are separate.
        if is_hard_mode:
            is_double_points = False

        start_time = (
            asyncio.get_running_loop()
            .time()
        )

        try:

            await post_guess(
                channel,
                chatters,
                active_periods,
                all_messages,
                is_hard_mode,
                is_double_points
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
