import discord
import os
import random
import re
import asyncio
import json
import subprocess
from datetime import timedelta
from pathlib import Path


# =========================================================
# SETTINGS
# =========================================================

TOKEN = os.getenv("DISCORD_TOKEN")
CHANNEL_ID = 1536769340970373241

MIN_CHARACTERS = 20
CHAT_DIR = "SOLO chats"

POLL_OPTIONS = 5

QUOTE_INTERVAL_SECONDS = 5 * 60
ANSWER_DELAY_SECONDS = 3 * 60
LEADERBOARD_INTERVAL_SECONDS = 10 * 60

LEADERBOARD_FILE = "leaderboard.json"


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

# Needed for poll vote events.
intents.message_content = True
intents.guild_polls = True

client = discord.Client(intents=intents)


# =========================================================
# LEADERBOARD
# =========================================================

scores = {}


def load_scores():
    global scores

    path = Path(LEADERBOARD_FILE)

    if not path.exists():
        scores = {}
        print("No leaderboard.json found. Starting with 0 points.", flush=True)
        return

    try:
        with open(path, "r", encoding="utf-8") as file:
            scores = json.load(file)

        print(
            f"Loaded leaderboard with {len(scores)} players.",
            flush=True
        )

    except Exception as error:
        print(
            f"Could not load leaderboard: {error}",
            flush=True
        )
        scores = {}


def save_scores():
    try:
        with open(
            LEADERBOARD_FILE,
            "w",
            encoding="utf-8"
        ) as file:

            json.dump(
                scores,
                file,
                indent=2,
                ensure_ascii=False
            )

    except Exception as error:
        print(
            f"Could not save leaderboard.json: {error}",
            flush=True
        )


