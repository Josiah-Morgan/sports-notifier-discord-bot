from datetime import datetime, timedelta, timezone, time
import pytz

import aiohttp
import disnake
from disnake import Webhook
from disnake.ext import commands, tasks

# https://cdn.espn.com/core/nfl/boxscore?xhr=1&gameId=401220225
LEAGUES_TO_WATCH = {
    "NFL": "https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard",
    "NBA": "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard",
    "NHL": "https://site.api.espn.com/apis/site/v2/sports/hockey/nhl/scoreboard", # does not support the update stats button
}
BOT_TOKEN = "aaaaaa"
BOT_ID = 11111111
CHANNEL_ID = 11111111 # to send messages
USER_ID = 111111111111  # To mention user about a game
HOURS_BEFORE_GAME = 2  # How many hours before the game you want to be notified

sent_messages = []
bot = commands.InteractionBot(
    intents=disnake.Intents.all(),
    activity=disnake.Game(name="sports"),
)


class UpdateStats(disnake.ui.View):
    def __init__(inter, game_id):
        super().__init__()
        inter.add_item(
            disnake.ui.Button(
                label="Update Stats",
                style=disnake.ButtonStyle.blurple,
                custom_id=game_id,
            )
        )

async def fetch_json(url, headers=None):
    """Gets data from a url"""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers) as response:
                return await response.json()
    except Exception as e:
        print("Error", e)
        return None
                

async def get_application_emojis():
    """
    Disnake is outdated and does not support
    application emojis, so we have to get
    them ourselves
    """
    url = f"https://discord.com/api/v10/applications/{BOT_ID}/emojis"
    headers = {"Authorization": f"Bot {BOT_TOKEN}"}

    return await fetch_json(url, headers)


async def make_game_name(teams_string):
    """
    Gets the emojis for the teams from the ESPN api
    Ex: Dallas Cowboys at New York Giants
    Gets the Dallas and Giants logos
    """
    team_one, team_two = teams_string.split(" at ", maxsplit=1)

    team_one_emoji = await get_emoji(team_one)
    team_two_emoji = await get_emoji(team_two)

    return f"{team_one_emoji} {team_one} at {team_two_emoji} {team_two}"


async def format_date(date):
    """
    2024-10-04 00:15:00+00:00 -> Thursday, October 03, 2024 at 08:15 PM EDT
    """
    utc_time = datetime.strptime(date, "%Y-%m-%d %H:%M:%S%z")

    # Convert UTC to EST
    est_time = utc_time.astimezone(pytz.timezone("US/Eastern"))

    return est_time.strftime("%A, %B %d, %Y at %I:%M %p %Z")


async def get_emoji(emoji_name):
    emoji_name = emoji_name.replace(" ", "_")  # To match emoji name format

    emojis = await get_application_emojis()
    for emoji in emojis.get("items", []):
        if emoji["name"] == emoji_name:
            return f"<:{emoji['name']}:{emoji['id']}>"
    return "\u200B"  # Inviable character


async def get_emoji_image(emoji_name):
    emoji_name = emoji_name.replace(" ", "_")  # To match emoji name format

    emojis = await get_application_emojis()
    for emoji in emojis.get("itmes", []):
        if emoji["name"] == emoji_name:
            return f"https://cdn.discordapp.com/emojis/{emoji['id']}.png"
    return ""  # Same color as background of embed


async def sports_notifier():
    time_to_check = datetime.now(timezone.utc) + timedelta(hours=HOURS_BEFORE_GAME)

    for league_name, url in LEAGUES_TO_WATCH.items():
        data = await fetch_json(url)
        if not data:
            continue
        
        for game in data.get("events", []):
            game_time = datetime.fromisoformat(game["date"].replace("Z", "+00:00"))

            if game["id"] in sent_messages:
                continue
            
            if datetime.now(timezone.utc) <= game_time <= time_to_check:

                embed = disnake.Embed(
                    description=f"## **{await make_game_name(game['name'])}**",
                    timestamp=disnake.utils.utcnow(),
                )
                embed.set_footer(text="Last Updated")
                embed.set_thumbnail(url=await get_emoji_image(league_name))
                embed.add_field(
                    name="Game Time",
                    value=f"**{await format_date(str(game_time))}**",
                )

                channel = bot.get_channel(CHANNEL_ID)

                if league_name == "NHL":
                    await channel.send(content=f"<@{USER_ID}>", embed=embed)
                else:
                    await channel.send(
                        content=f"<@{USER_ID}>", embed=embed,
                        view=UpdateStats(f"{league_name}-{game['id']}"),
                    )

                sent_messages.append(game["id"])


@tasks.loop(minutes=25)
async def game_time_check():
    print("Checking for games")
    await sports_notifier()


@bot.event
async def on_ready():
    print("Bot is online")
    game_time_check.start()


@bot.event
async def on_button_click(inter):
    custom_id = inter.component.custom_id
    league_name, game_id = custom_id.split("-")
    await inter.response.defer()

    url = f"https://cdn.espn.com/core/{league_name.lower()}/game?xhr=1&gameId={game_id}"
    data = await fetch_json(url)
    if not data:
        return await inter.edit_original_message("Could not fetch game data")
    
    game_details = data.get("gamepackageJSON", {})
    recent_play = game_details.get("winprobability", [])[-1].get("play", {})
    
    if not recent_play:
        return await inter.edit_original_message(content="No stats to report")

    embed = inter.message.embeds[0]
    embed.clear_fields()  # So no duplicates
    embed.timestamp = disnake.utils.utcnow()
    embed.set_footer(text="Last Updated")

    period = recent_play.get("period", {}).get("number", "N/A")
    clock = recent_play.get("clock", {}).get("displayValue", "N/A")
    away_score = recent_play.get("awayScore", "N/A")
    home_score = recent_play.get("homeScore", "N/A")

    away_team_emoji = await get_emoji(
        game_details["boxscore"]["teams"][0]["team"]["displayName"]
    )
    home_team_emoji = await get_emoji(
        game_details["boxscore"]["teams"][1]["team"]["displayName"]
    )
    embed.description = f"## **{away_team_emoji} {away_score} - {home_team_emoji} {home_score} (Q{period} {clock})**"


    # Stat leaders
    for team_data in game_details.get("leaders", [])[::-1]:  # [::-1] Reverses the loop to keep the theme of the away team being first
        team_name = team_data["team"]["displayName"]
        team_emoji = await get_emoji(team_name)

        team_leader_stats = ""
        for stat in team_data["leaders"]:
            stat_name = stat["displayName"]
            leader = stat["leaders"][0]["athlete"]["fullName"]
            stat_value = stat["leaders"][0]["displayValue"]

            team_leader_stats += f"{stat_name}: {leader} ({stat_value})\n"

        embed.add_field(
            name=f"{team_emoji} Leaders", value=team_leader_stats, inline=False
        )

    await inter.edit_original_message(embed=embed, content=game_id)


@bot.slash_command()
async def test(inter):
    await inter.response.send_message("working")


bot.run(BOT_TOKEN)
