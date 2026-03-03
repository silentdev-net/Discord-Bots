import discord
from discord.ext import commands
import asyncio
import json
import os
import aiohttp
import logging
from datetime import datetime
from typing import Dict, List, Optional, Union

TOKEN = "your token here" # bot token
BACKUP_DIR = "./backup" # path where the backups will go
ASSETS_DIR = os.path.join(BACKUP_DIR, "assets")

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)s | %(message)s',
    datefmt='%H:%M:%S'
)
logger = logging.getLogger("DiscordBackup")

def ensure_directories():
    if not os.path.exists(ASSETS_DIR):
        os.makedirs(ASSETS_DIR)

async def download_file(url: str, filename: str) -> Optional[str]:
    """Downloads a file to the assets directory safely."""
    if not url:
        return None

    safe_filename = "".join([c for c in filename if c.isalpha() or c.isdigit() or c in (' ', '.', '_', '-')]).rstrip()
    path = os.path.join(ASSETS_DIR, safe_filename)

    if os.path.exists(path):
        return path

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as resp:
                if resp.status == 200:
                    data = await resp.read()
                    with open(path, 'wb') as f:
                        f.write(data)
                    return path
    except Exception as e:
        logger.error(f"Failed to download asset {url}: {e}")
    return None

intents = discord.Intents.default()
intents.members = True
intents.message_content = True
intents.guilds = True
intents.emojis_and_stickers = True  

bot = commands.Bot(command_prefix="!", intents=intents)

class BackupEngine:
    def __init__(self, guild: discord.Guild):
        self.guild = guild
        self.data = {
            "name": guild.name,
            "roles": [],
            "categories": [],
            "text_channels": [],
            "voice_channels": [],
            "emojis": [],
            "stickers": []
        }

    async def serialize_roles(self):
        logger.info("Backing up Roles...")
        roles = sorted(self.guild.roles, key=lambda r: r.position)
        for role in roles:
            if role.is_default() or role.managed: continue

            role_data = {
                "name": role.name,
                "permissions": role.permissions.value,
                "color": role.color.value,
                "hoist": role.hoist,
                "mentionable": role.mentionable
            }
            self.data["roles"].append(role_data)

    async def serialize_emojis(self):
        logger.info("Backing up Emojis...")
        for emoji in self.guild.emojis:
            if emoji.managed: continue 

            ext = "gif" if emoji.animated else "png"
            filename = f"emoji_{emoji.id}_{emoji.name}.{ext}"
            path = await download_file(emoji.url, filename)

            if path:
                self.data["emojis"].append({
                    "name": emoji.name,
                    "path": path
                })

    async def serialize_stickers(self):
        logger.info("Backing up Stickers...")
        for sticker in self.guild.stickers:

            ext_map = {1: "png", 2: "png", 3: "json"} 
            ext = ext_map.get(sticker.format.value, "png")

            filename = f"sticker_{sticker.id}_{sticker.name}.{ext}"
            path = await download_file(sticker.url, filename)

            if path:
                self.data["stickers"].append({
                    "name": sticker.name,
                    "description": sticker.description,
                    "emoji": sticker.emoji, 

                    "path": path
                })

    async def serialize_channels(self):
        logger.info("Backing up Channel Hierarchy...")

        for cat in self.guild.categories:
            cat_data = {
                "id": cat.id,
                "name": cat.name,
                "position": cat.position,
                "overwrites": self._serialize_overwrites(cat.overwrites)
            }
            self.data["categories"].append(cat_data)

        for tc in self.guild.text_channels:
            logger.info(f"Scraping Text Channel: {tc.name}")
            messages = await self._scrape_messages(tc)

            tc_data = {
                "name": tc.name,
                "category_id": tc.category_id,
                "topic": tc.topic,
                "position": tc.position,
                "nsfw": tc.nsfw,
                "overwrites": self._serialize_overwrites(tc.overwrites),
                "messages": messages
            }
            self.data["text_channels"].append(tc_data)

        for vc in self.guild.voice_channels:
            vc_data = {
                "name": vc.name,
                "category_id": vc.category_id,
                "position": vc.position,
                "bitrate": vc.bitrate,
                "user_limit": vc.user_limit,
                "overwrites": self._serialize_overwrites(vc.overwrites)
            }
            self.data["voice_channels"].append(vc_data)

    def _serialize_overwrites(self, overwrites):
        serialized = []
        for target, overwrite in overwrites.items():
            if isinstance(target, discord.Role):
                serialized.append({
                    "type": "role",
                    "name": target.name,
                    "allow": overwrite.pair()[0].value,
                    "deny": overwrite.pair()[1].value
                })
        return serialized

    async def _scrape_messages(self, channel: discord.TextChannel) -> List[dict]:
        messages_data = []
        try:
            async for msg in channel.history(limit=None, oldest_first=True):

                avatar_path = None
                if msg.author.display_avatar:
                    ext = "gif" if msg.author.display_avatar.is_animated() else "png"
                    filename = f"avatar_{msg.author.id}_{msg.author.name}.{ext}"
                    avatar_path = await download_file(msg.author.display_avatar.url, filename)

                local_attachments = []
                for attachment in msg.attachments:
                    f_path = await download_file(attachment.url, attachment.filename)
                    if f_path:
                        local_attachments.append(f_path)

                msg_payload = {
                    "author": msg.author.name,
                    "avatar_path": avatar_path,
                    "content": msg.content,
                    "attachments": local_attachments,
                    "created_at": msg.created_at.isoformat(),
                    "embeds": [e.to_dict() for e in msg.embeds if e.type == 'rich']
                }
                messages_data.append(msg_payload)

                if len(messages_data) % 100 == 0:
                    logger.info(f"   Collected {len(messages_data)} messages...")
                    await asyncio.sleep(0.5)

        except discord.Forbidden:
            logger.warning(f"   Missing permissions to read {channel.name}")

        return messages_data

    async def save_to_disk(self):
        filepath = os.path.join(BACKUP_DIR, "backup.json")
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(self.data, f, indent=4)
        logger.info(f"Backup saved to {filepath}")

