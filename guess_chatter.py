import asyncio
import os
import random
import re
import math
from collections import Counter
from datetime import datetime, timezone, timedelta
from difflib import get_close_matches
from pathlib import Path

import discord
import chess
import chess.svg
import cairosvg
from io import BytesIO

from guess_leaderboard import (
    add_points,
    full_leaderboard,
    personal_ranking,
    record_poll_votes,
    guess_stats_for_user,
    guess_stats_for_name,
    format_guess_stats,
    get_score as guess_get_score,
    backfill_existing_guess_points_to_shared_coins,
)

from guess_chess_chatter import (
    GUESS_CHESS_BUILD,
    post_chess_round,
)

from shared_leaderboard import (
    get_cosmetic_profile,
    get_coins as shared_get_coins,
    buy_badge_box,
    equip_badge,
    buy_board,
    equip_board,
    buy_piece,
    equip_piece,
    transfer_coins,
    transfer_badge,
    resolve_badge as shared_resolve_badge,
    propose_trade as shared_propose_trade,
    accept_trade as shared_accept_trade,
    decline_trade as shared_decline_trade,
    format_trade_asset as shared_format_trade_asset,
    resolve_cosmetic_profile as shared_resolve_cosmetic_profile,
    format_points as shared_format_points,
)
from shop_catalog import (
    BADGE_BOX_COST, BADGE_POOLS, RARITY_LABELS,
    BOARD_COST, BOARD_THEMES, BOARD_DISPLAY_NAMES,
    PIECE_COST, PIECE_SETS, PIECE_DISPLAY_NAMES,
)

TOKEN = os.getenv(
    "DISCORD_TOKEN"
)

CHANNEL_ID = 1536769340970373241

GUESS_CHATTER_BUILD = "guess-chatter-v5-persistent-controller-2026-09-03"
GUESS_CONTROLLER_BUILD = "guess-games-v5-alternate-10m-next-2026-09-03"
PLAYER_INFO_BUILD = "guess-player-info-status-v2-2026-09-03"
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
LAST_ROUND_TYPE = None
# Protect the tiny gap between an idle !next and the task actually claiming
# CURRENT_ROUND_TYPE. Without this, two fast !next messages can queue the
# same next round twice.
PENDING_START_TYPE = None
ROUND_LOCK = asyncio.Lock()
SCHEDULER_TASK = None

ROUND_PREFIXES = {
    "chatter": (
        "💬 **Guess the Chatter**",
        "🔥 **Guess the Chatter — DOUBLE POINTS**",
        "💀 **Guess the Chatter — HARD MODE**",
        "🎭 **Guess the Chatter — QUOTE HUNT**",
    ),
    "chess": (
        "♟️ **Guess the Chess Chatter** —",
        "⚡ **CLOCK SCRAMBLE — DOUBLE POINTS** —",
    ),
}

ROUND_MAX_AGE_MINUTES = {
    "chatter": 10,
    "chess": 10,
}

QUOTE_HUNT_CHANCE = 0.10
QUOTE_HUNT_MAX_LENGTH = 55
QUOTE_HUNT_RECENT_LIMIT = 120
QUOTE_HUNT_RECENT_KEYS = []

TOTAL_DISASTER_OPENERS = [
    "The correct answer walked through the room completely unnoticed.",
    "Every single vote managed to dodge the correct answer.",
    "The correct option was right there and still got abandoned.",
    "Nobody found the target. Not one brave soul.",
    "The entire lobby collectively looked the other way.",
    "The correct answer survived the poll without being touched.",
    "Everyone formed a plan, and somehow the plan excluded the answer.",
    "The right option just watched the chaos from the sidelines.",
    "Not a single detective made it to the correct door.",
    "The answer hid in plain sight and won easily.",
]
TOTAL_DISASTER_MIDDLES = [
    "This was less a vote and more a coordinated evacuation.",
    "The investigation has officially been classified as missing.",
    "Accuracy has temporarily left the server.",
    "The guessing department requests immediate reinforcements.",
    "Several theories were tested. Reality was not among them.",
    "The poll has asked for witness protection.",
    "Every wrong option received more emotional support than the truth.",
    "The evidence was present. The detectives were elsewhere.",
    "Statistically impressive, strategically catastrophic.",
    "The correct answer would like to file a complaint.",
]
TOTAL_DISASTER_ENDINGS = [
    "Absolute cinema.",
    "A flawless disaster.",
    "We go again.",
    "History has been made for all the wrong reasons.",
    "Please pretend the replay does not exist.",
]


def total_disaster_message():
    # 10 × 10 × 5 = 500 distinct combinations.
    return (
        "💥 **TOTAL DISASTER**\n"
        + random.choice(TOTAL_DISASTER_OPENERS)
        + " "
        + random.choice(TOTAL_DISASTER_MIDDLES)
        + " "
        + random.choice(TOTAL_DISASTER_ENDINGS)
    )


def _guess_badge_rows(badges, rarity=None):
    counts = Counter(badges)
    first_index = {}
    for index, badge in enumerate(badges, 1):
        first_index.setdefault(badge, index)
    rows = []
    for badge, count in counts.items():
        badge_rarity = next((r for r, pool in BADGE_POOLS.items() if badge in pool), "unknown")
        if rarity and badge_rarity != rarity:
            continue
        rows.append((first_index[badge], badge, badge_rarity, count))
    return sorted(rows, key=lambda row: row[0])


def guess_cosmetic_profile_dashboard(user_id, display_name):
    profile = get_cosmetic_profile(user_id, display_name)
    badges = list(profile.get("badges", []))
    unique = set(badges)
    active = profile.get("active_badge") or "—"
    active_board = profile.get("active_board", "classic")
    active_piece = profile.get("active_piece", "classic")
    counts = {
        rarity: len({badge for badge in unique if badge in BADGE_POOLS[rarity]})
        for rarity in BADGE_POOLS
    }
    rarity_line = " • ".join(
        f"{RARITY_LABELS[r]} {counts[r]}"
        for r in ("legendary", "epic", "rare", "uncommon", "common", "basic")
    )
    return (
        f"👤 **Guess Profile — {(active + ' ') if active != '—' else ''}{profile.get('name', display_name)}**\n"
        f"🪙 **Coins:** {shared_format_points(profile.get('coins', 0))}\n"
        f"🏅 **Active badge:** {active}\n"
        f"🎨 **Active board:** {BOARD_DISPLAY_NAMES.get(active_board, str(active_board).title())}\n"
        f"♟️ **Active pieces:** {PIECE_DISPLAY_NAMES.get(active_piece, str(active_piece).title())}\n\n"
        f"🏅 **Badges:** {len(unique)} unique / {len(badges)} total\n"
        f"{rarity_line}\n"
        f"🎨 **Boards owned:** {len(profile.get('boards', [])) + 1}/{len(BOARD_THEMES)}\n"
        f"♟️ **Piece sets owned:** {len(profile.get('pieces', [])) + 1}/{len(PIECE_SETS)}\n\n"
        "Use the buttons below to browse badges, boards and pieces.\n"
        "On your own profile, click an owned cosmetic to equip it. `!profile badge 0` still unequips your badge."
    )


def guess_badge_overview(user_id, display_name):
    profile = get_cosmetic_profile(user_id, display_name)
    badges = list(profile.get("badges", []))
    unique = set(badges)
    lines = [
        f"🏅 **{profile.get('name', display_name)} — Badge Collection**",
        f"**{len(unique)} unique / {len(badges)} total**",
        "",
    ]
    for rarity in ("legendary", "epic", "rare", "uncommon", "common", "basic"):
        owned = len({badge for badge in unique if badge in BADGE_POOLS[rarity]})
        lines.append(
            f"**{RARITY_LABELS[rarity]}:** {owned}/{len(BADGE_POOLS[rarity])}"
        )
    lines.extend(["", "Use the rarity buttons to browse. Pages show 20 unique badges; duplicates are shown as `×2`, `×3`, etc."])
    return "\n".join(lines)


def guess_badge_page(user_id, display_name, rarity, page=1):
    rarity = str(rarity).casefold()
    if rarity not in BADGE_POOLS:
        raise ValueError("Unknown rarity.")
    profile = get_cosmetic_profile(user_id, display_name)
    rows = _guess_badge_rows(list(profile.get("badges", [])), rarity)
    total_pages = max(1, math.ceil(len(rows) / 20))
    page = max(1, min(int(page), total_pages))
    rows = rows[(page - 1) * 20:page * 20]
    lines = [
        f"🏅 **{profile.get('name', display_name)} — {RARITY_LABELS[rarity]} Badges**",
        f"Page **{page}/{total_pages}**",
        "",
    ]
    if not rows:
        lines.append("None owned in this rarity yet.")
    for index, badge, _r, count in rows:
        suffix = f" ×{count}" if count > 1 else ""
        lines.append(f"`#{index}` {badge}{suffix}")
    lines.extend(["", "Use the buttons below to browse. On your own profile, click a badge button to equip it."])
    return "\n".join(lines)


def guess_cosmetic_profile_messages(user_id, display_name):
    """Backward-compatible wrapper for the compact Guess profile."""
    return [guess_cosmetic_profile_dashboard(user_id, display_name)]


_GUESS_UNICODE_CHESS_GLYPHS = {
    "K": "♔", "Q": "♕", "R": "♖", "B": "♗", "N": "♘", "P": "♙",
    "k": "♚", "q": "♛", "r": "♜", "b": "♝", "n": "♞", "p": "♟",
}


def _guess_page_slice(items, page, page_size):
    total_pages = max(1, math.ceil(len(items) / page_size))
    try:
        page = int(page)
    except Exception:
        page = 1
    page = max(1, min(page, total_pages))
    start = (page - 1) * page_size
    return items[start:start + page_size], page, total_pages


def guess_board_page(user_id, display_name, page=1):
    profile = get_cosmetic_profile(user_id, display_name)
    owned = ["classic"] + [name for name in profile.get("boards", []) if name in BOARD_THEMES]
    page_items, page, total_pages = _guess_page_slice(owned, page, 20)
    lines = [
        f"🎨 **{profile.get('name', display_name)} — Owned Boards**",
        f"Page **{page}/{total_pages}** • {len(owned)}/{len(BOARD_THEMES)} owned",
        "",
    ]
    for name in page_items:
        marker = " ✅" if name == profile.get("active_board", "classic") else ""
        lines.append(f"• **{BOARD_DISPLAY_NAMES.get(name, name.title())}** (`{name}`){marker}")
    lines.extend(["", "Use the buttons below to browse/equip owned boards. `!customboard` opens the shop catalogue."])
    return "\n".join(lines)


def guess_piece_page(user_id, display_name, page=1):
    profile = get_cosmetic_profile(user_id, display_name)
    owned = ["classic"] + [name for name in profile.get("pieces", []) if name in PIECE_SETS]
    page_items, page, total_pages = _guess_page_slice(owned, page, 20)
    lines = [
        f"♟️ **{profile.get('name', display_name)} — Owned Piece Sets**",
        f"Page **{page}/{total_pages}** • {len(owned)}/{len(PIECE_SETS)} owned",
        "",
    ]
    for name in page_items:
        marker = " ✅" if name == profile.get("active_piece", "classic") else ""
        lines.append(f"• **{PIECE_DISPLAY_NAMES.get(name, name.title())}** (`{name}`){marker}")
    lines.extend(["", "Use the buttons below to browse/equip owned pieces. `!custompiece` opens the shop catalogue."])
    return "\n".join(lines)


