import os
import discord
import requests
import chess
import chess.svg
import chess.pgn
from io import BytesIO
import cairosvg

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
CHANNEL_ID = 1468320170891022417

intents = discord.Intents.default()
client = discord.Client(intents=intents)


async def post_puzzle(channel):

    r = requests.get(
        "https://lichess.org/api/puzzle/next",
        headers={"Accept": "application/json"},
        timeout=10
    )

    data = r.json()

    rating = data["puzzle"]["rating"]
    pgn = data["game"]["pgn"]

    game = chess.pgn.read_game(BytesIO(pgn.encode()))
    board = game.board()

    # speel eerste zet van de game zodat we puzzle positie krijgen
    move = next(game.mainline_moves())
    board.push(move)

    side = "White" if board.turn else "Black"
    orientation = chess.WHITE if board.turn else chess.BLACK

    svg_board = chess.svg.board(
        board=board,
        orientation=orientation,
        size=500,
        coordinates=True
    )

    png_bytes = cairosvg.svg2png(bytestring=svg_board.encode())
    image = BytesIO(png_bytes)

    file = discord.File(fp=image, filename="puzzle.png")

    embed = discord.Embed(
        title="🎲 Random Chess Puzzle",
        description=f"Rating: {rating}\n\n{side} to move",
        color=0x2ecc71
    )

    embed.set_image(url="attachment://puzzle.png")

    await channel.send(embed=embed, file=file)


@client.event
async def on_ready():

    channel = await client.fetch_channel(CHANNEL_ID)

    await post_puzzle(channel)

    await client.close()


client.run(DISCORD_TOKEN)
