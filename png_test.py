import discord
import os
import base64
from io import BytesIO

TOKEN = os.getenv("DISCORD_TOKEN")
CHANNEL_ID = 1468320170891022417

# 1x1 rode pixel PNG (100% geldig)
PNG_BASE64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwC"
    "AAAAC0lEQVR42mP8/x8AAwMCAO+/pZkAAAAASUVORK5CYII="
)

intents = discord.Intents.default()
client = discord.Client(intents=intents)

@client.event
async def on_ready():
    try:
        channel = await client.fetch_channel(CHANNEL_ID)

        image_bytes = base64.b64decode(PNG_BASE64)
        file = discord.File(fp=BytesIO(image_bytes), filename="test.png")

        await channel.send("🧪 PNG upload test:", file=file)

    except Exception as e:
        print("❌ Fout:", e)

    finally:
        await client.close()

client.run(TOKEN)
