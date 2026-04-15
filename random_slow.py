import os
import discord
import asyncio
import json
import requests
import chess
import chess.svg
import chess.pgn
from io import BytesIO, StringIO
import cairosvg

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
STATE_FILE = "random_state.json"

intents = discord.Intents.default()
intents.message_content = True

client = discord.Client(intents=intents)


def load_last_id():
    if not os.path.exists(STATE_FILE):
        return 0
    with open(STATE_FILE, "r") as f:
        return json.load(f).get("last_id", 0)


def save_last_id(message_id):
    with open(STATE_FILE, "w") as f:
        json.dump({"last_id": message_id}, f)


def uci_to_san_sequence(board, moves_uci):
    temp_board = board.copy()
    san_moves = []

    for move_uci in moves_uci:
        move = chess.Move.from_uci(move_uci)
        if move in temp_board.legal_moves:
            san_moves.append(temp_board.san(move))
            temp_board.push(move)

    return " ".join(san_moves)


async def post_puzzle(channel):
    r = requests.get(
        "https://lichess.org/api/puzzle/next",
        headers={"Accept": "application/json"},
        timeout=10
    )

    data = r.json()

    rating = data["puzzle"]["rating"]
    initial_ply = data["puzzle"]["initialPly"]
    pgn = data["game"]["pgn"]
    solution_moves = data["puzzle"]["solution"]
    puzzle_id = data["puzzle"]["id"]

    game = chess.pgn.read_game(StringIO(pgn))
    board = game.board()
    node = game

    for _ in range(initial_ply):
        if node.variations:
            node = node.variations[0]
            board.push(node.move)
        else:
            break

    if node.variations:
        node = node.variations[0]
        board.push(node.move)

    solution = uci_to_san_sequence(board, solution_moves)

    side = "White" if board.turn else "Black"
    orientation = chess.WHITE if board.turn else chess.BLACK

    svg_board = chess.svg.board(board=board, orientation=orientation, size=500)
    png_bytes = cairosvg.svg2png(bytestring=svg_board.encode())
    image = BytesIO(png_bytes)

    file = discord.File(fp=image, filename="puzzle.png")

    embed = discord.Embed(
        title="🎲 Random Chess Puzzle (backup)",
        description=(
            f"Rating: {rating}\n\n"
            f"{side} to move\n\n"
            f"Solution: ||{solution}||\n\n"
            f"https://lichess.org/training/{puzzle_id}"
        ),
        color=0xe67e22
    )

    embed.set_image(url="attachment://puzzle.png")

    await channel.send(embed=embed, file=file)


@client.event
async def on_message(message):
    if message.author.bot:
        return

    if message.content.strip() == "!randompuzzle":

        last_id = load_last_id()

        if message.id <= last_id:
            return

        # wacht zodat fast bot eerst kan reageren
        await asyncio.sleep(15)

        # check of er al een bot heeft gereageerd
        recent = [msg async for msg in message.channel.history(limit=5)]

        for msg in recent:
            if msg.author.bot:
                return

        await post_puzzle(message.channel)
        save_last_id(message.id)


client.run(DISCORD_TOKEN)