def guess_board_catalog_message(page=1):
    names = list(BOARD_THEMES)
    page_names, page, total_pages = _guess_page_slice(names, page, 25)
    lines = [
        "🎨 **Custom Boards**",
        f"Price: **{shared_format_points(BOARD_COST)} coins** each. Classic is free.",
        f"Page **{page}/{total_pages}** • {len(BOARD_THEMES)} themes",
        "",
    ]
    for start in range(0, len(page_names), 5):
        lines.append(" • ".join(BOARD_DISPLAY_NAMES[name] for name in page_names[start:start + 5]))
    lines.extend([
        "",
        "Use **Previous / Next** below to browse pages.",
        "`!customboard blue test` — preview",
        "`!customboard blue buy` — buy",
        "`!customboard blue` — equip if owned",
        "`!customboard default` — equip Classic",
    ])
    return "\n".join(lines)


def guess_piece_catalog_message(page=1):
    names = list(PIECE_SETS)
    page_names, page, total_pages = _guess_page_slice(names, page, 20)
    lines = [
        "♟️ **Custom Piece Sets**",
        f"Price: **{shared_format_points(PIECE_COST)} coins** each. Classic is free.",
        f"Page **{page}/{total_pages}** • {len(PIECE_SETS)} sets",
        "",
    ]
    for name in page_names:
        lines.append(f"• **{PIECE_DISPLAY_NAMES[name]}** (`{name}`)")
    lines.extend([
        "",
        "Use **Previous / Next** below to browse pages.",
        "`!custompiece figurine-gold test` — preview",
        "`!custompiece figurine-gold buy` — buy",
        "`!custompiece figurine-gold` — equip if owned",
        "`!custompiece default` — equip Classic",
    ])
    return "\n".join(lines)



GUESS_PROFILE_RARITY_ORDER = ("legendary", "epic", "rare", "uncommon", "common", "basic")


def _guess_button_emoji(value):
    try:
        text = str(value or "")
        if text.startswith("<:") or text.startswith("<a:"):
            return discord.PartialEmoji.from_str(text)
        return text or None
    except Exception:
        return None


class GuessCatalogPager(discord.ui.View):
    """Clickable Guess-channel board/piece browser with instant previews."""

    def __init__(self, viewer_id, kind, page=1, selected_name=None):
        super().__init__(timeout=300)
        self.viewer_id = int(viewer_id)
        self.kind = "board" if str(kind) == "board" else "piece"
        self.names = list(BOARD_THEMES) if self.kind == "board" else list(PIECE_SETS)
        self.page_size = 5
        self.total_pages = max(1, math.ceil(len(self.names) / self.page_size))
        self.page = max(1, min(int(page or 1), self.total_pages))
        page_names = self._page_names()
        wanted = str(selected_name or "").casefold()
        self.selected_name = wanted if wanted in page_names else page_names[0]
        self._rebuild()

    async def interaction_check(self, interaction):
        if interaction.user.id != self.viewer_id:
            await interaction.response.send_message("This catalogue belongs to another user.", ephemeral=True)
            return False
        return True

    def _page_names(self):
        start = (self.page - 1) * self.page_size
        return self.names[start:start + self.page_size]

    def _display_name(self, name):
        if self.kind == "board":
            return BOARD_DISPLAY_NAMES.get(name, name.title())
        return PIECE_DISPLAY_NAMES.get(name, name.title())

    def render(self, profile=None):
        selected = self.selected_name
        display = self._display_name(selected)
        price = BOARD_COST if self.kind == "board" else PIECE_COST
        item_word = "board" if self.kind == "board" else "piece set"
        owned = False
        active = False
        if profile is not None:
            if self.kind == "board":
                owned = selected == "classic" or selected in profile.get("boards", [])
                active = selected == profile.get("active_board", "classic")
            else:
                owned = selected == "classic" or selected in profile.get("pieces", [])
                active = selected == profile.get("active_piece", "classic")
        status = "Classic / free" if selected == "classic" else ("Owned" if owned else f"{shared_format_points(price)} coins")
        if active:
            status += " • Equipped"
        return (
            f"{'🎨' if self.kind == 'board' else '♟️'} **Custom {'Boards' if self.kind == 'board' else 'Piece Sets'}**\n"
            f"Page **{self.page}/{self.total_pages}** • choose one of the 5 buttons below.\n\n"
            f"**Preview:** {display}\n"
            f"**Status:** {status}\n\n"
            f"Click an option to instantly preview that {item_word}. Use **Buy selected** or **Equip selected** when ready."
        )

    async def preview_file(self, display_name):
        profile = await asyncio.to_thread(get_cosmetic_profile, self.viewer_id, display_name)
        if self.kind == "board":
            board_name = self.selected_name
            piece_name = profile.get("active_piece", "classic")
            filename = "guess_board_shop_preview.png"
        else:
            board_name = profile.get("active_board", "classic")
            piece_name = self.selected_name
            filename = "guess_piece_shop_preview.png"
        file = await asyncio.to_thread(
            guess_cosmetic_preview_file,
            board_name,
            piece_name,
            filename,
        )
        return profile, file

    async def _show_selected(self, interaction):
        await interaction.response.defer()
        try:
            profile, file = await self.preview_file(interaction.user.display_name)
            self._rebuild(profile)
            await interaction.message.edit(
                content=self.render(profile),
                attachments=[file],
                view=self,
            )
        except Exception as error:
            await interaction.followup.send(f"❌ Could not render preview: `{str(error)[:800]}`", ephemeral=True)

    def _rebuild(self, profile=None):
        self.clear_items()
        page_names = self._page_names()
        if self.selected_name not in page_names:
            self.selected_name = page_names[0]

        for name in page_names:
            button = discord.ui.Button(
                label=self._display_name(name)[:80],
                style=discord.ButtonStyle.primary if name == self.selected_name else discord.ButtonStyle.secondary,
                row=0,
            )

            async def select_callback(interaction, selected=name):
                self.selected_name = selected
                await self._show_selected(interaction)

            button.callback = select_callback
            self.add_item(button)

        previous = discord.ui.Button(label="◀", style=discord.ButtonStyle.secondary, disabled=self.page <= 1, row=1)
        indicator = discord.ui.Button(label=f"{self.page}/{self.total_pages}", style=discord.ButtonStyle.secondary, disabled=True, row=1)
        next_button = discord.ui.Button(label="▶", style=discord.ButtonStyle.secondary, disabled=self.page >= self.total_pages, row=1)

        async def previous_callback(interaction):
            self.page = max(1, self.page - 1)
            self.selected_name = self._page_names()[0]
            await self._show_selected(interaction)

        async def next_callback(interaction):
            self.page = min(self.total_pages, self.page + 1)
            self.selected_name = self._page_names()[0]
            await self._show_selected(interaction)

        previous.callback = previous_callback
        next_button.callback = next_callback
        self.add_item(previous)
        self.add_item(indicator)
        self.add_item(next_button)

        buy = discord.ui.Button(
            label="Buy selected",
            style=discord.ButtonStyle.success,
            disabled=self.selected_name == "classic",
            row=2,
        )
        equip = discord.ui.Button(label="Equip selected", style=discord.ButtonStyle.primary, row=2)

        async def buy_callback(interaction):
            name = self.selected_name
            await interaction.response.defer()
            try:
                if self.kind == "board":
                    updated = await asyncio.to_thread(
                        buy_board,
                        interaction.user.id,
                        interaction.user.display_name,
                        name,
                        f"guess-catalog-buy-board:{interaction.id}:{interaction.user.id}:{name}",
                    )
                    label = BOARD_DISPLAY_NAMES.get(name, name.title())
                else:
                    updated = await asyncio.to_thread(
                        buy_piece,
                        interaction.user.id,
                        interaction.user.display_name,
                        name,
                        f"guess-catalog-buy-piece:{interaction.id}:{interaction.user.id}:{name}",
                    )
                    label = PIECE_DISPLAY_NAMES.get(name, name.title())
                self._rebuild(updated)
                await interaction.message.edit(content=self.render(updated), view=self)
                await interaction.followup.send(
                    f"✅ Bought **{label}**. 🪙 Coins left: **{shared_format_points(updated['coins'])}**",
                    ephemeral=True,
                )
            except Exception as error:
                await interaction.followup.send(f"❌ Could not buy it: `{str(error)[:800]}`", ephemeral=True)

        async def equip_callback(interaction):
            name = self.selected_name
            await interaction.response.defer()
            try:
                if self.kind == "board":
                    updated = await asyncio.to_thread(
                        equip_board,
                        interaction.user.id,
                        interaction.user.display_name,
                        name,
                        f"guess-catalog-equip-board:{interaction.id}:{interaction.user.id}:{name}",
                    )
                    label = BOARD_DISPLAY_NAMES.get(updated.get("active_board", name), name.title())
                else:
                    updated = await asyncio.to_thread(
                        equip_piece,
                        interaction.user.id,
                        interaction.user.display_name,
                        name,
                        f"guess-catalog-equip-piece:{interaction.id}:{interaction.user.id}:{name}",
                    )
                    label = PIECE_DISPLAY_NAMES.get(updated.get("active_piece", name), name.title())
                self._rebuild(updated)
                await interaction.message.edit(content=self.render(updated), view=self)
                await interaction.followup.send(f"✅ Equipped **{label}**.", ephemeral=True)
            except Exception as error:
                await interaction.followup.send(f"❌ Could not equip it: `{str(error)[:800]}`", ephemeral=True)

        buy.callback = buy_callback
        equip.callback = equip_callback
        self.add_item(buy)
        self.add_item(equip)


async def send_guess_catalog_preview(message, kind, page=1):
    view = GuessCatalogPager(message.author.id, kind, page)
    profile, file = await view.preview_file(message.author.display_name)
    view._rebuild(profile)
    await message.channel.send(view.render(profile), file=file, view=view)

