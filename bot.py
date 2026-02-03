import discord
import os
import requests
import chess
import chess.svg
from io import BytesIO

CHANNEL_ID = 1468320170891022417  # #daily-puzzle
TOKEN = os.getenv("DISCORD_TOKEN")

intents = discord.Intents.default()
intents.guilds = True


class ChessBot(discord.Client):
    async def on_ready(self):
        channel = await self.fetch_channel(CHANNEL_ID)

        data = requests.get("https://lichess.org/api/puzzle/daily").json()

        # ✅ juiste FEN ophalen (API is inconsistent)
        fen = data.get("puzzle", {}).get("fen") or data["game"]["fen"]
        solution = data["puzzle"]["solution"][0]

        board = chess.Board(fen)
        side = "White" if board.turn == chess.WHITE else "Black"

        move = chess.Move.from_uci(solution)
        answer = board.san(move)

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

        await self.close()


client = ChessBot(intents=intents)
client.run(TOKEN)
