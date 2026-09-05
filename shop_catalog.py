"""Shared cosmetic shop catalogue for the Puzzle + Guess bots."""

SHOP_BUILD = "cosmetics-shop-v1-2026-09-05"

BADGE_BOX_COST = 50.0
BOARD_COST = 100.0
PIECE_COST = 100.0
ARROW_COST = 50.0
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
BOARD_THEMES = {'classic': ('#f0d9b5', '#b58863'),
 'blue': ('#dbeafe', '#4776a8'),
 'red': ('#f7d7d7', '#a94b4b'),
 'green': ('#e0efd4', '#6b8f5a'),
 'purple': ('#eadcf4', '#77588f'),
 'ocean': ('#d5f3f6', '#2f7888'),
 'midnight': ('#b9c4d0', '#253247'),
 'forest': ('#dfe8cf', '#4f6b45'),
 'rose': ('#f6dce5', '#a85f79'),
 'gold': ('#f7e8aa', '#9c7729'),
 'silver': ('#eceff1', '#7b8790'),
 'ice': ('#e5f8ff', '#69a6c5'),
 'lava': ('#ffd7b5', '#8f2f1f'),
 'neon': ('#d8ffb8', '#4c3c78'),
 'cyber': ('#c9f7ef', '#37516d'),
 'galaxy': ('#d7d0ef', '#39305f'),
 'sunset': ('#ffd9c2', '#9a5570'),
 'candy': ('#ffe2f2', '#70b9c7'),
 'mint': ('#daf5e7', '#57977b'),
 'coffee': ('#ead8c4', '#76533d'),
 'wood': ('#e6c99f', '#8c623f'),
 'marble': ('#f2f1ed', '#90979b'),
 'slate': ('#d7dde1', '#53606b'),
 'royal': ('#e1ddff', '#4b3c94'),
 'emerald': ('#d8f1df', '#287650'),
 'ruby': ('#f6dbdf', '#8d3244'),
 'sapphire': ('#dae4f7', '#2d5b96'),
 'amethyst': ('#eadcf5', '#74508d'),
 'arctic': ('#eefcff', '#83aebd'),
 'desert': ('#f4e0b8', '#a67845'),
 'jungle': ('#d7e7c0', '#446b3a'),
 'volcano': ('#f3c8b2', '#63342d'),
 'storm': ('#d9dde6', '#4c5367'),
 'peach': ('#ffe4cf', '#ba7862'),
 'lavender': ('#eee2f7', '#8d73a3'),
 'aqua': ('#d7f6f4', '#418d91'),
 'coral': ('#f9ded7', '#bc6a5e'),
 'lime': ('#e7f3c9', '#75934a'),
 'mono': ('#eeeeee', '#777777'),
 'graphite': ('#d4d4d4', '#4b4b4b'),
 'chesscom': ('#eeeed2', '#769656'),
 'lichess': ('#f0d9b5', '#b58863'),
 'retro': ('#f1d7a5', '#8b6a45'),
 'arcade': ('#dff7d1', '#5d4b88'),
 'halloween': ('#f3d29c', '#57394f'),
 'christmas': ('#f0e7d0', '#3f7553'),
 'spring': ('#ecf2cb', '#77a66b'),
 'autumn': ('#f0d1a8', '#8d5d38'),
 'nether': ('#e7c7c7', '#5b2d39'),
 'void': ('#cbc7d6', '#272331'),
 'teal': ('#d7f2ef', '#3f7f79'),
 'navy': ('#d9e2ef', '#30486f'),
 'sky': ('#e1f3ff', '#69a7cc'),
 'cobalt': ('#dce6f7', '#315f9e'),
 'indigo': ('#e4e0f4', '#4d4c91'),
 'violet': ('#eee1f4', '#855aa1'),
 'magenta': ('#f5ddea', '#9f4777'),
 'berry': ('#f3d9e3', '#8f435f'),
 'cherry': ('#f7d9dc', '#a54550'),
 'salmon': ('#f8dfd8', '#b86f61'),
 'amber': ('#f7e6bd', '#ad7b32'),
 'bronze': ('#ead5b7', '#88613a'),
 'copper': ('#f0d2bd', '#995c42'),
 'sand': ('#f2e5c9', '#aa8d5a'),
 'cream': ('#fff3d7', '#a78c63'),
 'olive': ('#eaebc9', '#7f834c'),
 'moss': ('#e0e8c7', '#667a45'),
 'sage': ('#e7ecd9', '#7b8e69'),
 'pine': ('#d9e7d9', '#3e684d'),
 'bamboo': ('#ebefcf', '#81934f'),
 'seafoam': ('#dbf3e7', '#4f9481'),
 'lagoon': ('#d6eff0', '#3f8290'),
 'tropical': ('#ddf1dd', '#39826a'),
 'frost': ('#effaff', '#75a7bb'),
 'glacier': ('#e5f2fb', '#668ca8'),
 'aurora': ('#e6f2e2', '#5f7a88'),
 'dusk': ('#e8dce5', '#72596f'),
 'twilight': ('#dfdbe8', '#514f72'),
 'night': ('#cfd7e2', '#283546'),
 'eclipse': ('#d5d2dc', '#38343f'),
 'carbon': ('#d7d7d7', '#3a3a3a'),
 'ash': ('#e5e5e2', '#6f7476'),
 'smoke': ('#e0e3e6', '#687078'),
 'paper': ('#faf7eb', '#9c927d'),
 'ivory': ('#fff8e7', '#a99a78'),
 'sandstone': ('#f1dfc5', '#9b7854'),
 'clay': ('#efd3c5', '#9f6656'),
 'terracotta': ('#f0d1c0', '#985c45'),
 'mocha': ('#e7d4c4', '#72503e'),
 'espresso': ('#ddcfca', '#4e3b36'),
 'plum': ('#eadce7', '#704e69'),
 'grape': ('#e5d9ed', '#684b81'),
 'orchid': ('#f0dff3', '#8e5d99'),
 'blossom': ('#f8e4e9', '#a46a7c'),
 'watermelon': ('#f5dfd9', '#63906a'),
 'lemon': ('#fff4c9', '#aa9846'),
 'kiwi': ('#edf1cd', '#718d4b'),
 'matrix': ('#d9f2d2', '#37644a'),
 'synthwave': ('#ecdaf3', '#4e4f94'),
 'deepsea': ('#d5e7ec', '#285565')}

