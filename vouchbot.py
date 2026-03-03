import discord
from discord import app_commands
from discord.ext import commands
import json
import os
import datetime

BOT_TOKEN = "your token here" # bot token
ALLOWED_ROLE_ID =     # role ids that can vouch
ALLOWED_CHANNEL_ID =  # channel ids where the vouches can go
SELLER_ID =           # sellers user id
SELLER_ID_2 =         # second sellers user id  

DATABASE_FILE = "vouches.json"

intents = discord.Intents.default()
intents.members = True 

client = commands.Bot(command_prefix="!", intents=intents)

def load_data():
    if not os.path.exists(DATABASE_FILE):
        return {"count": 397, "vouches": []} 

    with open(DATABASE_FILE, "r") as f:
        return json.load(f)

def save_data(data):
    with open(DATABASE_FILE, "w") as f:
        json.dump(data, f, indent=4)

@client.event
async def on_ready():
    print(f'Logged in as {client.user} (ID: {client.user.id})')
    try:
        synced = await client.tree.sync()
        print(f"synced {len(synced)} command(s)")
    except Exception as e:
        print(e)

@client.tree.command(name="vouch", description="Submit a new vouch")
@app_commands.describe(stars="Rating (1-5)", reason="Comment about the service")
async def vouch(interaction: discord.Interaction, stars: int, reason: str):

    if interaction.channel_id != ALLOWED_CHANNEL_ID:
        await interaction.response.send_message(f"You can only vouch in <#{ALLOWED_CHANNEL_ID}>", ephemeral=True)
        return

    if not any(role.id == ALLOWED_ROLE_ID for role in interaction.user.roles):
        await interaction.response.send_message("You do not have permission to vouch.", ephemeral=True)
        return

    if stars < 1 or stars > 5:
        await interaction.response.send_message("Stars must be between 1 and 5.", ephemeral=True)
        return

    data = load_data()
    data["count"] += 1
    current_vouch_num = data["count"]

    new_record = {
        "id": current_vouch_num,
        "author": interaction.user.id,
        "stars": stars,
        "reason": reason,
        "time": datetime.datetime.now().isoformat()
    }
    data["vouches"].append(new_record)
    save_data(data)

    star_display = "⭐" * stars

    seller_text = f"<@{SELLER_ID}> & <@{SELLER_ID_2}>"

    timestamp_code = int(datetime.datetime.now().timestamp())

    embed = discord.Embed(title="✨ New Vouch Recorded!", color=0xE19AB0)

    if interaction.user.avatar:
        embed.set_thumbnail(url=interaction.user.avatar.url)

    vouch_field_value = (
        f"> `🤵` **Seller:** {seller_text}\n"
        f"> `⭐` **Rating:** `{star_display}`\n"
        f"> `💭` **Reason:** {reason}"
    )
    embed.add_field(name="Vouch", value=vouch_field_value, inline=False)

    user_info_value = (
        f"> `🤵` **Vouched By:** {interaction.user.mention}\n"
        f"> `🆔` **UserID:** `{interaction.user.id}`\n"
        f"> `🕒` **Timestamp:** <t:{timestamp_code}:R>\n"
        f"> `🔢` **Vouch Nº** {current_vouch_num}"
    )
    embed.add_field(name="User Information", value=user_info_value, inline=False)

    embed.set_footer(text="CALIBER VOUCHES", icon_url=client.user.avatar.url if client.user.avatar else None)

    await interaction.response.send_message(embed=embed)

@client.tree.command(name="vouch_export", description="Download database backup (Admin Only)")
@commands.has_permissions(administrator=True)
async def vouch_export(interaction: discord.Interaction):
    if not os.path.exists(DATABASE_FILE):
        await interaction.response.send_message("No database exists yet.", ephemeral=True)
        return

    with open(DATABASE_FILE, "rb") as f:
        file = discord.File(f, filename="vouches_backup.json")
        await interaction.response.send_message("Database backup:", file=file, ephemeral=True)

@client.tree.command(name="vouch_restore", description="Upload database backup (Admin Only)")
@commands.has_permissions(administrator=True)
async def vouch_restore(interaction: discord.Interaction, file: discord.Attachment):
    if not file.filename.endswith(".json"):
        await interaction.response.send_message("File must be a .json", ephemeral=True)
        return

    await file.save(DATABASE_FILE)

    try:
        data = load_data()
        await interaction.response.send_message(f"Database restored. Next vouch will be Nº {data['count'] + 1}", ephemeral=True)
    except:
        await interaction.response.send_message("File corrupted or invalid JSON.", ephemeral=True)

client.run(BOT_TOKEN)

