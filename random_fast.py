import discord
import asyncio
import os
import json
import requests
import chess
import chess.pgn
import chess.svg
import cairosvg
from io import BytesIO

TOKEN = os.getenv("DISCORD_TOKEN")
CHANNEL_ID = 123456789012345678  # <-- jouw ID

STATE_FILE = "random_state.json"


def load_state():
    if not os.path.exists(STATE_FILE):
        return 0
    with open(STATE_FILE, "r") as f:
        return json.load(f).get("last_id", 0)


def save_state(last_id):
    with open(STATE_FILE, "w") as f:
        json.dump({"last_id": last_id}, f)


def fetch_puzzle():
    try:
        res = requests.get("https://lichess.org/api/puzzle/next", timeout=10)
        return res.json()
    except:
        return None


def build_board(data):
    pgn = data["game"]["pgn"]
    initial_ply = data["puzzle"]["initialPly"]

    game = chess.pgn.read_game(BytesIO(pgn.encode()))
    board = game.board()

    node = game

    for _ in range(initial_ply):
        node = node.variations[0]
        board.push(node.move)

    # 🔥 +1 fix
    node = node.variations[0]
    board.push(node.move)

    return board


def uci_to_san_sequence(board, moves):
    temp = board.copy()
    san_moves = []

    for move in moves:
        m = chess.Move.from_uci(move)
        san_moves.append(temp.san(m))
        temp.push(m)

    return " ".join(san_moves)


def render_board(board):
    svg = chess.svg.board(board=board, orientation=board.turn)
    return cairosvg.svg2png(bytestring=svg.encode())


intents = discord.Intents.default()
intents.message_content = True  # ⚠️ moet AAN staan in portal

client = discord.Client(intents=intents)


@client.event
async def on_ready():
    print(f"Logged in as {client.user}")

    channel = client.get_channel(CHANNEL_ID)
    last_id = load_state()

    # skip oude berichten
    if last_id == 0:
        messages = [msg async for msg in channel.history(limit=1)]
        if messages:
            last_id = messages[0].id
            save_state(last_id)

    while True:
        messages = [msg async for msg in channel.history(limit=25)]

        for message in reversed(messages):

            print("MSG:", message.content)

            if message.id <= last_id:
                continue

            if message.author.bot:
                continue

            # 🔥 command check
            if "!randompuzzle" not in message.content.lower():
                continue

            print("COMMAND!")

            data = fetch_puzzle()
            if not data:
                continue

            board = build_board(data)
            solution = uci_to_san_sequence(board, data["puzzle"]["solution"])
            png = render_board(board)

            side = "White" if board.turn else "Black"

            embed = discord.Embed(
                title="♟ Chess Puzzle",
                description=f"Rating: {data['puzzle']['rating']}\nSide: {side}",
                color=0x2b2d31
            )

            file = discord.File(BytesIO(png), filename="board.png")
            embed.set_image(url="attachment://board.png")

            embed.add_field(
                name="Solution",
                value=f"||{solution}||",
                inline=False
            )

            await message.channel.send(embed=embed, file=file)

            last_id = message.id
            save_state(last_id)

        await asyncio.sleep(5)


client.run(TOKEN)
