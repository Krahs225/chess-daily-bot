import discord
import os

TOKEN = os.getenv("DISCORD_TOKEN")
CHANNEL_ID = 1468320170891022417  # exact dezelfde

intents = discord.Intents.default()
client = discord.Client(intents=intents)

@client.event
async def on_ready():
    print("🤖 Bot logged in as", client.user)

    try:
        channel = await client.fetch_channel(CHANNEL_ID)
        await channel.send("Idk")
        print("✅ Message sent")
    except Exception as e:
        print("❌ Error while sending message:", e)

    await client.close()

client.run(TOKEN)
