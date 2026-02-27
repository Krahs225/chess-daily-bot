import os
import requests
import discord
import json
import isodate  # Needed to parse YouTube duration format

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
YOUTUBE_API_KEY = os.getenv("YOUTUBE_API_KEY")

DISCORD_CHANNEL_ID = 1466819168748704021  # #yt-notification
YT_CHANNEL_ID = "UCN6iO2ziSemeP82WCgsvesA"

STATE_FILE = "yt_state.json"

intents = discord.Intents.default()
client = discord.Client(intents=intents)


def get_latest_video():
    search_url = (
        "https://www.googleapis.com/youtube/v3/search"
        "?part=snippet"
        f"&channelId={YT_CHANNEL_ID}"
        "&maxResults=1"
        "&order=date"
        "&type=video"
        f"&key={YOUTUBE_API_KEY}"
    )

    r = requests.get(search_url, timeout=10)
    data = r.json()

    items = data.get("items", [])
    if not items:
        return None

    item = items[0]
    video_id = item["id"]["videoId"]
    title = item["snippet"]["title"]
    thumbnail = item["snippet"]["thumbnails"]["high"]["url"]

    return video_id, title, thumbnail


def get_video_duration(video_id):
    details_url = (
        "https://www.googleapis.com/youtube/v3/videos"
        "?part=contentDetails"
        f"&id={video_id}"
        f"&key={YOUTUBE_API_KEY}"
    )

    r = requests.get(details_url, timeout=10)
    data = r.json()

    items = data.get("items", [])
    if not items:
        return None

    duration_iso = items[0]["contentDetails"]["duration"]
    duration_seconds = isodate.parse_duration(duration_iso).total_seconds()

    return duration_seconds


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
            print("Could not fetch video.")
            await client.close()
            return

        video_id, title, thumbnail = latest
        last_video_id = load_last_video()

        if video_id == last_video_id:
            print("No new upload.")
            await client.close()
            return

        duration = get_video_duration(video_id)

        if duration is not None and duration <= 60:
            embed_title = "🎬 New Short Uploaded!"
        else:
            embed_title = "📺 New YouTube Video Uploaded!"

        save_last_video(video_id)

        video_url = f"https://youtu.be/{video_id}"

        embed = discord.Embed(
            title=embed_title,
            description=f"**{title}**",
            color=0xff0000
        )

        embed.add_field(
            name="Watch here:",
            value=video_url,
            inline=False
        )

        embed.set_image(url=thumbnail)
        embed.set_footer(text="Sh4rkmate YouTube Channel")

        await channel.send(embed=embed)

    finally:
        await client.close()


client.run(DISCORD_TOKEN)
