import os
import requests
import discord
import chess
import chess.svg
import cairosvg

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")

CHANNEL_ID = 1468320170891022417

intents = discord.Intents.default()
intents.message_content = True

client = discord.Client(intents=intents)


async def post_puzzle(channel):

    try:
        r = requests.get("https://api.chess.com/pub/puzzle", timeout=10)

        if r.status_code != 200:
            print("Chess.com API error")
            return

        data = r.json()

    except Exception as e:
        print("Puzzle fetch failed:", e)
        return

    fen = data.get("fen")
    title = data.get("title")
    url = data.get("url")

    if not fen:
        print("Invalid puzzle data")
        return

    board = chess.Board(fen)

    orientation = chess.WHITE
    if board.turn == chess.BLACK:
        orientation = chess.BLACK

    svg_board = chess.svg.board(board=board, orientation=orientation)

    cairosvg.svg2png(bytestring=svg_board, write_to="puzzle.png")

    file = discord.File("puzzle.png")

    embed = discord.Embed(
        title="♟️ Random Chess Puzzle",
        description=title,
        color=0x00ff00
    )

    embed.add_field(name="Puzzle link", value=url, inline=False)
    embed.set_image(url="attachment://puzzle.png")

    await channel.send(file=file, embed=embed)


@client.event
async def on_ready():

    channel = await client.fetch_channel(CHANNEL_ID)

    messages = [msg async for msg in channel.history(limit=10)]

    for msg in messages:

        if msg.author.bot:
            continue

        if msg.content.lower() == "!randompuzzle":
            await post_puzzle(channel)
            break

    await client.close()


client.run(DISCORD_TOKEN)
