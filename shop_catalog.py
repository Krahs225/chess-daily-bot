"""Shared cosmetic shop catalogue for the Puzzle + Guess bots."""

SHOP_BUILD = "cosmetics-shop-v1-2026-09-05"

BADGE_BOX_COST = 50.0
BOARD_COST = 100.0
SURVIVAL_HEART_COST = 100.0
COLOR_COST = 500.0

BADGE_RARITY_WEIGHTS = {
    "legendary": 1,
    "epic": 4,
    "rare": 10,
    "uncommon": 20,
    "common": 30,
    "basic": 35,
}

# Legendary is intentionally reserved for Shark's own Discord server emotes.
BADGE_POOLS = {
    "legendary": [
        "<:BIGBRAIN:1525486567219531777>",
        "<:BITS:1525487690554933378>",
        "<:BLUNDER:1525486089744154684>",
        "<:BRILLIANT:1525486133172240566>",
        "<:BRILLIANTBLUNDER:1525486423593975869>",
        "<:CONFUSED:1525486915103621150>",
        "<:COOKING:1525487725954728156>",
        "<:CRY:1525487422681518200>",
        "<:DANCE:1525487158729769010>",
        "<:DETECTIVE:1525486511620096100>",
        "<:DUBOVITALIAN:1525487113573896302>",
        "<:EZ:1525486209881735189>",
        "<:FACEPALM:1525486622873882644>",
        "<:GG:1525487789095784692>",
        "<:GUESSING:1525487842204057620>",
        "<:HEART:1525487524301242489>",
        "<:HYPE:1525487466654728242>",
        "<:INTERESTING:1525486348423659553>",
        "<:LAUGHING:1525487355031457864>",
        "<:PANIC:1525487257157500978>",
        "<:SCARED:1525487015724974172>",
        "<:SH4RKMATE:1525488071158665268>",
        "<:SLEEP:1525486795855495208>",
        "<:STOP:1525487597110165504>",
        "<:SUBSCRIBE:1525487952187228281>",
        "<:WAVE:1525487900924575784>",
    ],
    "epic": "👑 🐐 🦄 🐲 🐦‍🔥".split(),
    "rare": "⭐ 💎 🔥 🌈 ⚡ 💀 ☠️ 🤖 🗿 🚀 🛸 🔱 👾 🦈 🧙 🧬 🦋 🦕 🦉".split(),
    "uncommon": """
🏆 🥇 🏅 🎖️ ✨ ☄️ 🌌 🪐 🔮 🪄 🥷 🧛 👻 👽 🦁 🐯 🐺 🦅 🦂 🐍 🦇 🦖 🦚
🦍 🦧 🐆 🐊 🐋 🐬 🐙 🦑 🦞 🦀 🐈‍⬛ 🐻‍❄️ ♟️ 🎯 🎰 🕹️ 🎮 🎲 🧩 ⚔️ 🛡️ 🏹 🗡️ 💣 🧨
⛓️ 🧲 🔑 🗝️ 🔏 👀 👁️
""".split(),
    "common": """
🧠 ❤️ 🧡 💛 💚 💙 💜 🖤 🤍 🤎 🩷 🩵 🩶 💔 💕 💞 💓 💗 💝 💘
☀️ 🌕 🌑 🌙 ☁️ 🌧️ 🌨️ 🌩️ ❄️ ☂️ 🌊 🌀 🌫️ 🌪️ 🌹 🌻 🌷 🌺 🌸 🪷 🍀 🌵 🌴 🌲 🌳 🍄
🦊 🦝 🐼 🐨 🐻 🐩 🐈 🐕 🐇 🐹 🐁 🐀 🦔 🦡 🦦 🦫 🦥 🦘 🐪 🦙 🦒 🐘 🦏 🦛
🦜 🦩 🦢 🐧 🦆 🐔 🐓 🦃 🐝 🐞 🪲 🐛 🐜 🕷️ 🐌 🪱 🐟 🐠 🐡 🦐 🦭 🐢 🐸
""".split(),
    "basic": """
🍎 🍏 🍐 🍊 🍋 🍋‍🟩 🍌 🍉 🍇 🍓 🫐 🍈 🍒 🍑 🥭 🍍 🥝 🍅 🥑 🫒 🥥
🥕 🌽 🌶️ 🫑 🥒 🥬 🥦 🧄 🧅 🥔 🍠 🫘 🌰 🥜 🍞 🥐 🥖 🫓 🥨 🥯 🥞 🧇 🧀
🍗 🍖 🌭 🍔 🍟 🍕 🥪 🥙 🧆 🌮 🌯 🫔 🥗 🥘 🫕 🥫 🍝 🍜 🍲 🍛 🍣 🍱 🥟 🦪 🍤 🍙 🍚 🍘 🍥
🍦 🍧 🍨 🍩 🍪 🎂 🍰 🧁 🥧 🍫 🍬 🍭 🍮 🍯 ☕ 🍵 🫖 🥤 🧋 🧃 🥛
⌚ 📱 💻 ⌨️ 🖥️ 🖨️ 🖱️ 💽 💾 💿 📀 📷 📸 📹 🎥 📺 📻 🎙️ 🎚️ 🎛️ ☎️ 📞 📟 📠
🔋 🪫 🔌 💡 🔦 🕯️ 📚 📖 📝 ✏️ 🖊️ 🖋️ 🖌️ 🖍️ 📌 📍 📎 🖇️ 📏 📐 ✂️ 📦 📫 📬 ✉️ 💌
🧹 🧺 🧻 🪣 🧼 🫧 🧽 🪑 🛏️ 🛋️ 🚪 🪞 🪟 ⏰ ⌛ ⏳ ⏱️ ⏲️ 🔧 🔨 ⚒️ 🛠️ ⛏️ 🪚 🔩 ⚙️ 🧱 🪨 🪵
⚽ 🏀 🏈 ⚾ 🥎 🎾 🏐 🏉 🥏 🎱 🪀 🏓 🏸 🏒 🏑 🥍 🏏 ⛳ 🪁 🛝 🛼 🛹 ⛸️ 🎿 🎣 🤿 🥊 🥋
🎨 🧵 🪡 🧶 🎭 🎤 🎧 🎷 🎸 🎹 🥁 🎺 🎻
🚗 🚕 🚙 🚌 🚎 🏎️ 🚓 🚑 🚒 🚐 🛻 🚚 🚛 🚜 🏍️ 🛵 🚲 🛴 🚂 🚆 🚇 🚊 🚉 ✈️ 🛫 🛬 🚁 ⛵ 🚤 🛥️ 🚢
✅ ❌ ❗ ❓ ⭕ ❎ ➕ ➖ ➗ ✖️ ⬆️ ⬇️ ⬅️ ➡️ ↗️ ↘️ ↙️ ↖️
🔴 🟠 🟡 🟢 🔵 🟣 ⚫ ⚪ 🟤 🟥 🟧 🟨 🟩 🟦 🟪 ⬛ ⬜ 🟫 🔺 🔻 🔸 🔹 🔶 🔷
♠️ ♥️ ♦️ ♣️ 🔔 🔕 📣 📢 💬 💭 🗯️ ✔️ ☑️
😀 😃 😄 😁 😆 😅 😂 🤣 🙂 🙃 😉 😊 😇 🥰 😍 🤩 😘 😋 😛 😜 🤪 🤨 🧐 🤓 😎 🥸
😏 😒 😞 😔 😟 😕 🙁 ☹️ 😣 😖 😫 😩 🥺 😢 😭 😤 😠 😡 🤬 🤯 😳 🥵 🥶 😱 😨 😰 😥
🤔 🫡 🤭 🫢 🤫 😶 😐 😑 🥱 😴 🤤
👍 👎 👌 ✌️ 🤞 🤟 🤘 🤙 👈 👉 👆 👇 ☝️ ✋ 🤚 🖐️ 🖖 👋 👏 🙌 🫶 🤝 💪 🦾 🙏 ✍️ 👂 👃 🦶 🦵
""".split(),
}