BOARD_DISPLAY_NAMES = {
    "chesscom": "Chess.com",
    **{name: name.title() for name in BOARD_THEMES if name != "chesscom"},
}

PIECE_SETS = {
    # The default python-chess piece set stays free.
    "classic": {
        "label": "Classic",
        "shape": "classic",
    },

    # Custom sets use real chess-piece glyph silhouettes instead of letters,
    # tokens or diamonds.  DejaVu is already used by the bot renderer and is
    # available on the GitHub Actions runner, so these do not need image assets.
    "staunton": {
        "label": "Staunton",
        "shape": "glyph",
        "font_family": "DejaVu Sans",
        "font_size": 42,
        "font_weight": 700,
        "scale_x": 1.00,
        "scale_y": 1.00,
        "white_fill": "#f6f1e5",
        "black_fill": "#161616",
        "white_stroke": "#292929",
        "black_stroke": "#050505",
        "stroke_width": 0.55,
    },
    "modern": {
        "label": "Modern",
        "shape": "glyph",
        "font_family": "DejaVu Sans",
        "font_size": 40,
        "font_weight": 700,
        "scale_x": 1.00,
        "scale_y": 1.00,
        "white_fill": "#f4f4f1",
        "black_fill": "#151515",
        "white_stroke": "#242424",
        "black_stroke": "#050505",
        "stroke_width": 0.65,
    },
    "royal": {
        "label": "Royal",
        "shape": "glyph",
        "font_family": "DejaVu Sans",
        "font_size": 41,
        "font_weight": 400,
        "scale_x": 1.12,
        "scale_y": 1.02,
        "white_fill": "#fff7df",
        "black_fill": "#24212a",
        "white_stroke": "#3b352b",
        "black_stroke": "#08070a",
        "stroke_width": 0.8,
    },
    "mono": {
        "label": "Mono",
        "shape": "glyph",
        "font_family": "DejaVu Sans Mono",
        "font_size": 39,
        "font_weight": 700,
        "scale_x": 0.94,
        "scale_y": 1.03,
        "white_fill": "#f2f2f2",
        "black_fill": "#111111",
        "white_stroke": "#222222",
        "black_stroke": "#000000",
        "stroke_width": 0.65,
    },
    "slim": {
        "label": "Slim",
        "shape": "glyph",
        "font_family": "DejaVu Sans",
        "font_size": 42,
        "font_weight": 400,
        "scale_x": 0.82,
        "scale_y": 1.04,
        "white_fill": "#f7f7f2",
        "black_fill": "#171717",
        "white_stroke": "#2a2a2a",
        "black_stroke": "#050505",
        "stroke_width": 0.5,
    },
    "bold": {
        "label": "Bold",
        "shape": "glyph",
        "font_family": "DejaVu Sans",
        "font_size": 40,
        "font_weight": 900,
        "scale_x": 1.08,
        "scale_y": 1.02,
        "white_fill": "#f7f4ea",
        "black_fill": "#101010",
        "white_stroke": "#1c1c1c",
        "black_stroke": "#000000",
        "stroke_width": 1.1,
    },
    "outline": {
        "label": "Outline",
        "shape": "glyph",
        "font_family": "DejaVu Sans",
        "font_size": 40,
        "font_weight": 700,
        "scale_x": 1.00,
        "scale_y": 1.00,
        "outline_only": True,
        "white_fill": "#f9f9f5",
        "black_fill": "#151515",
        "white_stroke": "#262626",
        "black_stroke": "#050505",
        "stroke_width": 1.35,
    },
    "figurine": {
        "label": "Figurine",
        "shape": "glyph",
        "font_family": "DejaVu Sans",
        "font_size": 40,
        "font_weight": 700,
        "scale_x": 0.96,
        "scale_y": 1.00,
        "glyph_variant": "native",
        "white_fill": "#f7f7f2",
        "black_fill": "#111111",
        "white_stroke": "#222222",
        "black_stroke": "#050505",
        "stroke_width": 0.6,
    },
    # Keep one letter-based set for people who actually liked it; the dozens of
    # old colour variants, Token, Diamond, Shield and Minimal sets are gone.
    "monogram": {
        "label": "Monogram",
        "shape": "monogram",
        "white_fill": "#f7f7f2",
        "black_fill": "#111111",
        "white_stroke": "#111111",
        "black_stroke": "#f7f7f2",
    },
}