class GuessCosmeticProfileView(discord.ui.View):
    def __init__(self, viewer_id, target_user_id, target_name, editable=False):
        super().__init__(timeout=300)
        self.viewer_id = int(viewer_id)
        self.target_user_id = str(target_user_id)
        self.target_name = str(target_name)
        self.editable = bool(editable and str(viewer_id) == str(target_user_id))
        self.mode = "dashboard"
        self.rarity = None
        self.page = 1
        self._build_dashboard()

    async def interaction_check(self, interaction):
        if interaction.user.id != self.viewer_id:
            await interaction.response.send_message("Open your own `!profile` to use these buttons.", ephemeral=True)
            return False
        return True

    async def _profile(self):
        return await asyncio.to_thread(get_cosmetic_profile, self.target_user_id, self.target_name)

    def render(self, profile=None):
        if self.mode == "dashboard":
            if profile is None:
                return guess_cosmetic_profile_dashboard(self.target_user_id, self.target_name)
            active_badge = profile.get("active_badge") or "—"
            active_board = profile.get("active_board", "classic")
            active_piece = profile.get("active_piece", "classic")
            badges = list(profile.get("badges", []))
            unique = set(badges)
            rarity_line = " • ".join(
                f"{RARITY_LABELS[rarity]} {len({badge for badge in unique if BADGE_RARITY_BY_VALUE.get(badge) == rarity})}"
                for rarity in GUESS_PROFILE_RARITY_ORDER
            )
            return (
                f"👤 **Profile — {active_badge + ' ' if active_badge != '—' else ''}{profile.get('name', self.target_name)}**\n"
                f"🪙 **Coins:** {shared_format_points(profile.get('coins', 0))}\n"
                f"🏅 **Active badge:** {active_badge}\n"
                f"🎨 **Active board:** {BOARD_DISPLAY_NAMES.get(active_board, active_board.title())}\n"
                f"♟️ **Active pieces:** {PIECE_DISPLAY_NAMES.get(active_piece, active_piece.title())}\n\n"
                f"🏅 **Badges:** {len(unique)} unique / {len(badges)} total\n{rarity_line}\n"
                f"🎨 **Boards owned:** {len(profile.get('boards', [])) + 1}/{len(BOARD_THEMES)}\n"
                f"♟️ **Piece sets owned:** {len(profile.get('pieces', [])) + 1}/{len(PIECE_SETS)}\n\n"
                "Use the buttons below to browse the collection."
            )
        if self.mode == "badges":
            if profile is None:
                return guess_badge_page(self.target_user_id, self.target_name, self.rarity, self.page)
            rows = _guess_badge_rows(list(profile.get("badges", [])), self.rarity)
            page_rows, self.page, total_pages = _guess_page_slice(rows, self.page, 20)
            lines = [
                f"🏅 **{profile.get('name', self.target_name)} — {RARITY_LABELS[self.rarity]} Badges**",
                f"Page **{self.page}/{total_pages}** • {len(rows)} unique owned",
                "",
            ]
            if not page_rows:
                lines.append("None owned in this rarity yet.")
            else:
                for index, badge, _rarity, count in page_rows:
                    suffix = f" ×{count}" if count > 1 else ""
                    active = " ✅" if badge == profile.get("active_badge", "") else ""
                    lines.append(f"`#{index}` {badge}{suffix}{active}")
            lines.extend(["", "Use the buttons below to browse. On your own profile, click a badge button to equip it."])
            return "\n".join(lines)
        if self.mode == "boards":
            if profile is None:
                return guess_board_page(self.target_user_id, self.target_name, self.page)
            owned = ["classic"] + [name for name in profile.get("boards", []) if name in BOARD_THEMES]
            page_items, self.page, total_pages = _guess_page_slice(owned, self.page, 20)
            lines = [f"🎨 **{profile.get('name', self.target_name)} — Owned Boards**", f"Page **{self.page}/{total_pages}** • {len(owned)}/{len(BOARD_THEMES)} owned", ""]
            for name in page_items:
                marker = " ✅" if name == profile.get("active_board", "classic") else ""
                lines.append(f"• **{BOARD_DISPLAY_NAMES.get(name, name.title())}** (`{name}`){marker}")
            lines.extend(["", "Use the buttons below to browse/equip owned boards. `!customboard` opens the shop catalogue."])
            return "\n".join(lines)
        if self.mode == "pieces":
            if profile is None:
                return guess_piece_page(self.target_user_id, self.target_name, self.page)
            owned = ["classic"] + [name for name in profile.get("pieces", []) if name in PIECE_SETS]
            page_items, self.page, total_pages = _guess_page_slice(owned, self.page, 20)
            lines = [f"♟️ **{profile.get('name', self.target_name)} — Owned Piece Sets**", f"Page **{self.page}/{total_pages}** • {len(owned)}/{len(PIECE_SETS)} owned", ""]
            for name in page_items:
                marker = " ✅" if name == profile.get("active_piece", "classic") else ""
                lines.append(f"• **{PIECE_DISPLAY_NAMES.get(name, name.title())}** (`{name}`){marker}")
            lines.extend(["", "Use the buttons below to browse/equip owned pieces. `!custompiece` opens the shop catalogue."])
            return "\n".join(lines)
        return guess_cosmetic_profile_dashboard(self.target_user_id, self.target_name)

    def _build_dashboard(self):
        self.clear_items()
        self.mode = "dashboard"
        self.rarity = None
        self.page = 1
        for idx, rarity in enumerate(GUESS_PROFILE_RARITY_ORDER):
            button = discord.ui.Button(
                label=RARITY_LABELS[rarity],
                style=discord.ButtonStyle.primary if rarity in {"legendary", "epic", "rare"} else discord.ButtonStyle.secondary,
                row=idx // 3,
            )
            async def open_rarity(interaction, rarity=rarity):
                profile = await self._profile()
                self.mode = "badges"
                self.rarity = rarity
                self.page = 1
                self._build_badges(profile)
                await interaction.response.edit_message(content=self.render(profile), view=self)
            button.callback = open_rarity
            self.add_item(button)
        for label, mode, emoji in (("Boards", "boards", "🎨"), ("Pieces", "pieces", "♟️")):
            button = discord.ui.Button(label=label, emoji=emoji, style=discord.ButtonStyle.secondary, row=2)
            async def open_mode(interaction, mode=mode):
                profile = await self._profile()
                self.mode = mode
                self.page = 1
                self._build_assets(profile)
                await interaction.response.edit_message(content=self.render(profile), view=self)
            button.callback = open_mode
            self.add_item(button)

    def _build_badges(self, profile):
        self.clear_items()
        rows = _guess_badge_rows(list(profile.get("badges", [])), self.rarity)
        page_rows, self.page, total_pages = _guess_page_slice(rows, self.page, 20)
        if self.editable:
            active = profile.get("active_badge", "")
            for pos, (index, badge, _rarity, _count) in enumerate(page_rows):
                button = discord.ui.Button(
                    label=f"#{index}", emoji=_guess_button_emoji(badge),
                    style=discord.ButtonStyle.success if badge == active else discord.ButtonStyle.secondary,
                    row=pos // 5,
                )
                async def equip_callback(interaction, badge=badge):
                    updated = await asyncio.to_thread(
                        equip_badge, self.target_user_id, self.target_name, badge,
                        f"guess-profile-button-badge:{interaction.id}:{self.target_user_id}",
                    )
                    self._build_badges(updated)
                    await interaction.response.edit_message(content=self.render(updated), view=self)
                button.callback = equip_callback
                self.add_item(button)
        self._add_nav(total_pages, include_none=self.editable)

    def _build_assets(self, profile):
        self.clear_items()
        if self.mode == "boards":
            owned = ["classic"] + [name for name in profile.get("boards", []) if name in BOARD_THEMES]
            active = profile.get("active_board", "classic")
            display = BOARD_DISPLAY_NAMES
            equip_func = equip_board
        else:
            owned = ["classic"] + [name for name in profile.get("pieces", []) if name in PIECE_SETS]
            active = profile.get("active_piece", "classic")
            display = PIECE_DISPLAY_NAMES
            equip_func = equip_piece
        page_items, self.page, total_pages = _guess_page_slice(owned, self.page, 20)
        if self.editable:
            for pos, name in enumerate(page_items):
                button = discord.ui.Button(
                    label=display.get(name, name.title())[:80],
                    style=discord.ButtonStyle.success if name == active else discord.ButtonStyle.secondary,
                    row=pos // 5,
                )
                async def equip_callback(interaction, name=name, equip_func=equip_func):
                    updated = await asyncio.to_thread(
                        equip_func, self.target_user_id, self.target_name, name,
                        f"guess-profile-button-{self.mode}:{interaction.id}:{self.target_user_id}:{name}",
                    )
                    self._build_assets(updated)
                    await interaction.response.edit_message(content=self.render(updated), view=self)
                button.callback = equip_callback
                self.add_item(button)
        self._add_nav(total_pages)

    def _add_nav(self, total_pages, include_none=False):
        back = discord.ui.Button(label="← Profile", style=discord.ButtonStyle.primary, row=4)
        previous = discord.ui.Button(label="◀", style=discord.ButtonStyle.secondary, row=4, disabled=self.page <= 1)
        indicator = discord.ui.Button(label=f"{self.page}/{max(1, total_pages)}", style=discord.ButtonStyle.secondary, row=4, disabled=True)
        next_button = discord.ui.Button(label="▶", style=discord.ButtonStyle.secondary, row=4, disabled=self.page >= max(1, total_pages))
        async def back_callback(interaction):
            profile = await self._profile()
            self._build_dashboard()
            await interaction.response.edit_message(content=self.render(profile), view=self)
        async def previous_callback(interaction):
            profile = await self._profile()
            self.page = max(1, self.page - 1)
            if self.mode == "badges":
                self._build_badges(profile)
            else:
                self._build_assets(profile)
            await interaction.response.edit_message(content=self.render(profile), view=self)
        async def next_callback(interaction):
            profile = await self._profile()
            self.page = min(max(1, total_pages), self.page + 1)
            if self.mode == "badges":
                self._build_badges(profile)
            else:
                self._build_assets(profile)
            await interaction.response.edit_message(content=self.render(profile), view=self)
        back.callback = back_callback
        previous.callback = previous_callback
        next_button.callback = next_callback
        self.add_item(back)
        self.add_item(previous)
        self.add_item(indicator)
        self.add_item(next_button)
        if include_none:
            none_button = discord.ui.Button(label="No badge", style=discord.ButtonStyle.danger, row=4)
            async def none_callback(interaction):
                updated = await asyncio.to_thread(
                    equip_badge, self.target_user_id, self.target_name, "",
                    f"guess-profile-button-badge:{interaction.id}:{self.target_user_id}:none",
                )
                self._build_badges(updated)
                await interaction.response.edit_message(content=self.render(updated), view=self)
            none_button.callback = none_callback
            self.add_item(none_button)