class RestoreEngine:
    def __init__(self, guild: discord.Guild):
        self.guild = guild
        self.role_map = {}      
        self.cat_map = {}       
        self.asset_map = {}     
        self.data = None

    def load_backup(self):
        filepath = os.path.join(BACKUP_DIR, "backup.json")
        with open(filepath, 'r', encoding='utf-8') as f:
            self.data = json.load(f)

    async def nuke_server(self):
        logger.warning("☢ NUKE INITIATED...")

        for channel in self.guild.channels:
            try:
                await channel.delete()
                await asyncio.sleep(0.2) 
            except: pass

        for role in self.guild.roles:
            try:
                if not role.is_default() and not role.managed and role < self.guild.me.top_role:
                    await role.delete()
                    await asyncio.sleep(0.2)
            except: pass

        for emoji in self.guild.emojis:
             try: await emoji.delete(); await asyncio.sleep(0.5)
             except: pass

        for sticker in self.guild.stickers:
             try: await sticker.delete(); await asyncio.sleep(0.5)
             except: pass

        logger.info("Server Nuked Clean.")

    async def setup_internal_cdn(self):
        logger.info("Creating Internal Asset Storage (CDN)...")
        overwrites = {
            self.guild.default_role: discord.PermissionOverwrite(read_messages=False),
            self.guild.me: discord.PermissionOverwrite(read_messages=True)
        }
        cdn_channel = await self.guild.create_text_channel("internal-asset-storage", overwrites=overwrites)

        unique_avatar_paths = set()
        for tc in self.data["text_channels"]:
            for msg in tc["messages"]:
                if msg.get("avatar_path"):
                    unique_avatar_paths.add(msg["avatar_path"])

        total_assets = len(unique_avatar_paths)
        logger.info(f"Identified {total_assets} unique avatars to re-host.")

        for i, local_path in enumerate(unique_avatar_paths):
            if i % 5 == 0: logger.info(f"   Re-hosting assets: {i}/{total_assets}...")

            if os.path.exists(local_path):
                try:
                    file = discord.File(local_path)
                    msg = await cdn_channel.send(file=file)
                    if msg.attachments:
                        self.asset_map[local_path] = msg.attachments[0].url
                    await asyncio.sleep(1.5) 
                except Exception as e:
                    logger.error(f"Failed to rehost {local_path}: {e}")
            else:
                logger.warning(f"Local asset missing: {local_path}")

    async def restore_roles(self):
        logger.info("Restoring Roles...")
        for role_data in self.data["roles"]:
            try:
                new_role = await self.guild.create_role(
                    name=role_data["name"],
                    permissions=discord.Permissions(role_data["permissions"]),
                    color=discord.Color(role_data["color"]),
                    hoist=role_data["hoist"],
                    mentionable=role_data["mentionable"]
                )
                self.role_map[role_data["name"]] = new_role
                await asyncio.sleep(0.5) 
            except Exception as e:
                logger.error(f"Failed to create role {role_data['name']}: {e}")

    async def restore_emojis(self):
        logger.info("Restoring Emojis...")
        emojis = self.data.get("emojis", [])
        for e_data in emojis:
            path = e_data["path"]
            if os.path.exists(path):
                try:
                    with open(path, "rb") as f:
                        image = f.read()
                    await self.guild.create_custom_emoji(name=e_data["name"], image=image)
                    logger.info(f"   Created Emoji: {e_data['name']}")
                    await asyncio.sleep(2.0) 

                except Exception as e:
                    logger.error(f"Failed to create emoji {e_data['name']}: {e}")

    async def restore_stickers(self):
        logger.info("Restoring Stickers...")
        stickers = self.data.get("stickers", [])
        for s_data in stickers:
            path = s_data["path"]
            if os.path.exists(path):
                try:
                    file = discord.File(path)
                    await self.guild.create_sticker(
                        name=s_data["name"],
                        description=s_data["description"],
                        emoji=s_data["emoji"],
                        file=file
                    )
                    logger.info(f"   Created Sticker: {s_data['name']}")
                    await asyncio.sleep(2.0)
                except Exception as e:
                    logger.error(f"Failed to create sticker {s_data['name']}: {e}")

    def _get_overwrites(self, overwrite_data):
        overwrites = {}
        for item in overwrite_data:
            if item["type"] == "role":
                role = self.role_map.get(item["name"])
                if role:
                    ov = discord.PermissionOverwrite.from_pair(
                        discord.Permissions(item["allow"]),
                        discord.Permissions(item["deny"])
                    )
                    overwrites[role] = ov
        return overwrites

    async def restore_categories(self):
        logger.info("Restoring Categories...")
        for cat_data in self.data["categories"]:
            overwrites = self._get_overwrites(cat_data["overwrites"])
            new_cat = await self.guild.create_category(
                name=cat_data["name"],
                position=cat_data["position"],
                overwrites=overwrites
            )
            self.cat_map[cat_data["id"]] = new_cat
            await asyncio.sleep(1)

    async def restore_text_channels(self):
        logger.info("Restoring Text Channels & Messages...")
        for tc_data in self.data["text_channels"]:
            category = self.cat_map.get(tc_data["category_id"])
            overwrites = self._get_overwrites(tc_data["overwrites"])

            new_channel = await self.guild.create_text_channel(
                name=tc_data["name"],
                category=category,
                topic=tc_data["topic"],
                position=tc_data["position"],
                nsfw=tc_data["nsfw"],
                overwrites=overwrites
            )

            if tc_data["messages"]:
                await self._mimic_messages(new_channel, tc_data["messages"])

    async def _mimic_messages(self, channel, messages):
        webhook = await channel.create_webhook(name="MimicHook")

        for i, msg in enumerate(messages):
            if i % 20 == 0: logger.info(f"   Restoring {channel.name}: {i}/{len(messages)}")

            files = []
            for attach_path in msg["attachments"]:
                if os.path.exists(attach_path):
                    try: files.append(discord.File(attach_path))
                    except: pass

            avatar_url = self.asset_map.get(msg["avatar_path"])

            try:
                await webhook.send(
                    content=msg["content"],
                    username=msg["author"],
                    avatar_url=avatar_url,
                    files=files,
                    embeds=[discord.Embed.from_dict(e) for e in msg["embeds"]] if msg["embeds"] else [],
                    wait=True 
                )
            except discord.HTTPException as e:
                if e.status == 429:
                    await asyncio.sleep(e.retry_after + 1)
                else:
                    logger.error(f"   Webhook Error: {e}")

            await asyncio.sleep(2.0)

    async def restore_voice_channels(self):
        logger.info("Restoring Voice Channels...")
        for vc_data in self.data["voice_channels"]:
            category = self.cat_map.get(vc_data["category_id"])
            overwrites = self._get_overwrites(vc_data["overwrites"])

            await self.guild.create_voice_channel(
                name=vc_data["name"],
                category=category,
                position=vc_data["position"],
                bitrate=vc_data["bitrate"],
                user_limit=vc_data["user_limit"],
                overwrites=overwrites
            )
            await asyncio.sleep(1)

