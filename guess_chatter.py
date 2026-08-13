import discord
import os
import random
import re
import asyncio
import json
import subprocess
from datetime import timedelta
from pathlib import Path

TOKEN = os.getenv("DISCORD_TOKEN")
CHANNEL_ID = 1536769340970373241

MIN_CHARACTERS = 20
CHAT_DIR = "SOLO chats"
POLL_OPTIONS = 5

QUOTE_INTERVAL_SECONDS = 5 * 60
ANSWER_DELAY_SECONDS = 3 * 60
LEADERBOARD_INTERVAL_SECONDS = 10 * 60

LEADERBOARD_FILE = "leaderboard.json"


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


# ---------------------------------------------------------
# DISCORD
# ---------------------------------------------------------

intents = discord.Intents.default()

# Needed for poll vote events
intents.message_content = True
intents.polls = True

client = discord.Client(intents=intents)


# ---------------------------------------------------------
# LEADERBOARD DATA
# ---------------------------------------------------------

scores = {}


def load_scores():
    global scores

    path = Path(LEADERBOARD_FILE)

    if not path.exists():
        scores = {}
        return

    try:
        with open(path, "r", encoding="utf-8") as file:
            scores = json.load(file)

    except Exception:
        scores = {}


def save_scores():
    with open(LEADERBOARD_FILE, "w", encoding="utf-8") as file:
        json.dump(
            scores,
            file,
            indent=2,
            ensure_ascii=False
        )


def save_scores_to_github():
    """
    Saves leaderboard.json to the repository.

    GITHUB_TOKEN is provided automatically by GitHub Actions.
    """

    try:
        subprocess.run(
            ["git", "config", "user.name", "Guess the Chatter Bot"],
            check=True
        )

        subprocess.run(
            [
                "git",
                "config",
                "user.email",
                "guess-chatter-bot@users.noreply.github.com"
            ],
            check=True
        )

        subprocess.run(
            ["git", "add", LEADERBOARD_FILE],
            check=True
        )

        result = subprocess.run(
            [
                "git",
                "commit",
                "-m",
                "Update Guess the Chatter leaderboard"
            ],
            capture_output=True,
            text=True
        )

        # Nothing changed
        if result.returncode != 0:
            return

        subprocess.run(
            ["git", "push"],
            check=True
        )

    except Exception as error:
        print(f"Could not save leaderboard to GitHub: {error}")


def add_point(user):
    user_id = str(user.id)

    if user_id not in scores:
        scores[user_id] = {
            "name": user.display_name,
            "points": 0
        }

    # Always update the current Discord display name
    scores[user_id]["name"] = user.display_name

    scores[user_id]["points"] += 1


def leaderboard_text():
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

    for rank, (_, data) in enumerate(sorted_scores, start=1):

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

        lines.append(
            f"{prefix} {name} — **{points} point"
            f"{'s' if points != 1 else ''}**"
        )

    return "\n".join(lines)


# ---------------------------------------------------------
# CHAT PARSING
# ---------------------------------------------------------

