import asyncio
import os
import random
import re
from datetime import datetime, timezone, timedelta
from pathlib import Path

import discord

from guess_leaderboard import (
    add_points,
    full_leaderboard,
    personal_ranking,
)

from guess_chess_chatter import (
    GUESS_CHESS_BUILD,
    post_chess_round,
)

TOKEN = os.getenv(
    "DISCORD_TOKEN"
)

CHANNEL_ID = 1536769340970373241

GUESS_CHATTER_BUILD = "guess-chatter-v5-persistent-controller-2026-09-03"
GUESS_CONTROLLER_BUILD = "guess-games-v5-alternate-10m-next-2026-09-03"
PLAYER_INFO_BUILD = "guess-player-info-status-v1-2026-09-03"
PERSISTENT_GUESS_V5 = True
SHARKMEISTER_DEFAULT_USER_ID = "362606514764251137"

MIN_CHARACTERS = 20
CHAT_DIR = "SOLO chats"

POLL_OPTIONS = 5
POLL_DURATION_MINUTES = 8
ROUND_SLOT_MINUTES = 20
GUESS_SLOT_OFFSET = 0
TIME_ZONE = "Europe/Amsterdam"

NEXT_ROUND_EVENT = asyncio.Event()
ROUND_ACTIVE = False
NEXT_REQUESTED = False

# One persistent process owns BOTH Guess games. This avoids two Discord
# sessions with the same bot token and makes !l available between rounds.
CURRENT_ROUND_TYPE = None
FORCED_NEXT_TYPE = None
ROUND_LOCK = asyncio.Lock()
SCHEDULER_TASK = None

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
    "chess": 10,
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