def save_scores_to_github():
    """
    Save leaderboard.json back into the repository.

    This runs outside the Discord event loop.
    """

    try:
        subprocess.run(
            [
                "git",
                "config",
                "user.name",
                "Guess the Chatter Bot"
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
                LEADERBOARD_FILE
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
            # Nothing changed.
            return

        # Push to the branch that triggered this workflow.
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
            "Leaderboard saved to GitHub.",
            flush=True
        )

    except Exception as error:
        print(
            f"Could not push leaderboard to GitHub: {error}",
            flush=True
        )


def add_point(user_id, display_name):
    user_id = str(user_id)

    if user_id not in scores:
        scores[user_id] = {
            "name": display_name,
            "points": 0
        }

    scores[user_id]["name"] = display_name
    scores[user_id]["points"] += 1

    print(
        f"+1 point -> {display_name}",
        flush=True
    )


def make_leaderboard():
    if not scores:
        return (
            "🏆 **Guess the Chatter — Leaderboard**\n\n"
            "No points yet!"
        )

    sorted_scores = sorted(
        scores.items(),
        key=lambda item: item[1]["points"],
        reverse=True
    )

    lines = [
        "🏆 **Guess the Chatter — Leaderboard**",
        ""
    ]

    for rank, (_, data) in enumerate(
        sorted_scores,
        start=1
    ):

        name = data["name"]
        points = data["points"]

        if rank == 1:
            prefix = "🥇"
        elif rank == 2:
            prefix = "🥈"
        elif rank == 3:
            prefix = "🥉"
        else:
            prefix = f"**{rank}.**"

        point_word = (
            "point"
            if points == 1
            else "points"
        )

        lines.append(
            f"{prefix} {name} — **{points} {point_word}**"
        )

    return "\n".join(lines)


# =========================================================
# CHAT HISTORY
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


def load_chatters():

    chatters = {
        username: []
        for username in CHATTERS.values()
    }

    chat_path = Path(CHAT_DIR)

    if not chat_path.exists():
        print(
            f"ERROR: {CHAT_DIR} folder not found.",
            flush=True
        )
        return {}

    files = list(
        chat_path.glob("*.txt")
    )

    print(
        f"Found {len(files)} chat history files.",
        flush=True
    )

    for chat_file in files:

        try:

            with open(
                chat_file,
                "r",
                encoding="utf-8"
            ) as file:

                lines = file.readlines()

        except Exception as error:

            print(
                f"Could not read {chat_file}: {error}",
                flush=True
            )

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

                current_date = (
                    f"{day.zfill(2)}-"
                    f"{month.zfill(2)}-"
                    f"{year}"
                )

                continue

            # Message line
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

            message = (
                rest[colon_index + 1:]
                .strip()
            )

            if len(message) < MIN_CHARACTERS:
                continue

            if current_date is None:
                continue

            chatter = find_chatter(prefix)

            if not chatter:
                continue

            _, username = chatter

            chatters[username].append(
                (
                    message,
                    current_date
                )
            )

    valid = {
        username: messages
        for username, messages in chatters.items()
        if messages
    }

    total_messages = sum(
        len(messages)
        for messages in valid.values()
    )

    print(
        f"Loaded {total_messages} valid 20+ character messages "
        f"from {len(valid)} chatters.",
        flush=True
    )

    return valid


def display_name_for(username):

    for display_name, exact_username in CHATTERS.items():

        if exact_username.lower() == username.lower():
            return display_name

    return username


# =========================================================
# ACTIVE POLLS
# =========================================================

# Example:
#
# active_polls[message_id] = {
#     "correct_answer_id": 3,
#     "votes": {
#         "123456789": 3,
#         "987654321": 1
#     }
# }

active_polls = {}


# =========================================================
# RAW POLL VOTE EVENTS
# =========================================================

@client.event
async def on_raw_poll_vote_add(payload):

    message_id = payload.message_id

    if message_id not in active_polls:
        return

    active_polls[
        message_id
    ]["votes"][
        str(payload.user_id)
    ] = payload.answer_id

    print(
        f"Vote received: user={payload.user_id}, "
        f"message={message_id}, "
        f"answer={payload.answer_id}",
        flush=True
    )


@client.event
async def on_raw_poll_vote_remove(payload):

    message_id = payload.message_id

    if message_id not in active_polls:
        return

    user_id = str(payload.user_id)

    current_vote = active_polls[
        message_id
    ]["votes"].get(user_id)

    if current_vote == payload.answer_id:

        del active_polls[
            message_id
        ]["votes"][
            user_id
        ]

    print(
        f"Vote removed: user={payload.user_id}, "
        f"message={message_id}, "
        f"answer={payload.answer_id}",
        flush=True
    )


# =========================================================
# POST ONE GUESS
# =========================================================

async def post_guess(
    channel,
    chatters
):

    if len(chatters) < POLL_OPTIONS:

        await channel.send(
            "Not enough valid chatters "
            "for a 5-option poll."
        )

        return

    # Pick chatter
    username = random.choice(
        list(chatters.keys())
    )

    # Pick message
    message, date = random.choice(
        chatters[username]
    )

    correct_display_name = (
        display_name_for(username)
    )

    # Pick wrong answers
    wrong_usernames = [
        name
        for name in chatters.keys()
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

    # Create poll
    poll = discord.Poll(
        question="Who said this?",
        duration=timedelta(hours=1),
        multiple=False
    )

    correct_answer_id = None

    for option in options:

        answer = poll.add_answer(
            text=display_name_for(option)
        )

        if option == username:
            correct_answer_id = answer.id

    content = (
        f"💬 **Guess the Chatter**\n\n"
        f"> {message}\n\n"
        f"📅 **Date:** {date}"
    )

    print(
        f"Posting quote from {correct_display_name}...",
        flush=True
    )

    poll_message = await channel.send(
        content=content,
        poll=poll
    )

    print(
        f"Poll posted successfully. "
        f"Message ID: {poll_message.id}",
        flush=True
    )

    # Register active poll
    active_polls[
        poll_message.id
    ] = {
        "correct_answer_id": correct_answer_id,
        "votes": {}
    }

    # Wait 3 minutes
    await asyncio.sleep(
        ANSWER_DELAY_SECONDS
    )

    poll_data = active_polls.get(
        poll_message.id
    )

    if poll_data:

        correct_id = (
            poll_data[
                "correct_answer_id"
            ]
        )

        votes = dict(
            poll_data["votes"]
        )

        print(
            f"Poll finished with "
            f"{len(votes)} recorded voters.",
            flush=True
        )

        # Award points
        for user_id, answer_id in votes.items():

            if answer_id != correct_id:
                continue

            try:

                user = await client.fetch_user(
                    int(user_id)
                )

                add_point(
                    user.id,
                    user.display_name
                )

            except Exception as error:

                print(
                    f"Could not fetch user "
                    f"{user_id}: {error}",
                    flush=True
                )

        save_scores()

    # Close poll
    try:

        await poll_message.end_poll()

        print(
            "Poll closed.",
            flush=True
        )

    except discord.HTTPException as error:

        print(
            f"Could not close poll: {error}",
            flush=True
        )

    # Reveal answer
    await channel.send(
        f"🔓 **The answer was:** "
        f"||{correct_display_name}||"
    )

    # Remove from active polls
    active_polls.pop(
        poll_message.id,
        None
    )


# =========================================================
# LEADERBOARD LOOP
# =========================================================

async def leaderboard_loop(channel):

    while True:

        await asyncio.sleep(
            LEADERBOARD_INTERVAL_SECONDS
        )

        print(
            "Posting leaderboard...",
            flush=True
        )

        save_scores()

        # Push scores to GitHub
        await asyncio.to_thread(
            save_scores_to_github
        )

        # New leaderboard message
        await channel.send(
            make_leaderboard()
        )


# =========================================================
# MAIN BOT LOOP
# =========================================================

async def bot_loop(channel):

    chatters = load_chatters()

    if not chatters:

        await channel.send(
            "❌ No valid chatters found."
        )

        return

    while True:

        start_time = (
            asyncio.get_running_loop().time()
        )

        try:

            await post_guess(
                channel,
                chatters
            )

        except Exception as error:

            print(
                f"ERROR during guess: {error}",
                flush=True
            )

            try:

                await channel.send(
                    "⚠️ Guess the Chatter "
                    "encountered an error. "
                    "Check the GitHub Actions log."
                )

            except Exception:
                pass

        elapsed = (
            asyncio.get_running_loop().time()
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
# CONNECTION EVENTS
# =========================================================

@client.event
async def on_connect():

    print(
        "Discord Gateway connected.",
        flush=True
    )


@client.event
async def on_ready():

    # Prevent duplicate loops after reconnects
    if getattr(
        client,
        "started",
        False
    ):
        return

    client.started = True

    print(
        f"READY! Logged in as {client.user}",
        flush=True
    )

    print(
        "Finding Discord channel...",
        flush=True
    )

    # Try cached channel first
    channel = client.get_channel(
        CHANNEL_ID
    )

    # If not cached, fetch it
    if channel is None:

        print(
            "Channel not cached. Fetching...",
            flush=True
        )

        try:

            channel = await asyncio.wait_for(
                client.fetch_channel(
                    CHANNEL_ID
                ),
                timeout=15
            )

        except Exception as error:

            print(
                f"Could not fetch channel: {error}",
                flush=True
            )

            await client.close()

            return

    print(
        f"Channel found: {channel}",
        flush=True
    )

    load_scores()

    # Start leaderboard
    asyncio.create_task(
        leaderboard_loop(channel)
    )

    # Start main game loop
    asyncio.create_task(
        bot_loop(channel)
    )

    print(
        "Guess the Chatter is now running!",
        flush=True
    )


# =========================================================
# START
# =========================================================

print(
    "Starting Guess the Chatter...",
    flush=True
)

print(
    f"discord.py version: {discord.__version__}",
    flush=True
)

client.run(TOKEN)