@bot.event
async def on_ready():
    ensure_directories()
    logger.info(f"Logged in as {bot.user} (ID: {bot.user.id})")
    logger.info("Ready to Clone.")

@bot.command()
@commands.has_permissions(administrator=True)
async def backup(ctx):
    """Backs up the current server."""
    await ctx.send("**Starting System Backup... check console for progress.**")

    engine = BackupEngine(ctx.guild)

    await engine.serialize_roles()
    await engine.serialize_emojis()    

    await engine.serialize_stickers()  

    await engine.serialize_channels()
    await engine.save_to_disk()

    await ctx.send("**Backup Complete!**")

@bot.command()
@commands.has_permissions(administrator=True)
async def restore(ctx):
    """Restores the server from backup using Internal CDN strategy."""
    await ctx.send("**WARNING: NUCLEAR RESTORE DETECTED.**\n"
                   "This will:\n"
                   "1. Delete ALL channels/roles/emojis/stickers.\n"
                   "2. Create a hidden '#internal-asset-storage' channel.\n"
                   "3. Upload all backed-up avatars there to generate links.\n"
                   "4. Reconstruct the server.\n\n"
                   "Type `confirm` to proceed.")

    def check(m): return m.author == ctx.author and m.content.lower() == 'confirm'

    try:
        await bot.wait_for('message', check=check, timeout=15.0)
    except asyncio.TimeoutError:
        await ctx.send("❌ Timeout. Restore Cancelled.")
        return

    msg = await ctx.send("**Initializing Restore Engine...**")

    engine = RestoreEngine(ctx.guild)
    engine.load_backup()

    await engine.nuke_server()
    await engine.setup_internal_cdn()

    await engine.restore_roles()
    await engine.restore_emojis()    

    await engine.restore_stickers()  

    await engine.restore_categories()
    await engine.restore_text_channels()
    await engine.restore_voice_channels()

    try:
        general = discord.utils.get(ctx.guild.text_channels, position=0)
        if general:
            await general.send("**Server Restoration Complete.**\n"
                               "*Note: Do not delete #internal-asset-storage, or user avatars will break.*")
    except:
        pass

if __name__ == "__main__":
    if TOKEN == "YOUR_BOT_TOKEN_HERE":
        print("Error: Please set your bot token in the script.")
    else:
        bot.run(TOKEN)