# Short recognition guides for every current Guess the Chatter player.
# Commands are public: !thice, !sushi, !az, etc.
PLAYER_INFO = {
    "az": (
        "AZ",
        "**How to recognize:** Very dry/sarcastic. Loves intentionally obvious chess advice, says **wow** a lot, "
        "and has recurring jokes about the real 3D board, random *when?* suggestions, and fake-unfollowing over tiny things."
    ),
    "ben": (
        "Ben",
        "**How to recognize:** The huge tell is **New Zealand GeoGuessr**: NZ regions, road/meta details, "
        "AI-generated New Zealand maps and rematches. Usually concise and matter-of-fact."
    ),
    "geeflux": (
        "Geeflux",
        "**How to recognize:** Short, energetic messages: **yoo, haha, wtf, gg, let's play**. "
        "Usually quick reactions rather than paragraphs; casual and upbeat."
    ),
    "george": (
        "George",
        "**How to recognize:** Conversational and thinks out loud. Lots of **haha, maybe, I guess, I thought, not sure** "
        "while working through clues step by step."
    ),
    "grumpymonk": (
        "Grumpymonk",
        "**How to recognize:** Friendly and thoughtful, often writes fuller sentences. Recurring themes are "
        "**Sweden, FIDE/tournaments, chess books and improvement**."
    ),
    "jessebrawlstars": (
        "Jessebrawlstars",
        "**How to recognize:** Short blunt internet slang: **bro, bruv, unc, tuff**, playful roasting, "
        "then suddenly a concrete chess move or puzzle answer."
    ),
    "kurupt": (
        "Kurupt",
        "**How to recognize:** Compact casual-gamer chat: **xD, lol**, CS/gambling/game references, "
        "and lots of simple one-line reactions."
    ),
    "martin": (
        "Martin",
        "**How to recognize:** Chaotic teasing energy: lots of **nah, bruh, uppercase shouting**, mock outrage, "
        "wanting to play, and very distinctive hamster jokes."
    ),
    "mh": (
        "MH",
        "**How to recognize:** Extremely calculation-heavy in chess: move sequences, forcing lines, puzzle analysis, "
        "and lots of **coz**. If chat suddenly becomes a miniature analysis board, think MH."
    ),
    "mohammad": (
        "Mohammad",
        "**How to recognize:** Very recognizable **hello hello**, polite challenges, GG, asking to play, "
        "rating/tournament talk and often **I gtg** when leaving.\n"
        "**Chess style:** Fast and tactical, especially comfortable in **bullet**. Strong recurring interest in the "
        "**Alien Gambit / Martian Gambit** and practical attacking ideas."
    ),
    "mrthice": (
        "Mr_thice",
        "**How to recognize:** Probably the strongest fingerprint is **XD**. Also lots of **maybe, prob, or smt, aswell**, "
        "quick corrections and raw move sequences. **Mr_thick / Mr_thice + XD** is a huge tell.\n"
        "**Chess style:** Tactical/calculation-oriented and strong at puzzle ideas, but intentionally unserious: "
        "*move first think later XD*. Recurring **Dutch** and **French Defense** talk; practical positions over endless theory."
    ),
    "nairyaaa": (
        "Nairyaaa",
        "**How to recognize:** Lots of **haha, XD**, expressive punctuation/emojis, questions and polite reactions. "
        "Usually sounds genuinely curious and asks why things work.\n"
        "**Chess style:** Deliberate and quality-first rather than a natural bullet grinder; prefers finding the best move "
        "and can get frustrated when the clock forces a rushed decision."
    ),
    "pabu": (
        "Pabu",
        "**How to recognize:** **Emote/word spam** is a major tell: long Clap chains, repeated place names and Twitch emotes. "
        "Can suddenly switch from spam into serious chess or Geo discussion."
    ),
    "pandarou": (
        "Pandarou",
        "**How to recognize:** Lots of **xD/xDD**, dry reactions, direct move analysis and opening terminology. "
        "Often sounds half stream-watching, half analyzing a chess position.\n"
        "**Chess style:** Very **opening/theory-oriented**, with concrete gambit lines and **Alapin** ideas; comfortable with "
        "opening traps as well as deeper theoretical discussion."
    ),
    "pospos": (
        "Pospos",
        "**How to recognize:** Huge themes are **GeoGuessr** plus excited/self-deprecating chess Elo milestones, "
        "with plenty of specific map/meta observations.\n"
        "**Chess style:** Improving opening-focused player: **London** as White and **Caro-Kann** as Black; straightforward "
        "structures while still building endgame/theory knowledge."
    ),
    "rubriek": (
        "Rubriek",
        "**How to recognize:** Twitch-culture fingerprint: **7TV/emote talk**, bot commands, EZ Clap, peepoHappy, AlienDance, "
        "and generally short messages rather than essays."
    ),
    "sativahibread": (
        "Sativahibread",
        "**How to recognize:** Analytical but still jokey. Often discusses **chess games, gambits, practical psychology and fast play**, "
        "with a recurring idea of getting into the opponent's head and exploiting mistakes."
    ),
    "screamingcat": (
        "Screamingcat",
        "**How to recognize:** Long explanatory messages and mini fact-dumps. Often interested in technical/history/science details; "
        "spellings such as **definitly** and **alot** also stand out."
    ),
    "sh4rkmateisthebest": (
        "Sh4rkmate is the best",
        "**How to recognize:** Distinctive English spellings such as **cheack, massege, broo, agn, yee**. "
        "Often asks Shark to play, check Chess.com, or talks directly about chess/CS."
    ),
    "soyadelson": (
        "Soyadelson",
        "**How to recognize:** More verbose/story-like than most: **poker, rating challenges, competitive banter**, "
        "long explanations and occasional multilingual jokes.\n"
        "**Chess style:** Competitive and improvement-driven, with a strong **puzzle/tactics** interest; ambitious and streaky, "
        "more at home creating action than sterile positions."
    ),
    "stepu": (
        "Stepu",
        "**How to recognize:** Classic banter: **wassup, haha, XD, nah, ezz, skill issue, nice one**. "
        "Regularly roasts Thice/Shark in an obviously joking way.\n"
        "**Chess style:** Fast, confident and practical; strong rapid/bullet identity with tactical middlegame instincts and "
        "a willingness to play actively rather than sit in quiet positions."
    ),
    "sushi": (
        "Sushi",
        "**How to recognize:** Calls Shark **Sharky** a lot; frequent **tho, ugh, gotta, ain't, dammit, haha**. "
        "Chess messages are confident, direct and theory-heavy.\n"
        "**Chess style:** **DUBOV ITALIAN** is the headline. Loves sharp theory, gambits, sacrifices, attacking ideas and "
        "practical clock play; if a cool sacrifice might work, Sushi wants to investigate it."
    ),
    "thejazzdude": (
        "Thejazzdude",
        "**How to recognize:** Naturally mixes **Dutch and English**, relaxed words like **dude, man, maat**, "
        "and friendly complete sentences. Jazz and War Thunder are recurring topics."
    ),
}

PLAYER_INFO_ALIASES = {
    "jesse": "jessebrawlstars",
    "moh": "mohammad",
    "thice": "mrthice",
    "mrthick": "mrthice",
    "panda": "pandarou",
    "sativa": "sativahibread",
    "adelson": "soyadelson",
    "sh4rkbest": "sh4rkmateisthebest",
    "jazz": "thejazzdude",
}


def _player_info_key(value):
    return re.sub(r"[^a-z0-9]+", "", str(value).casefold())


