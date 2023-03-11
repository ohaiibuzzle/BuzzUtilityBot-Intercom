"""
The main Intercom module
"""

import asyncio
import os
import random
import sqlite3
import string

import aiohttp
import aiosqlite
import discord
from discord.ext import commands, tasks


class Intercom(commands.Cog):
    """
    Intercom class
    """
    ban_cache = {}
    all_channels = []

    def __init__(self, client):
        def setup_database():
            if not os.path.exists("runtime/intercom.db"):
                conn = sqlite3.connect("runtime/intercom.db")
                cursor = conn.cursor()
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS intercom 
                    (id INTEGER PRIMARY KEY AUTOINCREMENT, 
                    peer1 INTEGER, peer2 INTEGER, 
                    peer1_gid INTEGER, peer2_gid INTEGER, 
                    active INTEGER DEFAULT 1,
                    sync_bans INTEGER DEFAULT 1)
                    """
                )
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS webhooks_urls 
                    (id INTEGER PRIMARY KEY, url TEXT, gid INTEGER)
                    """
                )
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS silent_list 
                    (id INTEGER PRIMARY KEY AUTOINCREMENT, 
                    gid INTEGER, silent_gid INTEGER)
                    """
                )
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS fail2ban 
                    (id INTEGER PRIMARY KEY AUTOINCREMENT, 
                    gid INTEGER, target_gid INTEGER, 
                    count INTEGER DEFAULT 0)
                    """
                )
                conn.commit()
                conn.close()

        self.client = client
        setup_database()

    @tasks.loop(seconds=300)
    async def update_channels(self):
        """
        Update the list of channels the bot can see
        """
        print("Updating channels")
        self.all_channels = list(self.client.get_all_channels())
        print("Done")

    @update_channels.before_loop
    async def before_update_channels(self):
        """
        Wait for the bot to be ready before updating channels
        """
        await self.client.wait_until_ready()

    @tasks.loop(seconds=86400)
    async def update_ban_cache(self):
        """
        Task to update the ban cache
        """
        print("Updating global ban cache")
        guilds = self.client.guilds
        for guild in guilds:
            try:
                self.ban_cache[guild.id] = [
                    ban.user.id for ban in await guild.bans().flatten()
                ]
            except discord.errors.Forbidden:
                print(f"Failed to get bans for {guild.name} ({guild.id})")
                self.ban_cache[guild.id] = []
        print("Done")

    @update_ban_cache.before_loop
    async def before_update_ban_cache(self):
        """
        Wait for the bot to be ready before updating the ban cache
        """
        await self.client.wait_until_ready()

    async def is_user_banned(self, guild_id: int, user: discord.User):
        """
        Helper function to check if an user is banned
        """
        if guild_id in self.ban_cache:
            return user.id in self.ban_cache[guild_id]
        return False

    @commands.command()
    async def link(self, ctx: commands.Context, channel: int, sync_bans: bool = True):
        """
        Link two Discord Text channels together
        """
        if ctx.channel.permissions_for(ctx.author).manage_channels:
            async with aiosqlite.connect("runtime/intercom.db") as database:
                cursor = await database.cursor()

                # Check if this set of channels is already linked (both ways)
                check = await cursor.execute(
                    "SELECT * FROM intercom WHERE (peer1=? AND peer2=?) OR (peer1=? AND peer2=?)",
                    (ctx.channel.id, channel, channel, ctx.channel.id),
                )
                check = await check.fetchone()
                if check is not None:
                    return await ctx.send("The target channel is already linked!")

                target = discord.utils.find(
                    lambda m: m.id == channel, self.all_channels
                )

                if target is None:
                    return await ctx.send(
                        str(
                        "Invalid channel ID or this bot cannot see the target channel "
                        "(If you just created the target channel, please wait about 5 minutes)!"
                        )
                    )

                if target == ctx.channel:
                    return await ctx.send("You can't link to yourself!")

                if (
                    ctx.channel.type != discord.ChannelType.text
                    or target.type != discord.ChannelType.text
                ):
                    return await ctx.send("You can only link text channels!")

                # Check if the target server silenced the initiating server
                # or the initiator failed too many times
                check = await cursor.execute(
                    "SELECT * FROM silent_list WHERE gid=? AND silent_gid=?",
                    (target.guild.id, ctx.guild.id),
                )
                check = await check.fetchone()
                if check is not None:
                    # Throw a dubious error message
                    return await ctx.send("You can only link text channels!")

                check = await cursor.execute(
                    "SELECT * FROM fail2ban WHERE gid=? AND target_gid=?",
                    (ctx.guild.id, target.guild.id),
                )
                check = await check.fetchone()
                if check is not None:
                    # If more than 3 fails, throw a dubious error message
                    if check[3] >= 3:
                        await ctx.send("You can only link text channels!")
                        # ... and silent the initiating server from the target
                        await cursor.execute(
                            "INSERT INTO silent_list (gid, silent_gid) VALUES (?, ?)",
                            (target.guild.id, ctx.guild.id),
                        )
                        # remove the counter
                        await cursor.execute(
                            "DELETE FROM fail2ban WHERE gid=? AND target_gid=?",
                            (ctx.guild.id, target.guild.id),
                        )
                        await database.commit()

                # Warn and disable sync_bans if we don't have the required permissions
                if (
                    not ctx.channel.permissions_for(ctx.guild.me).ban_members
                    and sync_bans
                ):
                    sync_bans = False
                    await ctx.send(
                        str(
                        "Since the Ban Members permission is not granted to the bridge "
                        "(we need it to access the banned member list), "
                        "we won't be able to sync bans! Proceed with caution!"
                        )
                    )

                # Generate 6 random digits (prevents people from guessing the link)
                random_string = "".join(random.choice(string.digits) for _ in range(6))

                def verify_target(msg):
                    return (
                        msg.channel == target
                        and msg.channel.permissions_for(msg.author).manage_channels
                        and msg.content == random_string
                    )

                try:
                    await ctx.send("Waiting for confirmation...")

                    embed = discord.Embed(
                        title="Linking request",
                        description=str("There is a request to link to this channel "
                           f"from #{ctx.channel.name} (server: {ctx.guild.name})"),
                        color=0x00FF00,
                    )
                    embed.set_footer(
                        text=f"Type {random_string} to confirm or wait 30 seconds to cancel"
                    )
                    await target.send(embed=embed)

                    await self.client.wait_for(
                        "message", check=verify_target, timeout=30
                    )
                except asyncio.TimeoutError:
                    await target.send("Timeout!")
                    # Add a fail counter or increment it
                    check = await cursor.execute(
                        "SELECT * FROM fail2ban WHERE gid=? AND target_gid=?",
                        (ctx.guild.id, target.guild.id),
                    )
                    check = await check.fetchone()
                    if check is None:
                        await cursor.execute(
                            "INSERT INTO fail2ban (gid, target_gid, count) VALUES (?, ?, 1)",
                            (ctx.guild.id, target.guild.id),
                        )
                    else:
                        await cursor.execute(
                            "UPDATE fail2ban SET count=count+1 WHERE gid=? AND target_gid=?",
                            (ctx.guild.id, target.guild.id),
                        )
                    await database.commit()
                    return await ctx.send(
                        "The other side did not confirm this activity"
                    )

                await ctx.send("Linking...")

                # Clears any fail counter
                await cursor.execute(
                    "DELETE FROM fail2ban WHERE gid=? AND target_gid=?",
                    (ctx.guild.id, target.guild.id),
                )

                source_wh = await cursor.execute(
                    "SELECT * FROM webhooks_urls WHERE id=?", (ctx.channel.id,)
                )
                webhook_url = await source_wh.fetchone()

                if webhook_url is None:
                    webhook_url = await ctx.channel.create_webhook(
                        name=f"Intercom_{ctx.channel.name}"
                    )
                    await cursor.execute(
                        "INSERT INTO webhooks_urls VALUES (?, ?, ?)",
                        (ctx.channel.id, webhook_url.url, ctx.channel.guild.id),
                    )

                target_wh = await cursor.execute(
                    "SELECT * FROM webhooks_urls WHERE id=?", (target.id,)
                )
                webhook_url = await target_wh.fetchone()
                if webhook_url is None:
                    webhook_url = await target.create_webhook(
                        name=f"Intercom_{target.name}"
                    )
                    await cursor.execute(
                        "INSERT INTO webhooks_urls VALUES (?, ?, ?)",
                        (target.id, webhook_url.url, ctx.channel.guild.id),
                    )

                await cursor.execute(
                    """
                    INSERT INTO intercom 
                    (peer1, peer2, peer1_gid, peer2_gid, active, sync_bans) 
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        ctx.channel.id,
                        channel,
                        ctx.guild.id,
                        target.guild.id,
                        1,
                        sync_bans,
                    ),
                )
                await database.commit()
                await ctx.send("Successfully linked!")
                return await target.send(
                    str(
                    f"The channel {ctx.guild.name}/{ctx.channel.name} "
                    "has been successfully linked with this channel!"
                    )
                )
        else:
            await ctx.send("You don't have permission to do that!")

    @commands.command()
    async def unlink(self, ctx: commands.Context, channel: int):
        """
        Unlink a channel from the current channel
        """
        unlink_candidate = []
        if ctx.channel.permissions_for(ctx.author).manage_channels:
            async with aiosqlite.connect("runtime/intercom.db") as database:
                cursor = await database.cursor()
                await cursor.execute(
                    "SELECT * FROM intercom WHERE (peer1=? AND peer2=?) OR (peer1=? AND peer2=?)",
                    (ctx.channel.id, channel, channel, ctx.channel.id),
                )
                candidate = await cursor.fetchone()
                if candidate is not None:
                    unlink_candidate.append(candidate)

                if len(unlink_candidate) == 0:
                    return await ctx.send("You are not linked!")

                await cursor.execute(
                    "DELETE FROM intercom WHERE (peer1=? AND peer2=?) OR (peer1=? AND peer2=?)",
                    (ctx.channel.id, channel, channel, ctx.channel.id),
                )
                async with aiohttp.ClientSession() as session:
                    for unlink in unlink_candidate:
                        if unlink is None:
                            continue
                        target = discord.utils.find(
                            lambda m: m.id == unlink[1], self.all_channels # pylint: disable=cell-var-from-loop
                        )
                        if target is None:
                            continue
                        target_wh = await cursor.execute(
                            "SELECT * FROM webhooks_urls WHERE id=?", (target.id,)
                        )
                        webhook_url = await target_wh.fetchone()
                        if webhook_url is None:
                            continue
                        webhook = discord.Webhook.from_url(
                            webhook_url[1], session=session
                        )
                        await webhook.delete()
                        await cursor.execute(
                            "DELETE FROM webhooks_urls WHERE id=?", (target.id,)
                        )
                await database.commit()
                await ctx.send("Successfully unlinked!")

    @commands.command()
    async def togglelink(self, ctx, channel: int):
        """
        Change the state of the link between channels (toggle active bit)
        """
        toggle_candidate = []
        if ctx.channel.permissions_for(ctx.author).manage_channels:
            async with aiosqlite.connect("runtime/intercom.db") as database:
                cursor = await database.cursor()
                await cursor.execute(
                    "SELECT * FROM intercom WHERE peer1=? AND peer2=?",
                    (ctx.channel.id, channel),
                )
                candidate = await cursor.fetchone()
                if candidate is not None:
                    toggle_candidate.append(candidate)
                await cursor.execute(
                    "SELECT * FROM intercom WHERE peer1=? AND peer2=?",
                    (channel, ctx.channel.id),
                )
                candidate = await cursor.fetchone()
                if candidate is not None:
                    toggle_candidate.append(candidate)

                if len(toggle_candidate) == 0:
                    return await ctx.send("You are not linked!")

                await cursor.execute(
                    "UPDATE intercom SET active=? WHERE peer1=? AND peer2=?",
                    (1 - toggle_candidate[0][5], ctx.channel.id, channel),
                )
                await cursor.execute(
                    "UPDATE intercom SET active=? WHERE peer1=? AND peer2=?",
                    (1 - toggle_candidate[0][5], channel, ctx.channel.id),
                )
                await database.commit()
                await ctx.send("Successfully toggled!")

    @commands.command()
    async def listlinks(self, ctx):
        """
        List all the channels that are linked to this channel
        """
        async with aiosqlite.connect("runtime/intercom.db") as database:
            cursor = await database.cursor()
            await cursor.execute(
                "SELECT peer1, peer2 FROM intercom WHERE peer1=? OR peer2=?",
                (ctx.channel.id, ctx.channel.id),
            )
            result = await cursor.fetchall()
            if len(result) == 0:
                return await ctx.send("You are not linked!")

            for row in result:
                if row is None:
                    continue
                if row[0] == ctx.channel.id:
                    source = ctx.channel
                    channel_id = row[1]
                    target = discord.utils.find(
                        lambda m: m.id == channel_id, self.all_channels # pylint: disable=cell-var-from-loop
                    )
                    await ctx.send(
                        f"`#{source.name}` ↔️ `#{target}` (`{target.id}@{target.guild.name}`)"
                    )
                else:
                    source = ctx.channel
                    target = discord.utils.find(
                        lambda m: m.id == row[0], self.all_channels #pylint: disable=cell-var-from-loop
                    )
                    await ctx.send(
                        f"`#{source.name}` ↔️ `#{target}` (`{target.id}-{target.guild.name}`)"
                    )

    @commands.command()
    async def toggle_ban_sync(self, ctx):
        """
        Toggle the ban sync for this channel
        """
        if ctx.channel.permissions_for(ctx.author).manage_channels:
            # Check if we have the Ban User permission (needed to sync bans)
            if not ctx.channel.permissions_for(ctx.guild.me).ban_members:
                return await ctx.send(
                    "The Ban Members permission is necessary to access the ban list!"
                )
            async with aiosqlite.connect("runtime/intercom.db") as database:
                cursor = await database.cursor()
                await cursor.execute(
                    "SELECT * FROM intercom WHERE peer1=? OR peer2=?",
                    (ctx.channel.id, ctx.channel.id),
                )
                result = await cursor.fetchall()
                if len(result) == 0:
                    return await ctx.send("You are not linked!")

                await cursor.execute(
                    "UPDATE intercom SET ban_sync=? WHERE peer1=? OR peer2=?",
                    (1 - result[0][6], ctx.channel.id, ctx.channel.id),
                )
                await database.commit()
                await ctx.send("Successfully toggled!")

    @commands.command()
    async def toggle_silent(self, ctx, guild_id: int):
        """
        Disallow another server from sending link requests to this server
        """
        if ctx.channel.permissions_for(ctx.author).manage_channels:
            async with aiosqlite.connect("runtime/intercom.db") as database:
                cursor = await database.cursor()
                await cursor.execute(
                    "SELECT * FROM silent_list WHERE gid=? AND silent_gid=?",
                    (ctx.guild.id, guild_id),
                )
                result = await cursor.fetchone()
                if result is None:
                    await cursor.execute(
                        "INSERT INTO silent_list (gid, silent_gid) VALUES (?, ?)",
                        (ctx.guild.id, guild_id),
                    )
                    await database.commit()
                    await ctx.send(f"Successfully silenced `{guild_id}`!")
                else:
                    await cursor.execute(
                        "DELETE FROM silent_list WHERE gid=? AND silent_gid=?",
                        (ctx.guild.id, guild_id),
                    )
                    await database.commit()
                    await ctx.send(f"Successfully unsilenced `{guild_id}`!")

    @commands.Cog.listener()
    async def on_ready(self):
        """
        Handle ready event
        """
        # pylint: disable=no-member
        self.update_channels.start()
        self.update_ban_cache.start()
        # pylint: enable=no-member

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        """
        Handle messages
        """
        # Ignore: self, commands provided by self, bots,
        # discord invites & gifts and webhooks
        if (
            message.author.bot
            or message.author == self.client.user
            or message.content.startswith(self.client.command_prefix)
            or message.content.startswith("https://discord.gg/")
            or message.content.startswith("https://discord.com/invite/")
            or message.content.startswith("https://discordapp.com/invite/")
            or message.content.startswith("https://discord.gift/")
            or message.webhook_id is not None
        ):
            return
        files = [await attachment.to_file() for attachment in message.attachments]
        embeds = list(message.embeds)

        # Ignore message if EVERYTHING is empty at this point
        # (we can't support everything, eg. Stickers)
        if len(files) == 0 and len(embeds) == 0 and message.content == "":
            return

        # print(f"{message.channel.name}/{message.author.name}/{message.content}")
        async with aiosqlite.connect("runtime/intercom.db") as database:
            cursor = await database.cursor()
            await cursor.execute(
                "SELECT * FROM intercom WHERE (peer1=? AND active=1) OR (peer2=? AND active=1)",
                (message.channel.id, message.channel.id),
            )
            targets = await cursor.fetchall()
            if len(targets) == 0:
                return

            # print(targets)

            async with aiohttp.ClientSession() as session:
                for target in targets:
                    # if the targets has sync_bans enabled,
                    # check if the author is banned in the target channel
                    target_cid = (
                        target[1] if target[1] != message.channel.id else target[2]
                    )
                    target_gid = (
                        target[3] if target[1] != message.channel.id else target[4]
                    )

                    if target[6] == 1:
                        if await self.is_user_banned(target_gid, message.author):
                            continue

                    target = discord.utils.find(
                        lambda m: m.id == target_cid, self.all_channels #pylint: disable=cell-var-from-loop``
                    )
                    webhook = ""
                    rows = await cursor.execute(
                        "SELECT url FROM webhooks_urls WHERE id=?", (target.id,)
                    )
                    row = await rows.fetchone()
                    if row is None:
                        webhook = await target.create_webhook(
                            name=f"Intercom_{target.name}"
                        )
                        await cursor.execute(
                            "INSERT INTO webhooks_urls VALUES (?, ?, ?)",
                            (target.id, webhook.url, target.guild.id),
                        )
                        await database.commit()
                        webhook = webhook.url
                    else:
                        webhook = row[0]

                    # print(webhook)

                    webhook = discord.Webhook.from_url(webhook, session=session)
                    await webhook.send(
                        content=message.content,
                        files=files,
                        embeds=embeds,
                        avatar_url=message.author.avatar.url,
                        username=f"{message.author.name} @ {message.guild.name}",
                    )

    @commands.Cog.listener()
    async def on_guild_join(self):
        """
        Update the list of channels when the bot joins a new guild
        """
        self.all_channels = list(self.client.get_all_channels())

    @commands.Cog.listener()
    async def on_guild_channel_delete(self, channel):
        """
        Update the list of channels when a channel is deleted
        """
        async with aiosqlite.connect("runtime/intercom.db") as database:
            cursor = await database.cursor()
            await cursor.execute(
                "DELETE FROM intercom WHERE peer1=? OR peer2=?",
                (channel.id, channel.id),
            )
            await cursor.execute("DELETE FROM webhooks_urls WHERE id=?", (channel.id,))
            self.all_channels = list(self.client.get_all_channels())
            await database.commit()

    @commands.Cog.listener()
    async def on_guild_remove(self, guild):
        """
        Update the list of channels when the bot leaves a guild
        """
        async with aiosqlite.connect("runtime/intercom.db") as database:
            cursor = await database.cursor()
            await cursor.execute(
                "DELETE FROM intercom WHERE peer1_gid=? OR peer2_gid=?",
                (guild.id, guild.id),
            )
            await cursor.execute("DELETE FROM webhooks_urls WHERE gid=?", (guild.id,))
            self.all_channels = list(self.client.get_all_channels())
            return await database.commit()

    @commands.Cog.listener()
    async def on_member_ban(self, guild):
        """
        Hook the user banning event to update the ban cache for that guild
        """
        # Update the ban cache for that guild
        self.ban_cache[guild.id] = [ban.user.id for ban in await guild.bans().flatten()]

    @commands.Cog.listener()
    async def on_member_unban(self, guild):
        """
        Hook the user unbanning event to update the ban cache for that guild
        """
        # Update the ban cache for that guild
        self.ban_cache[guild.id] = [ban.user.id for ban in await guild.bans().flatten()]


def setup(client):
    """
    Setup the cog
    """
    client.add_cog(Intercom(client))
