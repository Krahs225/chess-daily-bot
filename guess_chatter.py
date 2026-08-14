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

# New quote every 5 minutes
QUOTE_INTERVAL_SECONDS = 5 * 60

# Answer after 3 minutes
ANSWER_DELAY_SECONDS = 3 * 60

# Leaderboard every 10 minutes
LEADERBOARD_INTERVAL_SECONDS = 10 * 60

# Persistent leaderboard file
SCORES_FILE = "guess_chatter_scores.json"


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

# Active polls:
# message_id -> {
#     "correct_username": "...",
#     "correct_display_name": "...",
#     "votes": {
#         "discord_user_id": answer_id
#     }
# }
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

        # No changes to commit
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
            "Leaderboard saved permanently to GitHub.",
            flush=True
        )

    except Exception as error:

        print(
            f"Could not save leaderboard to GitHub: {error}",
            flush=True
        )


async def save_scores_permanently():

    save_scores()

    await asyncio.to_thread(
        push_scores_to_github
    )


# =========================================================
# CHATTER PARSING
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
                r"^\d{1,2}:\d{2}\s*(.*)$",
                line
            )

            if not time_match:
                continue

            rest = time_match.group(1)

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
                (
                    message,
                    current_date
                )
            )

    return {
        username: messages
        for username, messages in chatters.items()
        if messages
    }


def display_name_for(username):

    for display_name, exact_username in CHATTERS.items():

        if (
            exact_username.lower()
            == username.lower()
        ):

            return display_name

    return username


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
        key=lambda item: item[1]["points"],
        reverse=True
    )

    lines = [
        "🏆 **Guess the Chatter — Leaderboard**",
        ""
    ]

    # Keep all players for now
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

        # Always keep the current Discord name
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
    chatters
):

    if len(chatters) < POLL_OPTIONS:

        await channel.send(
            "Not enough valid chatters "
            "for a 5-option poll."
        )

        return

    username = random.choice(
        list(chatters.keys())
    )

    message, date = random.choice(
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

    wrong_usernames = random.sample(
        wrong_usernames,
        POLL_OPTIONS - 1
    )

    options = (
        wrong_usernames
        + [username]
    )

    random.shuffle(options)

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
        f"📅 **Date:** {date}"
    )

    poll_message = await channel.send(
        content=message_content,
        poll=poll
    )

    # Find the answer ID belonging
    # to the correct chatter.
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

        "votes": {}
    }

    print(
        f"Poll created: {poll_message.id} "
        f"correct answer = "
        f"{correct_display_name}",
        flush=True
    )

    # Wait 3 minutes
    await asyncio.sleep(
        ANSWER_DELAY_SECONDS
    )

    # End poll
    try:

        await poll_message.end_poll()

    except discord.HTTPException:

        pass

    # Give points based on the votes
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

        del active_polls[
            poll_message.id
        ]

    # Answer message
    await channel.send(
        f"🔓 **The answer was:** "
        f"||{correct_display_name}||"
    )


# =========================================================
# POLL VOTE EVENTS
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

    # Store the user's current vote.
    #
    # If they change their vote,
    # the latest answer replaces the old one.
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

    # IMPORTANT:
    # Load old points instead of starting from 0.
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

    print(
        f"Loaded {len(chatters)} chatters.",
        flush=True
    )

    print(
        "Guess the Chatter is running.",
        flush=True
    )

    # Leaderboard task
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

    # Main quote loop
    while True:

        start_time = (
            asyncio.get_running_loop()
            .time()
        )

        try:

            await post_guess(
                channel,
                chatters
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