def player_info_for_command(command):
    if not command.startswith("!"):
        return None

    key = _player_info_key(command[1:])
    key = PLAYER_INFO_ALIASES.get(key, key)
    return PLAYER_INFO.get(key)


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

# /status is already registered on Discord by the Daily Puzzle bot.
# This controller owns the same command only inside the Guess Games channel.
# No sync is done here, so it cannot overwrite other application commands.
command_tree = discord.app_commands.CommandTree(client)
CURRENT_PRIVATE_GUESS = None


def _set_private_guess_answer(round_type, answer, game_url=None):
    global CURRENT_PRIVATE_GUESS
    CURRENT_PRIVATE_GUESS = {
        "type": str(round_type),
        "answer": str(answer),
        "game_url": str(game_url or "").strip(),
    }


def _clear_private_guess_answer():
    global CURRENT_PRIVATE_GUESS
    CURRENT_PRIVATE_GUESS = None


@command_tree.command(
    name="status",
    description="Show puzzle bot status.",
)
async def private_guess_status_command(interaction: discord.Interaction):
    # The Daily Puzzle process owns /status everywhere except this channel.
    # Returning without acknowledging here lets that process answer there.
    if interaction.channel_id != CHANNEL_ID:
        return

    await interaction.response.defer(ephemeral=True)

    shark_id = os.getenv(
        "SHARKMEISTER_USER_ID",
        SHARKMEISTER_DEFAULT_USER_ID,
    ).strip() or SHARKMEISTER_DEFAULT_USER_ID

    if str(interaction.user.id) != shark_id:
        await interaction.edit_original_response(
            content="✅ **Guess bot is online.**"
        )
        return

    active = CURRENT_PRIVATE_GUESS
    if not active:
        await interaction.edit_original_response(
            content=(
                "✅ **Guess bot is online.**\n"
                "No active Guess answer is available right now."
            )
        )
        return

    title = (
        "Guess the Chess Chatter"
        if active.get("type") == "chess"
        else "Guess the Chatter"
    )
    text = (
        f"🤫 **{title}**\n"
        f"**Answer:** `{active.get('answer', '')}`"
    )
    if active.get("game_url"):
        text += f"\n**Game:** {active['game_url']}"

    await interaction.edit_original_response(content=text)


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



