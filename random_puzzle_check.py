import os
import requests
import discord
import chess
import chess.svg
import cairosvg
import io

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")

DISCORD_CHANNEL_ID = 1468320170891022417

intents = discord.Intents.default()
intents.message_content = True
client = discord.Client(intents=intents)


def get_random_puzzle():
    url = "https://api.chess.com/pub/puzzle/random"
    r = requests.get(url)

    if r.status_code != 200:
        return None

    data = r.json()
    puzzle = data["puzzle"]

    fen = puzzle["fen"]
    moves = puzzle["moves"]
    title = puzzle.get("title", "Random Puzzle")

    return fen, moves, title


def render_board(fen):
    board = chess.Board(fen)

    if board.turn == chess.BLACK:
        orientation = chess.BLACK
    else:
        orientation = chess.WHITE

    svg = chess.svg.board(board=board, orientation=orientation)
    png = cairosvg.svg2png(bytestring=svg.encode("utf-8"))

    return png


async def post_puzzle(channel):
    puzzle = get_random_puzzle()

    if not puzzle:
        return

    fen, moves, title = puzzle
    image = render_board(fen)

    file = discord.File(io.BytesIO(image), filename="puzzle.png")

    embed = discord.Embed(
        title="♟️ Random Chess Puzzle",
        description="Find the best move!",
        color=0x00ff00
    )

    embed.set_image(url="attachment://puzzle.png")
    embed.set_footer(text="Source: Chess.com")

    await channel.send(embed=embed, file=file)


@client.event
async def on_ready():
    channel = await client.fetch_channel(DISCORD_CHANNEL_ID)

    async for message in channel.history(limit=50):
        if "!randompuzzle" in message.content:
            await post_puzzle(channel)
            break

    await client.close()


client.run(DISCORD_TOKEN)