def canonical_piece_set(name):
    """Map old 97-set cosmetic keys onto the smaller real-piece catalogue."""
    key = str(name or "classic").casefold().strip()
    if key in PIECE_SETS:
        return key
    if key.startswith("figurine"):
        return "figurine"
    if key.startswith("monogram"):
        return "monogram"
    if key.startswith("token"):
        return "modern"
    if key.startswith("diamond"):
        return "mono"
    if key.startswith("shield"):
        return "royal"
    if key.startswith("minimal"):
        return "slim"
    return None


PIECE_DISPLAY_NAMES = {name: data["label"] for name, data in PIECE_SETS.items()}

ARROW_COLORS = {
    "green": {"label": "Green", "hex": "#15781B"},
    "red": {"label": "Red", "hex": "#C0392B"},
    "blue": {"label": "Blue", "hex": "#2980B9"},
    "yellow": {"label": "Yellow", "hex": "#F1C40F"},
    "orange": {"label": "Orange", "hex": "#E67E22"},
    "purple": {"label": "Purple", "hex": "#8E44AD"},
    "cyan": {"label": "Cyan", "hex": "#00A8C6"},
    "pink": {"label": "Pink", "hex": "#E84393"},
    "gold": {"label": "Gold", "hex": "#D4AC0D"},
    "lime": {"label": "Lime", "hex": "#7ED957"},
}
DEFAULT_ARROW_COLOR = "green"

NAME_COLORS = {
    "red": {"label": "Red", "discord_color": 0xC0392B},
    "yellow": {"label": "Yellow", "discord_color": 0xD4AC0D},
    "orange": {"label": "Orange", "discord_color": 0xD35400},
    "green": {"label": "Green", "discord_color": 0x239B56},
    "purple": {"label": "Purple", "discord_color": 0x8E44AD},
    "cyan": {"label": "Cyan", "discord_color": 0x17A2B8},
    "gold": {"label": "Gold", "discord_color": 0xB7950B},
    "gray": {"label": "Gray", "discord_color": 0x7F8C8D},
}

SHOP_COLOR_ROLE_PREFIX = "Shop Color • "