def _guess_piece_overlay_svg(board, orientation, piece_theme):
    style = PIECE_SETS.get(piece_theme, PIECE_SETS["classic"])
    shape = style.get("shape", "classic")
    if shape == "classic":
        return ""
    square_size = 45.0
    board_offset = 15.0
    white_fill = style.get("white_fill", "#f7f7f2")
    black_fill = style.get("black_fill", "#111111")
    white_stroke = style.get("white_stroke", "#111111")
    black_stroke = style.get("black_stroke", "#f7f7f2")
    letters = {1: "P", 2: "N", 3: "B", 4: "R", 5: "Q", 6: "K"}
    parts = ['<g class="custom-piece-set">']
    for square, piece in board.piece_map().items():
        file_index = chess.square_file(square)
        rank_index = chess.square_rank(square)
        x = (file_index if orientation else 7 - file_index) * square_size + board_offset
        y = (7 - rank_index if orientation else rank_index) * square_size + board_offset
        cx = x + square_size / 2
        cy = y + square_size / 2
        fill = white_fill if piece.color else black_fill
        stroke = white_stroke if piece.color else black_stroke
        symbol = piece.symbol()
        letter = letters[piece.piece_type]
        if shape == "figurine":
            glyph = _GUESS_UNICODE_CHESS_GLYPHS[symbol]
            parts.append(
                f'<text x="{cx:.2f}" y="{cy + 1:.2f}" text-anchor="middle" dominant-baseline="central" '
                f'font-family="DejaVu Sans, serif" font-size="38" font-weight="700" fill="{fill}" '
                f'stroke="{stroke}" stroke-width="0.7" paint-order="stroke">{glyph}</text>'
            )
        elif shape in {"monogram", "minimal"}:
            size = 29 if shape == "monogram" else 25
            weight = 800 if shape == "monogram" else 600
            parts.append(
                f'<text x="{cx:.2f}" y="{cy + 1:.2f}" text-anchor="middle" dominant-baseline="central" '
                f'font-family="DejaVu Sans, sans-serif" font-size="{size}" font-weight="{weight}" fill="{fill}" '
                f'stroke="{stroke}" stroke-width="0.8" paint-order="stroke">{letter}</text>'
            )
        else:
            if shape == "token":
                parts.append(f'<circle cx="{cx:.2f}" cy="{cy:.2f}" r="17" fill="{fill}" stroke="{stroke}" stroke-width="2" />')
            elif shape == "diamond":
                pts = f'{cx:.2f},{cy-19:.2f} {cx+18:.2f},{cy:.2f} {cx:.2f},{cy+19:.2f} {cx-18:.2f},{cy:.2f}'
                parts.append(f'<polygon points="{pts}" fill="{fill}" stroke="{stroke}" stroke-width="2" />')
            else:
                pts = f'{cx-16:.2f},{cy-17:.2f} {cx+16:.2f},{cy-17:.2f} {cx+18:.2f},{cy+5:.2f} {cx:.2f},{cy+19:.2f} {cx-18:.2f},{cy+5:.2f}'
                parts.append(f'<polygon points="{pts}" fill="{fill}" stroke="{stroke}" stroke-width="2" />')
            text_fill = "#111111" if piece.color else "#ffffff"
            parts.append(
                f'<text x="{cx:.2f}" y="{cy + 1:.2f}" text-anchor="middle" dominant-baseline="central" '
                f'font-family="DejaVu Sans, sans-serif" font-size="22" font-weight="800" fill="{text_fill}">{letter}</text>'
            )
    parts.append('</g>')
    return "".join(parts)


def guess_render_custom_board_svg(board, board_theme="classic", piece_theme="classic", size=500):
    board_theme = str(board_theme or "classic").casefold()
    piece_theme = str(piece_theme or "classic").casefold()
    light, dark = BOARD_THEMES.get(board_theme, BOARD_THEMES["classic"])
    if piece_theme == "classic" or piece_theme not in PIECE_SETS:
        return chess.svg.board(
            board=board, orientation=True, size=size, coordinates=True,
            colors={"square light": light, "square dark": dark},
        )
    svg = chess.svg.board(
        board=None, orientation=True, size=size, coordinates=True,
        colors={"square light": light, "square dark": dark},
    )
    return svg.replace("</svg>", _guess_piece_overlay_svg(board, True, piece_theme) + "</svg>")


def guess_cosmetic_preview_file(board_theme="classic", piece_theme="classic", filename="guess_cosmetic_preview.png"):
    board = chess.Board()
    svg = guess_render_custom_board_svg(board, board_theme, piece_theme, 500)
    png = cairosvg.svg2png(bytestring=svg.encode("utf-8"))
    return discord.File(BytesIO(png), filename=filename)


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
        "**How to recognize:** Very dry and sarcastic. Loves intentionally useless chess advice, says **wow** a lot, "
        "and has recurring jokes about the **real 3D board**, random *when?* suggestions and fake-unfollowing over tiny things.\n"
        "**Languages:** English."
    ),
    "ben": (
        "Ben",
        "**How to recognize:** The New Zealand GeoGuessr specialist. Very specific NZ regions, roads and metas; "
        "usually concise, confident and matter-of-fact.\n"
        "**Languages:** German, English."
    ),
    "geeflux": (
        "Geeflux",
        "**How to recognize:** Energetic and competitive, but also **rages/tilts easily**. When a chess game goes badly, "
        "expect reasons like being tired, playing randomly, having played too much chess, or simply not having his day. "
        "Lots of **yoo, haha, wtf, gg** and quick reactions.\n"
        "**Languages:** English."
    ),
    "george": (
        "George",
        "**How to recognize:** Thinks out loud constantly. Lots of **maybe, I think, haha, I guess** and chains of observations "
        "before committing to an answer.\n"
        "**Languages:** English."
    ),
    "grumpymonk": (
        "Grumpymonk",
        "**How to recognize:** Friendly and thoughtful. Chess books, strategy, tournaments, improvement and Sweden come up regularly.\n"
        "**Languages:** Swedish, English."
    ),
    "jessebrawlstars": (
        "Jessebrawlstars",
        "**How to recognize:** Lots of **bro, bruv, unc, tuff**, short chaotic roasts, then suddenly an actual chess move or puzzle answer.\n"
        "**Languages:** English, some Dutch.\n"
        "**ELO:** **2000**."
    ),
    "kurupt": (
        "Kurupt",
        "**How to recognize:** Short gamer-style messages, lots of **lol, xD**, CS/gambling references and quick one-line reactions.\n"
        "**Languages:** English."
    ),
    "martin": (
        "Martin",
        "**How to recognize:** Chaotic, dramatic and loud. Lots of **nah, bruh, caps-lock**, mock outrage, wanting to play, "
        "and the recurring **hamster** jokes.\n"
        "**Languages:** Czech, Polish, Slovak, German, English, and some Italian."
    ),
    "mh": (
        "MH",
        "**How to recognize:** Extremely calculation-heavy in chess: move sequences, forcing lines, puzzle analysis and lots of **coz**. "
        "Also known for watching **dubious anime**.\n"
        "**Languages:** English."
    ),
    "mohammad": (
        "Mohammad",
        "**How to recognize:** Very recognizable **hello hello**, polite challenges, GG, asking to play, rating/tournament talk "
        "and often **I gtg** when leaving.\n"
        "**Languages:** Arabic, English.\n"
        "**ELO:** **2200**.\n"
        "**Chess style:** Fast and tactical, especially in **bullet/blitz**. Strong **Alien Gambit / Martian Gambit** fingerprint; "
        "likes active gambit positions and practical complications."
    ),
    "mrthice": (
        "Mr_thice",
        "**How to recognize:** The biggest tell is **XD**. Also lots of **maybe, prob, or smt, aswell**, quick corrections, jokes "
        "and raw chess lines. **Mr_thick / Mr_thice + XD** is a huge tell.\n"
        "**Languages:** English.\n"
        "**ELO:** **2399**.\n"
        "**Chess style:** Tactical and calculation-heavy, strong puzzle instincts and very practical. Recurring **Dutch** and "
        "**French Defense** talk; *move first think later XD* fits the vibe."
    ),
    "nairyaaa": (
        "Nairyaaa",
        "**How to recognize:** Expressive, curious and friendly. Lots of questions, punctuation, emojis and careful reasoning.\n"
        "**Languages:** French, English.\n"
        "**ELO:** **1800**.\n"
        "**Chess style:** Careful and calculation-first. Wants to find the best move rather than rely purely on speed, "
        "and is less naturally comfortable with bullet."
    ),
    "pabu": (
        "Pabu",
        "**How to recognize:** Huge **emote/repetition spam** is the tell: Clap chains, 7TV-style nonsense and repeated words, "
        "then suddenly normal chess or Geo discussion again.\n"
        "**Languages:** English, some Spanish."
    ),
    "pandarou": (
        "Pandarou",
        "**How to recognize:** Dry reactions, lots of **xD/xDD**, concrete move analysis and opening terminology. Often sounds "
        "half stream-watching and half analysing a board.\n"
        "**Languages:** English.\n"
        "**ELO:** **2250**.\n"
        "**Chess style:** Very theory-oriented. Gambits, **Alapin ideas**, concrete variations, prep and differences between "
        "rapid/bullet come up regularly. Likes sharp practical opening ideas."
    ),
    "pospos": (
        "Pospos",
        "**How to recognize:** GeoGuessr plus proudly announcing chess Elo milestones. Often self-deprecating and excited about improvement.\n"
        "**Languages:** English.\n"
        "**ELO:** **800**.\n"
        "**Chess style:** Improving player strongly associated with the **London** and **Caro-Kann**, with opening knowledge "
        "developing faster than endgame knowledge."
    ),
    "rubriek": (
        "Rubriek",
        "**How to recognize:** The 7TV/Twitch-culture person: bot commands, **EZ Clap, peepoHappy, AlienDance**, emote-set talk, etc.\n"
        "**Languages:** English, French."
    ),
    "sativahibread": (
        "Sativahibread",
        "**How to recognize:** Practical, competitive and psychology-focused. Talks about exploiting opponents' mistakes, "
        "playing quickly and getting inside their head; often shares chess games and ideas.\n"
        "**Languages:** English, some Spanish."
    ),
    "screamingcat": (
        "Screamingcat",
        "**How to recognize:** Long explanations, fact dumps and technology/history/science tangents. Usually much more detailed "
        "than the average chatter; spellings such as **definitly** and **alot** also stand out.\n"
        "**Languages:** English."
    ),
    "sh4rkmateisthebest": (
        "Sh4rkmate is the best",
        "**How to recognize:** Distinctive spellings such as **cheack, massege, agn, broo** and lots of direct chess/CS questions.\n"
        "**Languages:** English."
    ),
    "soyadelson": (
        "Soyadelson / Adelson",
        "**How to recognize:** Competitive, talkative, poker/rating stories, dramatic reactions and lots of challenges.\n"
        "**Languages:** Spanish, English.\n"
        "**ELO:** **1300**.\n"
        "**Chess style:** Tactical, ambitious and streaky. Very interested in puzzles and brilliancies; capable of strong tactical "
        "games but openly describes some normal games as getting completely thrown away."
    ),
    "stepu": (
        "Stepu",
        "**How to recognize:** **wassup, skill issue, haha**, friendly trash talk and a lot of confidence. Regularly roasts Thice/Shark.\n"
        "**Languages:** Spanish, English.\n"
        "**ELO:** **2200 on a good day**.\n"
        "**Chess style:** Strong, fast and practical. Rapid/bullet-oriented, tactical, confident and happy to challenge stronger players."
    ),
    "sushi": (
        "Sushi",
        "**How to recognize:** Calls Shark **Sharky** a lot; frequent **tho, ugh, gotta, ain't, dammit, haha**. Chess comments are "
        "confident, direct and theory-heavy.\n"
        "**Languages:** Dutch, English.\n"
        "**ELO:** **2200**.\n"
        "**Chess style:** **DUBOV ITALIAN** is the enormous giveaway. Loves sharp theory, opening prep, gambits, sacrifices, "
        "attacking positions and practical clock play."
    ),
    "thejazzdude": (
        "Thejazzdude",
        "**How to recognize:** Friendly, relaxed, fuller sentences, naturally mixes Dutch and English, and unsurprisingly likes jazz.\n"
        "**Languages:** Dutch, English."
    ),
    "shark": (
        "Shark / Sharkmeister",
        "**How to recognize:** A completely unbiased description: **chess genius, absurd calculation, suspiciously frequent "
        "brilliancies and clearly the greatest mind ever to touch a chessboard.**\n"
        "**Languages:** Dutch, English, **fluent Italian, fluent German**, and **a few words of Polish**.\n"
        "**ELO:** **2200**.\n"
        "**Chess style:** Creative and tactical; likes flashy moves, attacking chances and finding brilliancies."
    ),
    "lars": (
        "Lars",
        "**How to recognize:** **German and a cheater.**\n"
        "**Languages:** German, English.\n"
        "**ELO:** **900–1700** *(cheater)*.\n"
        "**Chess style:** No strong stylistic fingerprint added yet; the clearest identifiers are Lars, German, and the cheating."
    ),
}