def find_chatter(prefix):
    prefix = prefix.strip().lower()

    matches = []

    for display_name, username in CHATTERS.items():
        username_lower = username.lower()

        if prefix.endswith(username_lower):
            matches.append(
                (len(username_lower), display_name, username)
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
            message = rest[colon_index + 1:].strip()

            if len(message) < MIN_CHARACTERS:
                continue

            if current_date is None:
                continue

            chatter = find_chatter(prefix)

            if not chatter:
                continue

            _, username = chatter

            chatters[username].append(
                (message, current_date)
            )

    return {
        username: messages
        for username, messages in chatters.items()
        if messages
    }


def display_name_for(username):

    for display_name, exact_username in CHATTERS.items():

        if exact_username.lower() == username.lower():
            return display_name

    return username


# ---------------------------------------------------------
# ACTIVE POLLS
# ---------------------------------------------------------

# Structure:
#
# active_polls[message_id] = {
#     "correct_answer_id": int,
#     "votes": {
#         user_id: answer_id
#     }
# }

active_polls = {}


# ---------------------------------------------------------
# POLL VOTE EVENTS
# ---------------------------------------------------------

@client.event
async def on_poll_vote_add(user, answer):

    message = answer.poll.message

    if message is None:
        return

    poll_data = active_polls.get(message.id)

    if poll_data is None:
        return

    user_id = str(user.id)

    poll_data["votes"][user_id] = answer.id

    # Store current Discord display name
    if user_id in scores:
        scores[user_id]["name"] = user.display_name


@client.event
async def on_poll_vote_remove(user, answer):

    message = answer.poll.message

    if message is None:
        return

    poll_data = active_polls.get(message.id)

    if poll_data is None:
        return

    user_id = str(user.id)

    # Only remove if this is still their current vote
    if poll_data["votes"].get(user_id) == answer.id:
        del poll_data["votes"][user_id]


# ---------------------------------------------------------
# CREATE ONE GUESS
# ---------------------------------------------------------

async def post_guess(channel, chatters):

    if len(chatters) < POLL_OPTIONS:

        await channel.send(
            "Not enough valid chatters for a 5-option poll."
        )

        return

    # Pick random chatter
    username = random.choice(
        list(chatters.keys())
    )

    # Pick random message
    message, date = random.choice(
        chatters[username]
    )

    correct_display_name = display_name_for(
        username
    )

    # Pick four wrong answers
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

    # Create Discord poll
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

    message_content = (
        f"💬 **Guess the Chatter**\n\n"
        f"> {message}\n\n"
        f"📅 **Date:** {date}"
    )

    # Send quote + poll
    poll_message = await channel.send(
        content=message_content,
        poll=poll
    )

    # Remember this poll
    active_polls[poll_message.id] = {
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

        correct_id = poll_data[
            "correct_answer_id"
        ]

        voters = list(
            poll_data["votes"].items()
        )

        # Give +1 to everyone whose FINAL vote
        # was the correct answer
        for user_id, answer_id in voters:

            if answer_id != correct_id:
                continue

            try:
                user = await client.fetch_user(
                    int(user_id)
                )

                add_point(user)

            except Exception as error:

                print(
                    f"Could not award point "
                    f"to {user_id}: {error}"
                )

        # Save updated scores
        save_scores()

        # Save to GitHub
        await asyncio.to_thread(
            save_scores_to_github
        )

    # Close poll
    try:
        await poll_message.end_poll()

    except discord.HTTPException:
        pass

    # Reveal answer
    await channel.send(
        f"🔓 **The answer was:** "
        f"||{correct_display_name}||"
    )

    # Remove finished poll
    active_polls.pop(
        poll_message.id,
        None
    )


# ---------------------------------------------------------
# LEADERBOARD LOOP
# ---------------------------------------------------------

async def leaderboard_loop(channel):

    while True:

        await asyncio.sleep(
            LEADERBOARD_INTERVAL_SECONDS
        )

        # Make sure latest scores are saved
        save_scores()

        await asyncio.to_thread(
            save_scores_to_github
        )

        # Send a NEW leaderboard message
        await channel.send(
            leaderboard_text()
        )


# ---------------------------------------------------------
# BOT START
# ---------------------------------------------------------

@client.event
async def on_ready():

    print(
        f"Logged in as {client.user}"
    )

    load_scores()

    channel = await client.fetch_channel(
        CHANNEL_ID
    )

    chatters = load_chatters()

    if not chatters:

        await channel.send(
            "No valid chatters found."
        )

        await client.close()

        return

    # Start leaderboard task only once
    if not hasattr(client, "leaderboard_task"):

        client.leaderboard_task = asyncio.create_task(
            leaderboard_loop(channel)
        )

    # Main 5-minute loop
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
                f"Error during guess: {error}"
            )

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


client.run(TOKEN)