async def post_guess(
    channel
):
    global ROUND_ACTIVE
    global NEXT_REQUESTED

    # Persistent controller can run many rounds in one process.
    NEXT_REQUESTED = False
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

    _set_private_guess_answer(
        "chatter",
        display_name_for(username),
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



def scheduled_round_type(moment=None):
    """0/20/40 = Chatter, 10/30/50 = Chess."""
    if moment is None:
        moment = current_local_time()

    minute = moment.minute
    if minute % 20 == 0:
        return "chatter"
    if minute % 20 == 10:
        return "chess"
    return None


def next_ten_minute_slot():
    now = current_local_time()
    base = now.replace(second=0, microsecond=0)
    minutes_to_add = 10 - (now.minute % 10)

    # If the process happens to become ready exactly on a slot, use that slot.
    if now.minute % 10 == 0 and now.second == 0 and now.microsecond == 0:
        target = base
    else:
        target = base + timedelta(minutes=minutes_to_add)

    return target


async def start_round(channel, round_type, reason="schedule"):
    global CURRENT_ROUND_TYPE
    global FORCED_NEXT_TYPE

    if round_type not in {"chatter", "chess"}:
        return False

    async with ROUND_LOCK:
        if CURRENT_ROUND_TYPE is not None:
            print(
                f"Guess {round_type} skipped ({reason}): "
                f"{CURRENT_ROUND_TYPE} is already running.",
                flush=True,
            )
            return False

        # After a workflow restart, an older poll may still be open for a few
        # seconds. Never post a duplicate on top of it.
        active_type = await latest_active_round_type(channel)
        if active_type is not None:
            print(
                f"Guess {round_type} skipped ({reason}): "
                f"Discord already has active {active_type} round.",
                flush=True,
            )
            return False

        CURRENT_ROUND_TYPE = round_type
        NEXT_ROUND_EVENT.clear()

        try:
            if round_type == "chatter":
                await post_guess(channel)
            else:
                await post_chess_round(
                    channel,
                    stop_event=NEXT_ROUND_EVENT,
                    answer_callback=_set_private_guess_answer,
                )
        except Exception as error:
            print(
                f"Guess {round_type} round error: {error}",
                flush=True,
            )
            try:
                await channel.send(
                    f"❌ **Guess {round_type.title()} error:** "
                    f"`{str(error)[:900]}`"
                )
            except Exception:
                pass
        finally:
            CURRENT_ROUND_TYPE = None
            NEXT_ROUND_EVENT.clear()
            _clear_private_guess_answer()

        forced = FORCED_NEXT_TYPE
        FORCED_NEXT_TYPE = None

    if forced is not None:
        # Let the answer/reward messages settle before the next poll appears.
        await asyncio.sleep(2)
        asyncio.create_task(
            start_round(
                channel,
                forced,
                reason="!next",
            )
        )

    return True


async def scheduler_loop(channel):
    """Keep fixed 10-minute alternation for the lifetime of the Action."""
    while not client.is_closed():
        target = next_ten_minute_slot()
        now = current_local_time()
        wait_seconds = max(0.0, (target - now).total_seconds())

        print(
            f"Next Guess slot: {target.isoformat()}",
            flush=True,
        )

        await asyncio.sleep(wait_seconds)

        round_type = scheduled_round_type(target)
        if round_type is not None:
            asyncio.create_task(
                start_round(
                    channel,
                    round_type,
                    reason="schedule",
                )
            )

        # Move beyond the exact boundary so the same slot is never selected twice.
        await asyncio.sleep(1.2)


async def command_handler(message):
    global NEXT_REQUESTED
    global FORCED_NEXT_TYPE

    if (
        message.author.bot
        or message.channel.id != CHANNEL_ID
    ):
        return

    command = message.content.strip().casefold()

    if command in {"!next", "!n"}:
        if CURRENT_ROUND_TYPE is None:
            await message.channel.send(
                "⏭️ **There is no active Guess round to skip.**"
            )
            return

        if FORCED_NEXT_TYPE is not None:
            return

        FORCED_NEXT_TYPE = (
            "chess"
            if CURRENT_ROUND_TYPE == "chatter"
            else "chatter"
        )
        NEXT_REQUESTED = True
        NEXT_ROUND_EVENT.set()

        await message.channel.send(
            f"⏭️ **Next!** Ending Guess the "
            f"{'Chatter' if CURRENT_ROUND_TYPE == 'chatter' else 'Chess Chatter'} "
            f"now. The other Guess game starts right after the answer."
        )
        return

    if command in {"!leaderboard", "!lb", "!l"}:
        leaderboard_text = await asyncio.to_thread(
            full_leaderboard,
            "🏆 **Guess Games Leaderboard**",
        )
        await message.channel.send(leaderboard_text)
        return

    if command in {"!help", "!info", "!i"}:
        await message.channel.send(
            "🧠 **Guess Games**\n\n"
            "💬 **Guess the Chatter** — :00 / :20 / :40\n"
            "♟️ **Guess the Chess Chatter** — :10 / :30 / :50\n"
            "Each poll is open for **8 minutes**.\n\n"
            "⏭️ `!next` / `!n` — end the active poll, reveal/award it, "
            "then immediately start the other Guess game.\n"
            "🏆 `!l` / `!lb` / `!leaderboard` — leaderboard at any time.\n"
            "👤 `!<name>` — show recognition info about a Guess Chatter player "
            "(for example `!thice` or `!sushi`).\n\n"
            "Guess Chatter still has its scheduled Double Points / Hard Mode bonus rounds."
        )
        return

    info = player_info_for_command(command)
    if info is not None:
        display_name, description = info
        await message.channel.send(
            f"👤 **{display_name}**\n{description}"
        )
        return


@client.event
async def on_message(message):
    try:
        await command_handler(message)
    except Exception as error:
        print(
            f"Guess controller command error: {error}",
            flush=True,
        )
        try:
            await message.channel.send(
                "❌ **Guess bot error:** "
                f"`{str(error)[:1000]}`"
            )
        except Exception:
            pass


@client.event
async def on_ready():
    global SCHEDULER_TASK

    if getattr(client, "_guess_controller_started", False):
        return

    client._guess_controller_started = True

    print(
        f"Guess Games controller ready as {client.user}",
        flush=True,
    )
    print(f"Controller build: {GUESS_CONTROLLER_BUILD}", flush=True)
    print(f"Chatter build: {GUESS_CHATTER_BUILD}", flush=True)
    print(f"Chess build: {GUESS_CHESS_BUILD}", flush=True)

    channel = await client.fetch_channel(CHANNEL_ID)

    SCHEDULER_TASK = asyncio.create_task(
        scheduler_loop(channel)
    )

    print(
        "Guess Games persistent controller is running continuously.",
        flush=True,
    )


@client.event
async def on_disconnect():
    print(
        "Guess Games Discord connection lost; reconnecting automatically.",
        flush=True,
    )


@client.event
async def on_resumed():
    print(
        "Guess Games Discord connection resumed.",
        flush=True,
    )


print("Starting persistent Guess Games controller...", flush=True)
client.run(TOKEN, reconnect=True)