# Every spelling/nickname below resolves to the same profile.
# Non-alphanumeric characters are ignored, so !mr_thice == !mrthice.
PLAYER_INFO_ALIASES = {
    "az3d": "az",
    "az3d__": "az",
    "benniru": "ben",
    "gee": "geeflux",
    "flux": "geeflux",
    "georgeonzola": "george",
    "georgeonz0la": "george",
    "grumpy": "grumpymonk",
    "grumpymonk147": "grumpymonk",
    "jesse": "jessebrawlstars",
    "jessebrawl": "jessebrawlstars",
    "kurupttv": "kurupt",
    "martinxploz": "martin",
    "martin_xploz": "martin",
    "mh050131": "mh",
    "mh05": "mh",
    "moh": "mohammad",
    "moh979xx": "mohammad",
    "mohammad768": "mohammad",
    "mohammad_768": "mohammad",
    "thice": "mrthice",
    "thick": "mrthice",
    "mr_thice": "mrthice",
    "mrthick": "mrthice",
    "mr_thick": "mrthice",
    "nairya": "nairyaaa",
    "nairaa": "nairyaaa",
    "naiiiraaa": "nairyaaa",
    "notpabu": "pabu",
    "not_pabu": "pabu",
    "pandaro": "pandarou",
    "panda": "pandarou",
    "iampandaro": "pandarou",
    "pos": "pospos",
    "pospos12": "pospos",
    "rub": "rubriek",
    "sativa": "sativahibread",
    "hibread": "sativahibread",
    "screamingcat02n7": "screamingcat",
    "screamingcat_02n7": "screamingcat",
    "cat": "screamingcat",
    "sharkbest": "sh4rkmateisthebest",
    "sh4rkbest": "sh4rkmateisthebest",
    "sh4rkmatebest": "sh4rkmateisthebest",
    "sh4rkmate_is_the_best": "sh4rkmateisthebest",
    "adelson": "soyadelson",
    "soy": "soyadelson",
    "soyadelson7": "soyadelson",
    "stepu6568": "stepu",
    "tvoltios": "stepu",
    "t_voltios": "stepu",
    "isolatedsushi": "sushi",
    "isolatedsushi11": "sushi",
    "jazz": "thejazzdude",
    "jazzdude": "thejazzdude",
    "thejazzdude_": "thejazzdude",
    "sharkmeister": "shark",
    "sh4rkmate": "shark",
    "sharky": "shark",
    "lars11111": "lars",
}


def _player_info_key(value):
    return re.sub(r"[^a-z0-9]+", "", str(value).casefold())


# Normalize the aliases once so underscore/hyphen variants work automatically.
_NORMALIZED_PLAYER_ALIASES = {
    _player_info_key(alias): target
    for alias, target in PLAYER_INFO_ALIASES.items()
}


def player_info_for_command(command):
    if not command.startswith("!"):
        return None

    key = _player_info_key(command[1:])
    if not key:
        return None

    # Exact profile/alias first.
    exact_key = _NORMALIZED_PLAYER_ALIASES.get(key, key)
    exact = PLAYER_INFO.get(exact_key)
    if exact is not None:
        return exact

    # Also accept small spelling mistakes in names/nicknames. Keep very short
    # commands exact-only so normal bot commands are never accidentally matched.
    if len(key) < 4:
        return None

    candidates = sorted(
        set(PLAYER_INFO) | set(_NORMALIZED_PLAYER_ALIASES)
    )
    close = get_close_matches(
        key,
        candidates,
        n=1,
        cutoff=0.80,
    )
    if not close:
        return None

    matched_key = close[0]
    canonical = _NORMALIZED_PLAYER_ALIASES.get(
        matched_key,
        matched_key,
    )
    return PLAYER_INFO.get(canonical)


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

    Hard Mode keeps five poll options, hides the date, and is worth +2.
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


async def end_latest_orphaned_round(channel):
    """Best-effort close the newest open Guess poll after a controller restart."""
    async for recent in channel.history(limit=60):
        if (
            client.user is not None
            and recent.author.id != client.user.id
        ):
            continue

        round_type = _round_type_for_message(recent)
        if round_type is None or recent.poll is None:
            continue

        if not await _poll_is_open(recent, round_type):
            continue

        try:
            await recent.end_poll()
            print(
                f"Closed orphaned Guess {round_type} poll {recent.id} before manual !next.",
                flush=True,
            )
        except Exception as error:
            # Manual !next still proceeds. The controller has no active in-memory
            # round, so this poll belongs to an older/restarted process.
            print(
                f"Could not close orphaned Guess poll {recent.id}: {error}",
                flush=True,
            )

        return round_type

    return None


async def latest_round_type(
    channel
):
    """Return the newest Guess round type, even when its poll has ended."""
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
        if round_type is not None:
            return round_type

    return None



def _quote_hunt_text(value):
    text = " ".join(str(value or "").split())
    if not text:
        return None
    if len(text) > QUOTE_HUNT_MAX_LENGTH:
        return None
    return text


def _quote_hunt_candidates(chatters, avoid_recent=True):
    recent = set(QUOTE_HUNT_RECENT_KEYS) if avoid_recent else set()
    result = {}

    for username, entries in chatters.items():
        choices = []
        for quote, entry_date, quote_index in entries:
            text = _quote_hunt_text(quote)
            if text is None:
                continue
            key = f"{username}:{entry_date}:{quote_index}"
            if key in recent:
                continue
            choices.append(
                {
                    "username": username,
                    "text": text,
                    "date": entry_date,
                    "quote_index": quote_index,
                    "key": key,
                }
            )
        if choices:
            result[username] = choices

    return result


def build_quote_hunt_round(chatters):
    """Return a five-real-quote round or None when there is not enough data."""
    candidates = _quote_hunt_candidates(chatters, avoid_recent=True)
    if len(candidates) < 5:
        candidates = _quote_hunt_candidates(chatters, avoid_recent=False)
    if len(candidates) < 5:
        return None

    usernames = list(candidates)

    for _ in range(40):
        selected_users = random.sample(usernames, 5)
        target_username = random.choice(selected_users)
        selected = []
        used_texts = set()
        valid = True

        for username in selected_users:
            options = list(candidates[username])
            random.shuffle(options)
            picked = None
            for option in options:
                text_key = option["text"].casefold()
                if text_key not in used_texts:
                    picked = option
                    used_texts.add(text_key)
                    break
            if picked is None:
                valid = False
                break
            selected.append(picked)

        if not valid:
            continue

        random.shuffle(selected)
        correct_index = next(
            index
            for index, option in enumerate(selected)
            if option["username"] == target_username
        )

        for option in selected:
            QUOTE_HUNT_RECENT_KEYS.append(option["key"])
        if len(QUOTE_HUNT_RECENT_KEYS) > QUOTE_HUNT_RECENT_LIMIT:
            del QUOTE_HUNT_RECENT_KEYS[:-QUOTE_HUNT_RECENT_LIMIT]

        return target_username, selected, correct_index

    return None


async def post_quote_hunt(channel, chatters):
    global ROUND_ACTIVE
    global NEXT_REQUESTED

    built = build_quote_hunt_round(chatters)
    if built is None:
        return None

    target_username, options, correct_index = built
    target_name = display_name_for(target_username)
    correct_quote = options[correct_index]["text"]

    _set_private_guess_answer(
        "chatter",
        f"{target_name} — {correct_quote}",
    )

    poll = discord.Poll(
        question=f"Which message was written by {target_name}?",
        duration=timedelta(hours=1),
        multiple=False,
    )

    for option in options:
        poll.add_answer(text=option["text"])

    poll_message = await channel.send(
        content=(
            "🎭 **Guess the Chatter — QUOTE HUNT**\n\n"
            f"Which of these messages was really written by **{target_name}**?"
        ),
        poll=poll,
    )

    ROUND_ACTIVE = True
    NEXT_ROUND_EVENT.clear()

    try:
        await asyncio.wait_for(
            NEXT_ROUND_EVENT.wait(),
            timeout=(POLL_DURATION_MINUTES * 60 + 2),
        )
    except asyncio.TimeoutError:
        pass

    try:
        await poll_message.end_poll()
    except Exception as error:
        print(
            f"Quote Hunt poll end error: {error}",
            flush=True,
        )

    voters_by_answer = []
    poll_results_loaded = False
    try:
        finished_message = await channel.fetch_message(poll_message.id)
        finished_poll = (
            finished_message.poll
            if finished_message.poll is not None
            else poll
        )
        for answer in finished_poll.answers:
            answer_voters = []
            async for voter in answer.voters():
                if not voter.bot:
                    answer_voters.append(voter)
            voters_by_answer.append(answer_voters)
        poll_results_loaded = True
    except Exception as error:
        print(
            f"Quote Hunt poll result error: {error}",
            flush=True,
        )

    await channel.send(
        "🔓 **Quote Hunt answer**\n"
        f"**{target_name}** wrote:\n> {correct_quote}"
    )

    vote_records = []
    seen_vote_ids = set()
    for answer_index, answer_voters in enumerate(voters_by_answer):
        for voter in answer_voters:
            if voter.id in seen_vote_ids:
                continue
            seen_vote_ids.add(voter.id)
            vote_records.append(
                {
                    "user_id": voter.id,
                    "display_name": voter.display_name,
                    "correct": answer_index == correct_index,
                }
            )

    stats_result = None
    if vote_records:
        try:
            stats_result = await asyncio.to_thread(
                record_poll_votes,
                poll_message.id,
                vote_records,
                source="guess-chatter-quote-hunt",
                target_name=target_name,
            )
        except Exception as error:
            print(
                f"Quote Hunt stats error for poll {poll_message.id}: {error}",
                flush=True,
            )

    rewarded = []
    seen = set()
    if correct_index < len(voters_by_answer):
        for voter in voters_by_answer[correct_index]:
            if voter.bot or voter.id in seen:
                continue
            seen.add(voter.id)
            try:
                add_points(
                    voter.id,
                    voter.display_name,
                    1,
                    transaction_id=f"guess:{poll_message.id}:{voter.id}",
                    source="guess-chatter-quote-hunt",
                )
                rewarded.append(voter.display_name)
            except Exception as error:
                print(
                    f"Quote Hunt leaderboard error for {voter.display_name}: {error}",
                    flush=True,
                )

    if rewarded:
        await channel.send(
            "🎉 " + " • ".join(f"**{name} +1**" for name in rewarded)
        )
    elif poll_results_loaded:
        await channel.send(total_disaster_message())

    bonuses = (
        stats_result.get("_streak_bonuses", [])
        if isinstance(stats_result, dict)
        else []
    )
    if bonuses:
        await channel.send(
            "🔥 **Guess streak bonus!** "
            + " • ".join(
                f"**{item['display_name']} +1** for a {item['streak']}-streak"
                for item in bonuses
            )
        )

    ROUND_ACTIVE = False
    return NEXT_REQUESTED


