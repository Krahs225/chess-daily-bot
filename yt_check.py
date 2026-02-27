import os
import requests
import discord
import json

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
YOUTUBE_API_KEY = os.getenv("YOUTUBE_API_KEY")

DISCORD_CHANNEL_ID = 1466819168748704021  # #yt-notification channel ID
YT_CHANNEL_ID = "UCN6iO2ziSemeP82WCgsvesA"

STATE_FILE = "yt_state.json"

intents = discord.Intents.default()
client = discord.Client(intents=intents)


def get_latest_video():
    url = (
        "https://www.googleapis.com/youtube/v3/search"
        "?part=snippet"
        f"&channelId={YT_CHANNEL_ID}"
        "&maxResults=1"
        "&order=date"
        "&type=video"
        f"&key={YOUTUBE_API_KEY}"
    )

    r = requests.get(url, timeout=10)
    data = r.json()

    items = data.get("items", [])
    if not items:
        return None

    item = items[0]
    video_id = item["id"]["videoId"]
    title = item["snippet"]["title"]
    thumbnail = item["snippet"]["thumbnails"]["high"]["url"]

    return video_id, title, thumbnail


def load_last_video():
    if not os.path.exists(STATE_FILE):
        return None
    with open(STATE_FILE, "r") as f:
        data = json.load(f)
        return data.get("last_video_id")


def save_last_video(video_id):
    with open(STATE_FILE, "w") as f:
        json.dump({"last_video_id": video_id}, f)


@client.event
async def on_ready():
    try:
        channel = await client.fetch_channel(DISCORD_CHANNEL_ID)

        latest = get_latest_video()
        if not latest:
            await client.close()
            return

        video_id, title, thumbnail = latest
        last_video_id = load_last_video()

        if video_id == last_video_id:
            print("No new video.")
            await client.close()
            return

        save_last_video(video_id)

        video_url = f"https://youtu.be/{video_id}"

        embed = discord.Embed(
            title="📺 New YouTube Upload!",
            description=f"**{title}**",
            color=0xff0000
        )

        embed.add_field(name="Watch here:", value=video_url, inline=False)
        embed.set_thumbnail(url=thumbnail)

        await channel.send(embed=embed)

    finally:
        await client.close()


client.run(DISCORD_TOKEN)
