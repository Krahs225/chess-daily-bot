import discord
import asyncio
import os
import csv
import random
import datetime
import pytz
import chess
import chess.svg
from io import BytesIO

CHANNEL_ID = 1468320170891022417  # jouw #daily-puzzle kanaal
PUZZLE_CSV = "lichess_db_puzzle.csv"
TIMEZONE = pytz.timezone("Europe/Amsterdam")

TOKEN = os.getenv("DISCORD_TOKEN")

intents = discord.Intents.default()
intents.guilds = True

class ChessBot(discord.Client):
    async def setup_hook(self):
        asyncio.create_task(self.scheduler())

    def get_random_puzzle(self):
        with open(PUZZLE_CSV, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            puzzles = list(reader)

        puzzle = random.choice(puzzles)
        fen = puzzle["FEN"]
        moves = puzzle["Moves"].split()

        board = chess.Board(fen)
        side = "White" if board.turn == chess.WHITE else "Black"

        solution_uci = moves[0]
        move = chess.Move.from_uci(solution_uci)
        san = board.san(move)

        return fen, san, side

    async def post_puzzle(self, channel):
        fen, san, side = self.get_random_puzzle()
        board = chess.Board(fen)

        svg = chess.svg.board(board=board, size=500)
        image = BytesIO(svg.encode("utf-8"))
        file = discord.File(fp=image, filename="puzzle.svg")

        embed = discord.Embed(
            title="♟️ Daily Chess Puzzle",
            description=f"**{side} to move. Find the best move!**",
            color=0x2ecc71
        )
        embed.set_image(url="attachment://puzzle.svg")

        await channel.send(embed=embed, file=file)

        await asyncio.sleep(3600)  # 1 uur
        await channel.send(f"💡 **Answer:** ||{san}||")

    async def scheduler(self):
        await self.wait_until_ready()
        channel = await self.fetch_channel(CHANNEL_ID)

        while not self.is_closed():
            now = datetime.datetime.now(TIMEZONE)

            target = now.replace(hour=10, minute=0, second=0, microsecond=0)
            if now >= target:
                target += datetime.timedelta(days=1)

            await asyncio.sleep((target - now).total_seconds())
            await self.post_puzzle(channel)

    async def on_ready(self):
        print(f"🤖 Online as {self.user}")

client = ChessBot(intents=intents)
client.run(TOKEN)