RARITY_LABELS = {
    "legendary": "Legendary",
    "epic": "Epic",
    "rare": "Rare",
    "uncommon": "Uncommon",
    "common": "Common",
    "basic": "Basic",
}

# 50 board themes. Only square colors are overridden; coordinates/pieces keep
# the proven python-chess defaults for maximum readability.
BOARD_THEMES = {
    "classic": ("#f0d9b5", "#b58863"),
    "blue": ("#dbeafe", "#4776a8"),
    "red": ("#f7d7d7", "#a94b4b"),
    "green": ("#e0efd4", "#6b8f5a"),
    "purple": ("#eadcf4", "#77588f"),
    "ocean": ("#d5f3f6", "#2f7888"),
    "midnight": ("#b9c4d0", "#253247"),
    "forest": ("#dfe8cf", "#4f6b45"),
    "rose": ("#f6dce5", "#a85f79"),
    "gold": ("#f7e8aa", "#9c7729"),
    "silver": ("#eceff1", "#7b8790"),
    "ice": ("#e5f8ff", "#69a6c5"),
    "lava": ("#ffd7b5", "#8f2f1f"),
    "neon": ("#d8ffb8", "#4c3c78"),
    "cyber": ("#c9f7ef", "#37516d"),
    "galaxy": ("#d7d0ef", "#39305f"),
    "sunset": ("#ffd9c2", "#9a5570"),
    "candy": ("#ffe2f2", "#70b9c7"),
    "mint": ("#daf5e7", "#57977b"),
    "coffee": ("#ead8c4", "#76533d"),
    "wood": ("#e6c99f", "#8c623f"),
    "marble": ("#f2f1ed", "#90979b"),
    "slate": ("#d7dde1", "#53606b"),
    "royal": ("#e1ddff", "#4b3c94"),
    "emerald": ("#d8f1df", "#287650"),
    "ruby": ("#f6dbdf", "#8d3244"),
    "sapphire": ("#dae4f7", "#2d5b96"),
    "amethyst": ("#eadcf5", "#74508d"),
    "arctic": ("#eefcff", "#83aebd"),
    "desert": ("#f4e0b8", "#a67845"),
    "jungle": ("#d7e7c0", "#446b3a"),
    "volcano": ("#f3c8b2", "#63342d"),
    "storm": ("#d9dde6", "#4c5367"),
    "peach": ("#ffe4cf", "#ba7862"),
    "lavender": ("#eee2f7", "#8d73a3"),
    "aqua": ("#d7f6f4", "#418d91"),
    "coral": ("#f9ded7", "#bc6a5e"),
    "lime": ("#e7f3c9", "#75934a"),
    "mono": ("#eeeeee", "#777777"),
    "graphite": ("#d4d4d4", "#4b4b4b"),
    "chesscom": ("#eeeed2", "#769656"),
    "lichess": ("#f0d9b5", "#b58863"),
    "retro": ("#f1d7a5", "#8b6a45"),
    "arcade": ("#dff7d1", "#5d4b88"),
    "halloween": ("#f3d29c", "#57394f"),
    "christmas": ("#f0e7d0", "#3f7553"),
    "spring": ("#ecf2cb", "#77a66b"),
    "autumn": ("#f0d1a8", "#8d5d38"),
    "nether": ("#e7c7c7", "#5b2d39"),
    "void": ("#cbc7d6", "#272331"),
}

BOARD_DISPLAY_NAMES = {
    "chesscom": "Chess.com",
    **{name: name.title() for name in BOARD_THEMES if name != "chesscom"},
}

NAME_COLORS = {
    "red": {"label": "Red", "discord_color": 0xC0392B},
    "yellow": {"label": "Yellow", "discord_color": 0xD4AC0D},
    "orange": {"label": "Orange", "discord_color": 0xD35400},
    "green": {"label": "Green", "discord_color": 0x239B56},
}

SHOP_COLOR_ROLE_PREFIX = "Shop Color • "