async def post_guess(
    channel
):
    global ROUND_ACTIVE
    global NEXT_REQUESTED

    # Persistent controller can run many rounds in one process.
    NEXT_REQUESTED = False
    chatters, all_entries = load_chatters()

    mode = guess_special_mode()

    # Quote Hunt only replaces an ordinary Chatter round. Scheduled Hard and
    # Double Points rounds always keep their original behavior.
    if mode == "normal" and random.random() < QUOTE_HUNT_CHANCE:
        quote_hunt_result = await post_quote_hunt(
            channel,
            chatters,
        )
        if quote_hunt_result is not None:
            return quote_hunt_result

    # Hard Mode is difficult because the date is hidden, not because the poll
    # has fewer answers. All standard Chatter rounds keep five options.
    option_count = POLL_OPTIONS

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

    voters_by_answer = []
    poll_results_loaded = False
    try:

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

        poll_results_loaded = True

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

    # Record EVERY vote for !stats, including wrong answers. One poll/user
    # combination is stored only once, so retries can never duplicate stats.
    vote_records = []
    seen_vote_ids = set()

    for answer_index, answer_voters in enumerate(voters_by_answer):
        for voter in answer_voters:
            if voter.id in seen_vote_ids:
                continue

            seen_vote_ids.add(voter.id)
            vote_records.append(
                {
                    "user_id": voter.id,
                    "display_name": voter.display_name,
                    "correct": answer_index == correct_index,
                }
            )

    stats_result = None
    if vote_records:
        try:
            stats_result = await asyncio.to_thread(
                record_poll_votes,
                poll_message.id,
                vote_records,
                source=f"guess-chatter-{mode}",
                target_name=display_name_for(username),
            )
        except Exception as error:
            # Stats failure must never block the existing points/reveal flow.
            print(
                f"Guess stats error for poll {poll_message.id}: {error}",
                flush=True,
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
    elif poll_results_loaded:
        await channel.send(total_disaster_message())

    bonuses = (
        stats_result.get("_streak_bonuses", [])
        if isinstance(stats_result, dict)
        else []
    )
    if bonuses:
        await channel.send(
            "🔥 **Guess streak bonus!** "
            + " • ".join(
                f"**{item['display_name']} +1** for a {item['streak']}-streak"
                for item in bonuses
            )
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


async def start_round(
    channel,
    round_type,
    reason="schedule",
    ignore_discord_active=False,
):
    global CURRENT_ROUND_TYPE
    global FORCED_NEXT_TYPE
    global LAST_ROUND_TYPE
    global PENDING_START_TYPE

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

        # Scheduled starts stay fail-safe against a still-open Discord poll.
        # A manual idle !next first closes an orphaned poll best-effort and then
        # deliberately bypasses this history guard so a stale poll cannot block
        # the requested new game forever after a workflow restart.
        if not ignore_discord_active:
            active_type = await latest_active_round_type(channel)
            if active_type is not None:
                print(
                    f"Guess {round_type} skipped ({reason}): "
                    f"Discord already has active {active_type} round.",
                    flush=True,
                )
                return False

        CURRENT_ROUND_TYPE = round_type

        # The round has now genuinely started, so the startup-spam guard can
        # be released. !next during the active round is handled by
        # FORCED_NEXT_TYPE as before.
        if PENDING_START_TYPE == round_type:
            PENDING_START_TYPE = None

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
            LAST_ROUND_TYPE = round_type
            CURRENT_ROUND_TYPE = None
            NEXT_ROUND_EVENT.clear()
            _clear_private_guess_answer()

        forced = FORCED_NEXT_TYPE
        FORCED_NEXT_TYPE = None

    if forced is not None:
        # Let the answer/reward messages settle before the next poll appears.
        await asyncio.sleep(2)
        queue_round_start(
            channel,
            forced,
            reason="!next",
            ignore_discord_active=True,
        )

    return True


async def start_round_with_retry(
    channel,
    round_type,
    reason="!next",
    attempts=8,
    ignore_discord_active=False,
):
    """Start a requested round, retrying through brief Discord poll-state lag."""
    for attempt in range(attempts):
        # If another task already started a round, the user already got a new game.
        if CURRENT_ROUND_TYPE is not None:
            return True

        started = await start_round(
            channel,
            round_type,
            reason=reason,
            ignore_discord_active=ignore_discord_active,
        )
        if started:
            return True

        if attempt < attempts - 1:
            await asyncio.sleep(2)

    print(
        f"Guess {round_type} could not start after {attempts} attempts ({reason}).",
        flush=True,
    )
    return False


def queue_round_start(
    channel,
    round_type,
    reason="!next",
    ignore_discord_active=False,
):
    """Queue exactly one pending Guess start during the pre-start gap."""
    global PENDING_START_TYPE

    if CURRENT_ROUND_TYPE is not None:
        return False

    if PENDING_START_TYPE is not None:
        return False

    PENDING_START_TYPE = round_type

    async def runner():
        global PENDING_START_TYPE

        try:
            await start_round_with_retry(
                channel,
                round_type,
                reason=reason,
                ignore_discord_active=ignore_discord_active,
            )
        finally:
            # If every retry failed before a round could claim the pending
            # start, release the guard so a later !next can try again.
            if (
                CURRENT_ROUND_TYPE is None
                and PENDING_START_TYPE == round_type
            ):
                PENDING_START_TYPE = None

    asyncio.create_task(
        runner()
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


def _guess_shop_asset_from_text(text, owned_badges):
    raw = str(text or "").strip()
    try:
        amount = round(float(raw), 3)
        if amount > 0 and " " not in raw:
            return {"type": "coins", "amount": amount}
    except Exception:
        pass
    badge = shared_resolve_badge(raw, owned_badges)
    return {"type": "badge", "badge": badge}


async def _guess_target_identity(message, typed_name):
    query = str(typed_name or "").strip()
    if not query:
        raise ValueError("Player name is empty.")
    try:
        target = await asyncio.to_thread(shared_resolve_cosmetic_profile, query)
        return str(target["user_id"]), target.get("name", query)
    except Exception:
        pass
    query_key = query.casefold()
    for member in getattr(message.guild, "members", []):
        names = {
            str(getattr(member, "display_name", "")).casefold(),
            str(getattr(member, "name", "")).casefold(),
            str(getattr(member, "global_name", "") or "").casefold(),
        }
        if query_key in names:
            return str(member.id), member.display_name
    raise ValueError(f"No player named '{query}' was found.")


async def _guess_parse_donation_args(message, arg_text):
    words = str(arg_text or "").split()
    if len(words) < 2:
        raise ValueError("Usage: `!donate <name> <coins|badge>`")
    sender_profile = await asyncio.to_thread(
        get_cosmetic_profile, message.author.id, message.author.display_name
    )
    if message.mentions:
        target = message.mentions[0]
        mention_forms = {f"<@{target.id}>", f"<@!{target.id}>"}
        remaining = [word for word in words if word not in mention_forms]
        if not remaining:
            raise ValueError("Add coins or a badge after the player name.")
        asset = _guess_shop_asset_from_text(" ".join(remaining), sender_profile.get("badges", []))
        return str(target.id), target.display_name, asset

    candidates = []
    seen = set()
    for split in range(1, len(words)):
        try:
            target_id, target_name = await _guess_target_identity(message, " ".join(words[:split]))
            asset = _guess_shop_asset_from_text(" ".join(words[split:]), sender_profile.get("badges", []))
        except Exception:
            continue
        key = (str(target_id), asset["type"], str(asset.get("amount", asset.get("badge", ""))))
        if key not in seen:
            seen.add(key)
            candidates.append((target_id, target_name, asset))
    if not candidates:
        raise ValueError("Could not match that player + coins/badge. Use the exact badge emoji/name if needed.")
    if len(candidates) > 1:
        raise ValueError("That donation is ambiguous. Mention the player or use the exact badge emoji.")
    return candidates[0]


async def _guess_parse_trade_args(message, arg_text):
    words = str(arg_text or "").split()
    if len(words) < 3:
        raise ValueError("Usage: `!trade <name> <give coins/badge> <receive coins/badge>`")
    sender_profile = await asyncio.to_thread(
        get_cosmetic_profile, message.author.id, message.author.display_name
    )
    target_candidates = []
    if message.mentions:
        target = message.mentions[0]
        mention_forms = {f"<@{target.id}>", f"<@!{target.id}>"}
        remaining = [word for word in words if word not in mention_forms]
        target_candidates.append((str(target.id), target.display_name, remaining))
    else:
        for target_split in range(1, len(words) - 1):
            try:
                target_id, target_name = await _guess_target_identity(message, " ".join(words[:target_split]))
            except Exception:
                continue
            target_candidates.append((target_id, target_name, words[target_split:]))

    parsed = []
    seen = set()
    for target_id, target_name, remaining in target_candidates:
        if str(target_id) == str(message.author.id) or len(remaining) < 2:
            continue
        target_profile = await asyncio.to_thread(get_cosmetic_profile, target_id, target_name)
        for split in range(1, len(remaining)):
            try:
                offer = _guess_shop_asset_from_text(" ".join(remaining[:split]), sender_profile.get("badges", []))
                request = _guess_shop_asset_from_text(" ".join(remaining[split:]), target_profile.get("badges", []))
            except Exception:
                continue
            key = (
                str(target_id), offer["type"], str(offer.get("amount", offer.get("badge", ""))),
                request["type"], str(request.get("amount", request.get("badge", ""))),
            )
            if key not in seen:
                seen.add(key)
                parsed.append((target_id, target_name, offer, request))
    if not parsed:
        raise ValueError("Could not understand that trade. Only coins and badges can be traded.")
    if len(parsed) > 1:
        raise ValueError("That trade is ambiguous. Mention the player and/or use exact badge emojis.")
    return parsed[0]


def guess_pending_trade_message(profile):
    pending = profile.get("pending_trade") if isinstance(profile, dict) else None
    if not pending:
        return "🤝 **No pending trade.**"
    return (
        f"🤝 **Pending trade from {pending.get('from_name', 'Unknown')}**\n"
        f"They give you: **{shared_format_trade_asset(pending['offer'])}**\n"
        f"They want: **{shared_format_trade_asset(pending['request'])}**\n\n"
        "Use `!accepttrade` or `!declinetrade`."
    )


async def command_handler(message):
    global NEXT_REQUESTED
    global FORCED_NEXT_TYPE

    if (
        message.author.bot
        or message.channel.id != CHANNEL_ID
    ):
        return

    raw_command = message.content.strip()
    command = raw_command.casefold()

    if command in {"!next", "!n", "n"}:
        if CURRENT_ROUND_TYPE is None:
            # A previous idle !next may already have queued a start but the
            # task has not yet reached CURRENT_ROUND_TYPE. Ignore duplicate
            # clicks during that tiny window instead of posting/queuing twice.
            if PENDING_START_TYPE is not None:
                return

            # After a workflow restart Discord can still contain the old poll
            # even though this controller has no active in-memory round. Close
            # that orphan best-effort and use it as the previous round type.
            orphan_type = await end_latest_orphaned_round(
                message.channel
            )

            last_type = orphan_type or LAST_ROUND_TYPE
            if last_type is None:
                last_type = await latest_round_type(
                    message.channel
                )

            target_type = (
                "chess"
                if last_type == "chatter"
                else "chatter"
                if last_type == "chess"
                else scheduled_round_type(
                    next_ten_minute_slot()
                ) or "chatter"
            )

            queued = queue_round_start(
                message.channel,
                target_type,
                reason="!next-idle",
                ignore_discord_active=True,
            )
            if not queued:
                return

            # Confirm only after the new round has actually claimed the
            # controller state. This prevents the old false-positive message
            # where the bot said it was starting but history blocking stopped it.
            started = False
            for _ in range(30):
                await asyncio.sleep(0.1)
                if CURRENT_ROUND_TYPE == target_type:
                    started = True
                    break
                if PENDING_START_TYPE is None:
                    break

            if started:
                await message.channel.send(
                    f"⏭️ **Starting Guess the "
                    f"{'Chatter' if target_type == 'chatter' else 'Chess Chatter'} now.**"
                )
            else:
                await message.channel.send(
                    "❌ **Could not start the requested Guess round.** "
                    "Try `!next` once more."
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

    if command in {"!coins", "!bank"}:
        try:
            points, coins = await asyncio.gather(
                asyncio.to_thread(guess_get_score, message.author.id),
                asyncio.to_thread(shared_get_coins, message.author.id),
            )
            await message.channel.send(
                f"🏆 Guess Points: **{shared_format_points(points)}**\n"
                f"🪙 Shared Coins: **{shared_format_points(coins)}**"
            )
        except Exception as error:
            await message.channel.send(f"❌ **Could not read your bank:** `{str(error)[:700]}`")
        return

    if command == "!donate" or command.startswith("!donate "):
        arg_text = raw_command[len("!donate"):].strip()
        try:
            target_user_id, target_name, asset = await _guess_parse_donation_args(message, arg_text)
        except ValueError as error:
            await message.channel.send(f"❌ **{error}**")
            return
        if str(target_user_id) == str(message.author.id):
            await message.channel.send("❌ You cannot donate to yourself.")
            return
        try:
            if asset["type"] == "coins":
                result = await asyncio.to_thread(
                    transfer_coins, message.author.id, message.author.display_name,
                    target_user_id, target_name, asset["amount"],
                    f"coin-donate:{message.id}:{message.author.id}:{target_user_id}",
                    source="guess-donation",
                )
                await message.channel.send(
                    f"🪙 **{message.author.display_name} donated {shared_format_points(asset['amount'])} coins to {target_name}.**\n"
                    f"Your coins: **{shared_format_points(result['sender_coins'])}**"
                )
            else:
                await asyncio.to_thread(
                    transfer_badge, message.author.id, message.author.display_name,
                    target_user_id, target_name, asset["badge"],
                    f"badge-donate:{message.id}:{message.author.id}:{target_user_id}",
                    source="guess-badge-donation",
                )
                await message.channel.send(
                    f"🎁 **{message.author.display_name} donated {asset['badge']} to {target_name}.**"
                )
        except ValueError as error:
            await message.channel.send(f"❌ **{error}**")
        except Exception as error:
            await message.channel.send(f"❌ Could not safely donate: `{str(error)[:700]}`")
        return

    if command == "!trade" or command.startswith("!trade "):
        arg_text = raw_command[len("!trade"):].strip()
        try:
            target_user_id, target_name, offer, request = await _guess_parse_trade_args(message, arg_text)
            await asyncio.to_thread(
                shared_propose_trade, message.author.id, message.author.display_name,
                target_user_id, target_name, offer, request,
                f"trade-propose:{message.id}:{message.author.id}:{target_user_id}",
            )
        except ValueError as error:
            await message.channel.send(f"❌ **{error}**")
            return
        except Exception as error:
            await message.channel.send(f"❌ Could not safely create trade: `{str(error)[:700]}`")
            return
        await message.channel.send(
            f"🤝 **Trade offer for {target_name}**\n"
            f"{message.author.display_name} gives: **{shared_format_trade_asset(offer)}**\n"
            f"{message.author.display_name} receives: **{shared_format_trade_asset(request)}**\n"
            f"{target_name}: use `!accepttrade` or `!declinetrade`."
        )
        return

    if command in {"!pendingtrade", "!pending trade"}:
        try:
            profile = await asyncio.to_thread(
                get_cosmetic_profile, message.author.id, message.author.display_name
            )
            await message.channel.send(guess_pending_trade_message(profile))
        except Exception as error:
            await message.channel.send(f"❌ **Could not read pending trade:** `{str(error)[:700]}`")
        return

    if command in {"!accepttrade", "!accept trade"}:
        try:
            details = await asyncio.to_thread(
                shared_accept_trade, message.author.id, message.author.display_name,
                f"trade-accept:{message.id}:{message.author.id}",
            )
        except ValueError as error:
            await message.channel.send(f"❌ **{error}**")
            return
        except Exception as error:
            await message.channel.send(f"❌ Could not safely accept trade: `{str(error)[:700]}`")
            return
        await message.channel.send(
            f"✅ **Trade accepted!**\n"
            f"{message.author.display_name} received **{shared_format_trade_asset(details['offer'])}**.\n"
            f"{details.get('from_name', 'Other player')} received **{shared_format_trade_asset(details['request'])}**."
        )
        return

    if command in {"!declinetrade", "!decline trade"}:
        try:
            pending = await asyncio.to_thread(
                shared_decline_trade, message.author.id, message.author.display_name,
                f"trade-decline:{message.id}:{message.author.id}",
            )
        except ValueError as error:
            await message.channel.send(f"❌ **{error}**")
            return
        except Exception as error:
            await message.channel.send(f"❌ Could not safely decline trade: `{str(error)[:700]}`")
            return
        await message.channel.send(
            f"❌ **Trade declined.** Offer from {pending.get('from_name', 'Unknown')} was removed."
        )
        return

    if command in {"!shop", "!shop badge", "!shop badges"}:
        profile = await asyncio.to_thread(
            get_cosmetic_profile,
            message.author.id,
            message.author.display_name,
        )
        await message.channel.send(
            "🛍️ **Guess Shop**\n"
            f"🪙 Coins: **{shared_format_points(profile.get('coins', 0))}**  •  `!coins` / `!bank`\n\n"
            f"🎁 **Badge Box — {shared_format_points(BADGE_BOX_COST)} coins**: `!box`\n"
            "💸 `!donate <name> <coins|badge>` • 🤝 `!trade <name> <give> <receive>`\n"
            f"🎨 **Boards — {shared_format_points(BOARD_COST)} coins each**: `!customboard`\n"
            f"♟️ **Piece Sets — {shared_format_points(PIECE_COST)} coins each**: `!custompiece`\n\n"
            "Boards/pieces use the same shared inventory as Puzzle. They apply to your Puzzle games and, if you are captain, your Survival run."
        )
        return

    if command in {"!shop box", "!box"}:
        try:
            result = await asyncio.to_thread(
                buy_badge_box,
                message.author.id,
                message.author.display_name,
                f"guess-badge-box:{message.id}",
            )
        except ValueError as error:
            await message.channel.send(f"❌ **{error}**")
            return
        except Exception as error:
            print(f"Guess badge box error: {error}", flush=True)
            await message.channel.send("❌ **Could not safely open the badge box. Try again later.**")
            return

        await message.channel.send(
            "🎁 **Mystery Badge Box opened!**\n"
            f"You got {result['badge']} **{result['rarity_label']}**\n"
            f"🪙 Coins left: **{shared_format_points(result['coins'])}**"
        )
        return

    if command == "!customboard" or command.startswith("!customboard "):
        args = raw_command.split()[1:]
        if not args:
            try:
                await send_guess_catalog_preview(message, "board", 1)
            except Exception as error:
                await message.channel.send(f"❌ **Could not open board previews:** `{str(error)[:800]}`")
            return
        if len(args) == 1 and args[0].isdigit():
            page = int(args[0])
            try:
                await send_guess_catalog_preview(message, "board", page)
            except Exception as error:
                await message.channel.send(f"❌ **Could not open board previews:** `{str(error)[:800]}`")
            return
        board_name = args[0].casefold()
        if board_name == "default":
            board_name = "classic"
        if board_name not in BOARD_THEMES:
            await message.channel.send("❌ **Unknown board theme. Use `!customboard` for the catalogue.**")
            return
        action = args[1].casefold() if len(args) > 1 else "equip"
        if action == "test":
            profile = await asyncio.to_thread(get_cosmetic_profile, message.author.id, message.author.display_name)
            piece_name = profile.get("active_piece", "classic")
            file = await asyncio.to_thread(guess_cosmetic_preview_file, board_name, piece_name, "guess_board_preview.png")
            await message.channel.send(
                f"🎨 **{BOARD_DISPLAY_NAMES[board_name]} preview** • Pieces: **{PIECE_DISPLAY_NAMES.get(piece_name, 'Classic')}**",
                file=file,
            )
            return
        if action == "buy":
            if board_name == "classic":
                await message.channel.send("✅ **Classic is free.**")
                return
            try:
                profile = await asyncio.to_thread(
                    buy_board, message.author.id, message.author.display_name, board_name,
                    f"guess-buy-board:{message.id}:{message.author.id}:{board_name}",
                )
                await message.channel.send(
                    f"✅ Bought **{BOARD_DISPLAY_NAMES[board_name]}** for **{shared_format_points(BOARD_COST)} coins**.\n"
                    f"🪙 Coins left: **{shared_format_points(profile['coins'])}**\n"
                    f"Equip it with `!customboard {board_name}`."
                )
            except Exception as error:
                await message.channel.send(f"❌ **Could not buy board:** {str(error)[:800]}")
            return
        try:
            profile = await asyncio.to_thread(
                equip_board, message.author.id, message.author.display_name, board_name,
                f"guess-equip-board:{message.id}:{message.author.id}:{board_name}",
            )
            await message.channel.send(f"🎨 **Board equipped:** {BOARD_DISPLAY_NAMES[profile['active_board']]}")
        except Exception as error:
            await message.channel.send(f"❌ **Could not equip board:** {str(error)[:800]}")
        return

    if command == "!custompiece" or command.startswith("!custompiece "):
        args = raw_command.split()[1:]
        if not args:
            try:
                await send_guess_catalog_preview(message, "piece", 1)
            except Exception as error:
                await message.channel.send(f"❌ **Could not open piece previews:** `{str(error)[:800]}`")
            return
        if len(args) == 1 and args[0].isdigit():
            page = int(args[0])
            try:
                await send_guess_catalog_preview(message, "piece", page)
            except Exception as error:
                await message.channel.send(f"❌ **Could not open piece previews:** `{str(error)[:800]}`")
            return
        piece_name = args[0].casefold()
        if piece_name == "default":
            piece_name = "classic"
        if piece_name not in PIECE_SETS:
            await message.channel.send("❌ **Unknown piece set. Use `!custompiece` for the catalogue.**")
            return
        action = args[1].casefold() if len(args) > 1 else "equip"
        if action == "test":
            profile = await asyncio.to_thread(get_cosmetic_profile, message.author.id, message.author.display_name)
            board_name = profile.get("active_board", "classic")
            file = await asyncio.to_thread(guess_cosmetic_preview_file, board_name, piece_name, "guess_piece_preview.png")
            await message.channel.send(
                f"♟️ **{PIECE_DISPLAY_NAMES[piece_name]} preview** • Board: **{BOARD_DISPLAY_NAMES.get(board_name, 'Classic')}**",
                file=file,
            )
            return
        if action == "buy":
            if piece_name == "classic":
                await message.channel.send("✅ **Classic is free.**")
                return
            try:
                profile = await asyncio.to_thread(
                    buy_piece, message.author.id, message.author.display_name, piece_name,
                    f"guess-buy-piece:{message.id}:{message.author.id}:{piece_name}",
                )
                await message.channel.send(
                    f"✅ Bought **{PIECE_DISPLAY_NAMES[piece_name]}** for **{shared_format_points(PIECE_COST)} coins**.\n"
                    f"🪙 Coins left: **{shared_format_points(profile['coins'])}**\n"
                    f"Equip it with `!custompiece {piece_name}`."
                )
            except Exception as error:
                await message.channel.send(f"❌ **Could not buy piece set:** {str(error)[:800]}")
            return
        try:
            profile = await asyncio.to_thread(
                equip_piece, message.author.id, message.author.display_name, piece_name,
                f"guess-equip-piece:{message.id}:{message.author.id}:{piece_name}",
            )
            await message.channel.send(f"♟️ **Piece set equipped:** {PIECE_DISPLAY_NAMES[profile['active_piece']]}")
        except Exception as error:
            await message.channel.send(f"❌ **Could not equip piece set:** {str(error)[:800]}")
        return

    if command in {"!me", "!profile"}:
        text = await asyncio.to_thread(
            guess_cosmetic_profile_dashboard,
            message.author.id,
            message.author.display_name,
        )
        await message.channel.send(text, view=GuessCosmeticProfileView(
            message.author.id, message.author.id, message.author.display_name, editable=True
        ))
        return

    if command in {"!me badges", "!profile badges"}:
        text = await asyncio.to_thread(
            guess_badge_overview,
            message.author.id,
            message.author.display_name,
        )
        await message.channel.send(text, view=GuessCosmeticProfileView(
            message.author.id, message.author.id, message.author.display_name, editable=True
        ))
        return

    if command.startswith("!me badges ") or command.startswith("!profile badges "):
        parts = raw_command.split()
        rarity = parts[2].casefold() if len(parts) > 2 else ""
        page = int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else 1
        try:
            text = await asyncio.to_thread(
                guess_badge_page,
                message.author.id,
                message.author.display_name,
                rarity,
                page,
            )
        except ValueError:
            await message.channel.send("❌ **Use Legendary, Epic, Rare, Uncommon, Common or Basic.**")
            return
        await message.channel.send(text, view=GuessCosmeticProfileView(message.author.id, message.author.id, message.author.display_name, editable=True))
        return

    if command.startswith("!profile badge ") or command.startswith("!me badge "):
        raw_index = raw_command.split()[-1]
        try:
            index = int(raw_index)
        except ValueError:
            await message.channel.send("❌ **Use a badge number from `!profile`.**")
            return

        profile = await asyncio.to_thread(
            get_cosmetic_profile,
            message.author.id,
            message.author.display_name,
        )
        badges = list(profile.get("badges", []))
        if index == 0:
            try:
                await asyncio.to_thread(
                    equip_badge, message.author.id, message.author.display_name, "",
                    f"guess-equip-badge:{message.id}:0",
                )
            except Exception as error:
                print(f"Guess unequip badge error: {error}", flush=True)
                await message.channel.send("❌ **Could not safely unequip your badge.**")
                return
            await message.channel.send("🏅 **Badge unequipped.**")
            return
        if index < 1 or index > len(badges):
            await message.channel.send("❌ **That badge number is not in your inventory. Use `!profile badge 0` for no badge.**")
            return

        badge = badges[index - 1]
        try:
            await asyncio.to_thread(
                equip_badge,
                message.author.id,
                message.author.display_name,
                badge,
                f"guess-equip-badge:{message.id}",
            )
        except Exception as error:
            print(f"Guess equip badge error: {error}", flush=True)
            await message.channel.send("❌ **Could not safely equip that badge.**")
            return
        await message.channel.send(f"🏅 **Equipped:** {badge}")
        return

    if command.startswith("!me boards") or command.startswith("!profile boards"):
        parts = raw_command.split()
        page = int(parts[-1]) if parts[-1].isdigit() else 1
        text = await asyncio.to_thread(guess_board_page, message.author.id, message.author.display_name, page)
        await message.channel.send(text, view=GuessCosmeticProfileView(message.author.id, message.author.id, message.author.display_name, editable=True))
        return

    if command.startswith("!me pieces") or command.startswith("!profile pieces"):
        parts = raw_command.split()
        page = int(parts[-1]) if parts[-1].isdigit() else 1
        text = await asyncio.to_thread(guess_piece_page, message.author.id, message.author.display_name, page)
        await message.channel.send(text, view=GuessCosmeticProfileView(message.author.id, message.author.id, message.author.display_name, editable=True))
        return

    if command.startswith("!profile board ") or command.startswith("!me board "):
        board_name = raw_command.split()[-1].casefold()
        if board_name == "default":
            board_name = "classic"
        try:
            profile = await asyncio.to_thread(
                equip_board, message.author.id, message.author.display_name, board_name,
                f"guess-profile-board:{message.id}:{message.author.id}:{board_name}",
            )
            await message.channel.send(f"🎨 **Board equipped:** {BOARD_DISPLAY_NAMES[profile['active_board']]}")
        except Exception as error:
            await message.channel.send(f"❌ **Could not equip board:** {str(error)[:800]}")
        return

    if command.startswith("!profile piece ") or command.startswith("!me piece "):
        piece_name = raw_command.split()[-1].casefold()
        if piece_name == "default":
            piece_name = "classic"
        try:
            profile = await asyncio.to_thread(
                equip_piece, message.author.id, message.author.display_name, piece_name,
                f"guess-profile-piece:{message.id}:{message.author.id}:{piece_name}",
            )
            await message.channel.send(f"♟️ **Piece set equipped:** {PIECE_DISPLAY_NAMES[profile['active_piece']]}")
        except Exception as error:
            await message.channel.send(f"❌ **Could not equip piece set:** {str(error)[:800]}")
        return

    if command.startswith("!profile "):
        requested_name = raw_command[len("!profile"):].strip()
        if requested_name:
            try:
                if message.mentions:
                    target = message.mentions[0]
                    target_id, target_name = str(target.id), target.display_name
                else:
                    target_id, target_name = await _guess_target_identity(message, requested_name)
                text = await asyncio.to_thread(guess_cosmetic_profile_dashboard, target_id, target_name)
                view = GuessCosmeticProfileView(
                    message.author.id, target_id, target_name, editable=(str(target_id) == str(message.author.id))
                )
                await message.channel.send(text, view=view)
            except Exception as error:
                await message.channel.send(f"❌ **Profile not found:** {str(error)[:800]}")
            return

    if command == "!stats" or command.startswith("!stats "):
        if command == "!stats":
            stats = await asyncio.to_thread(
                guess_stats_for_user,
                message.author.id,
                message.author.display_name,
            )
        else:
            requested_name = raw_command[len("!stats"):].strip()

            # Mentions are the most reliable way to identify another Discord user.
            if message.mentions:
                target = message.mentions[0]
                stats = await asyncio.to_thread(
                    guess_stats_for_user,
                    target.id,
                    target.display_name,
                )
            elif requested_name:
                stats = await asyncio.to_thread(
                    guess_stats_for_name,
                    requested_name,
                )
            else:
                stats = None

            if stats is None:
                await message.channel.send(
                    f"❌ **No Guess stats found for `{requested_name}` yet.**"
                )
                return

        try:
            cosmetic = await asyncio.to_thread(
                get_cosmetic_profile,
                stats.get("user_id"),
                stats.get("name", "Unknown"),
            )
            stats = dict(stats)
            stats["active_badge"] = cosmetic.get("active_badge", "")
        except Exception as error:
            print(f"Guess stats badge lookup error: {error}", flush=True)

        await message.channel.send(format_guess_stats(stats))
        return

    if command in {"!leaderboard", "!lb", "!l"}:
        leaderboard_text = await asyncio.to_thread(
            full_leaderboard,
            "🏆 **Guess Games Leaderboard**",
            True,
        )
        await message.channel.send(
            leaderboard_text,
            allowed_mentions=discord.AllowedMentions.none(),
        )
        return

    if command in {"!help", "!info", "!i"}:
        await message.channel.send(
            "🧠 **Guess Games**\n\n"
            "💬 **Guess the Chatter** — guess who wrote the real chat message.\n"
            "♟️ **Guess the Chess Chatter** — browse a real Chess.com game and guess the player.\n\n"
            "⏭️ `n` / `!n` / `!next` — end/reveal the current round and start the other Guess game.\n"
            "🏆 `!l` — Guess leaderboard. `!stats` / `!stats <name>` — Guess stats.\n"
            "🪙 Every Guess point also gives the same amount of shared coins. `!coins` / `!bank` shows both.\n"
            "💸 `!donate <name> <coins|badge>` — donate coins or a badge. `!trade <name> <give> <receive>` — trade coins/badges. `!pendingtrade` repeats your pending offer.\n"
            "👤 `!me` / `!profile` — clickable cosmetic profile. `!profile <name>` — view another player.\n\n"
            f"🎁 `!box` — badge box (**{shared_format_points(BADGE_BOX_COST)} coins**).\n"
            f"🎨 `!customboard` — **{len(BOARD_THEMES)}** boards (**{shared_format_points(BOARD_COST)} coins** each).\n"
            f"♟️ `!custompiece` — **{len(PIECE_SETS)}** piece sets (**{shared_format_points(PIECE_COST)} coins** each).\n"
            "🛍️ `!shop` — full Guess shop instructions.\n\n"
            "`!<name>` — recognition info for a Guess Chatter / Chess Chatter player."
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

    try:
        migration = await asyncio.to_thread(
            backfill_existing_guess_points_to_shared_coins
        )
        print(
            "Guess shared-coin backfill: "
            f"{migration.get('users', 0)} users, "
            f"{migration.get('credited', 0)} coins credited.",
            flush=True,
        )
    except Exception as error:
        # Never block the Guess scheduler if the one-time wallet backfill has
        # a temporary repository/network problem. Future point awards safely
        # retry the per-user watermark sync.
        print(f"Guess shared-coin backfill warning: {error}", flush=True)

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
