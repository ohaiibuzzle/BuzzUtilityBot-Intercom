from discord import webhook
from discord.ext import commands, tasks
import sqlite3, aiosqlite, asyncio, os
import discord
import aiohttp
from discord.ext.commands.core import command


class Intercom(commands.Cog):
    def __init__(self, client):
        def setup_database():
            if not os.path.exists("runtime/intercom.db"):
                conn = sqlite3.connect("runtime/intercom.db")
                c = conn.cursor()
                c.execute(
                    """
                    CREATE TABLE IF NOT EXISTS intercom 
                    (id INTEGER PRIMARY KEY AUTOINCREMENT, 
                    peer1 INTEGER, peer2 INTEGER, 
                    peer1_gid INTEGER, peer2_gid INTEGER, 
                    active INTEGER DEFAULT 1)
                    """
                )
                c.execute(
                    "CREATE TABLE IF NOT EXISTS webhooks_urls (id INTEGER PRIMARY KEY, url TEXT, gid INTEGER)"
                )
                conn.commit()
                conn.close()

        self.client = client
        setup_database()

    @tasks.loop(seconds=300)
    async def update_channels(self):
        print("Updating channels")
        self.all_channels = [channel for channel in self.client.get_all_channels()]
        print("Done")

    @commands.command()
    async def link(self, ctx: commands.Context, channel: int):
        if ctx.author.permissions_in(ctx.channel).manage_channels:
            async with aiosqlite.connect("runtime/intercom.db") as db:
                c = await db.cursor()
                await c.execute(
                    "SELECT * FROM intercom WHERE peer1=? AND peer2=?",
                    (ctx.channel.id, channel),
                )
                if await c.fetchone() is not None:
                    return await ctx.send("You are already linked!")
                await c.execute(
                    "SELECT * FROM intercom WHERE peer1=? AND peer2=?",
                    (channel, ctx.channel.id),
                )
                if await c.fetchone() is not None:
                    return await ctx.send("You are already linked!")

                target = discord.utils.find(
                    lambda m: m.id == channel, self.all_channels
                )

                if target == ctx.channel:
                    return await ctx.send("You can't link to yourself!")

                if target is None:
                    return await ctx.send(
                        "Invalid channel ID or this bot cannot see that channel (If you just created this channel, please wait about 5 minutes)!"
                    )

                if (
                    ctx.channel.type != discord.ChannelType.text
                    or target.type != discord.ChannelType.text
                ):
                    return await ctx.send("You can only link text channels!")

                def verify_target(msg):
                    return (
                        msg.channel == target
                        and msg.author.permissions_in(target).manage_channels
                        and msg.content == "Confirm"
                    )

                try:
                    await ctx.send("Waiting for confirmation...")
                    await target.send(
                        f"There is a request to link this channel! (channel: {ctx.channel.name}, server: {ctx.guild.name})"
                    )
                    await target.send(
                        f"This request was created by @{ctx.author.name}#{ctx.author.discriminator}"
                    )
                    await target.send(
                        f"Please type `Confirm` in this channel to confirm"
                    )
                    msg = await self.client.wait_for(
                        "message", check=verify_target, timeout=30
                    )
                except asyncio.TimeoutError:
                    await target.send("Timeout!")
                    return await ctx.send(
                        "The other side did not confirm this activity"
                    )
                else:
                    await ctx.send("Linking...")

                    source_wh = await c.execute(
                        "SELECT * FROM webhooks_urls WHERE id=?", (ctx.channel.id,)
                    )
                    webhook_url = await source_wh.fetchone()

                    if webhook_url is None:
                        webhook_url = await ctx.channel.create_webhook(
                            name=f"Intercom_{ctx.channel.name}"
                        )
                        await c.execute(
                            "INSERT INTO webhooks_urls VALUES (?, ?, ?)",
                            (ctx.channel.id, webhook_url.url, ctx.channel.guild.id),
                        )

                    target_wh = await c.execute(
                        "SELECT * FROM webhooks_urls WHERE id=?", (target.id,)
                    )
                    webhook_url = await target_wh.fetchone()
                    if webhook_url is None:
                        webhook_url = await target.create_webhook(
                            name=f"Intercom_{target.name}"
                        )
                        await c.execute(
                            "INSERT INTO webhooks_urls VALUES (?, ?, ?)",
                            (target.id, webhook_url.url, ctx.channel.guild.id),
                        )

                    await c.execute(
                        "INSERT INTO intercom (peer1, peer2, peer1_gid, peer2_gid, active) VALUES (?, ?, ?, ?, ?)",
                        (ctx.channel.id, channel, ctx.guild.id, target.guild.id, 1),
                    )
                    await db.commit()
                    await ctx.send("Successfully linked!")
                    return await target.send(
                        f"The channel {ctx.guild.name}/{ctx.channel.name} has been successfully linked with this channel!"
                    )
        else:
            await ctx.send("You don't have permission to do that!")

    @commands.command()
    async def unlink(self, ctx: commands.Context, channel: int):
        unlink_candidate = []
        if ctx.author.permissions_in(ctx.channel).manage_channels:
            async with aiosqlite.connect("runtime/intercom.db") as db:
                c = await db.cursor()
                await c.execute(
                    "SELECT * FROM intercom WHERE peer1=? AND peer2=?",
                    (ctx.channel.id, channel),
                )
                candidate = await c.fetchone()
                if candidate is not None:
                    unlink_candidate.append(candidate)
                await c.execute(
                    "SELECT * FROM intercom WHERE peer1=? AND peer2=?",
                    (channel, ctx.channel.id),
                )
                candidate = await c.fetchone()
                if candidate is not None:
                    unlink_candidate.append(candidate)

                if unlink_candidate.__len__() == 0:
                    return await ctx.send("You are not linked!")

                await c.execute(
                    "DELETE FROM intercom WHERE peer1=? AND peer2=?",
                    (ctx.channel.id, channel),
                )
                await c.execute(
                    "DELETE FROM intercom WHERE peer1=? AND peer2=?",
                    (channel, ctx.channel.id),
                )
                async with aiohttp.ClientSession() as session:
                    for unlink in unlink_candidate:
                        if unlink is None:
                            continue
                        target = discord.utils.find(
                            lambda m: m.id == unlink[1], self.all_channels
                        )
                        if target is None:
                            continue
                        target_wh = await c.execute(
                            "SELECT * FROM webhooks_urls WHERE id=?", (target.id,)
                        )
                        webhook_url = await target_wh.fetchone()
                        if webhook_url is None:
                            continue
                        webhook = discord.Webhook.from_url(
                            webhook_url[1], adapter=discord.AsyncWebhookAdapter(session)
                        )
                        await webhook.delete()
                        await c.execute(
                            "DELETE FROM webhooks_urls WHERE id=?", (target.id,)
                        )
                await db.commit()
                await ctx.send("Successfully unlinked!")

    @commands.command()
    async def togglelink(self, ctx, channel: int):
        """
        Change the state of the link between channels (toggle active bit)
        """
        toggle_candidate = []
        if ctx.author.permissions_in(ctx.channel).manage_channels:
            async with aiosqlite.connect("runtime/intercom.db") as db:
                c = await db.cursor()
                await c.execute(
                    "SELECT * FROM intercom WHERE peer1=? AND peer2=?",
                    (ctx.channel.id, channel),
                )
                candidate = await c.fetchone()
                if candidate is not None:
                    toggle_candidate.append(candidate)
                await c.execute(
                    "SELECT * FROM intercom WHERE peer1=? AND peer2=?",
                    (channel, ctx.channel.id),
                )
                candidate = await c.fetchone()
                if candidate is not None:
                    toggle_candidate.append(candidate)

                if toggle_candidate.__len__() == 0:
                    return await ctx.send("You are not linked!")

                await c.execute(
                    "UPDATE intercom SET active=? WHERE peer1=? AND peer2=?",
                    (1 - toggle_candidate[0][5], ctx.channel.id, channel),
                )
                await c.execute(
                    "UPDATE intercom SET active=? WHERE peer1=? AND peer2=?",
                    (1 - toggle_candidate[0][5], channel, ctx.channel.id),
                )
                await db.commit()
                await ctx.send("Successfully toggled!")

    @commands.command()
    async def listlinks(self, ctx):
        """
        List all the channels that are linked to this channel
        """
        async with aiosqlite.connect("runtime/intercom.db") as db:
            c = await db.cursor()
            await c.execute(
                "SELECT peer1, peer2 FROM intercom WHERE peer1=? OR peer2=?",
                (ctx.channel.id, ctx.channel.id),
            )
            result = await c.fetchall()
            if result.__len__() == 0:
                return await ctx.send("You are not linked!")

            for row in result:
                if row is None:
                    continue
                if row[0] == ctx.channel.id:
                    source = ctx.channel
                    target = discord.utils.find(
                        lambda m: m.id == row[1], self.all_channels
                    )
                    await ctx.send(
                        f"`#{source.name}` ↔️ `#{target}` (`{target.id}@{target.guild.name}`)"
                    )
                else:
                    source = ctx.channel
                    target = discord.utils.find(
                        lambda m: m.id == row[0], self.all_channels
                    )
                    await ctx.send(
                        f"`#{source.name}` ↔️ `#{target}` (`{target.id}-{source.guild.name}`)"
                    )

    @commands.Cog.listener()
    async def on_ready(self):
        self.update_channels.start()

    @commands.Cog.listener()
    async def on_message(self, message):
        if message.content.startswith("$linktool."):
            return
        if message.author.bot:
            return
        if message.content.startswith("https://discord."):
            return
        files = [await attachment.to_file() for attachment in message.attachments]
        embeds = [embed for embed in message.embeds]
        targets = []
        # print(f"{message.channel.name}/{message.author.name}/{message.content}")
        async with aiosqlite.connect("runtime/intercom.db") as db:
            c = await db.cursor()
            await c.execute(
                "SELECT * FROM intercom WHERE peer1=? AND active=1",
                (message.channel.id,),
            )
            for target in await c.fetchall():
                if target is not None:
                    targets.append(target[2])
            await c.execute(
                "SELECT * FROM intercom WHERE peer2=? AND active=1",
                (message.channel.id,),
            )
            for target in await c.fetchall():
                if target is not None:
                    targets.append(target[1])

            if targets.__len__() == 0:
                return

            # print(targets)

            async with aiohttp.ClientSession() as session:
                for target in targets:
                    target = discord.utils.find(
                        lambda m: m.id == target, self.all_channels
                    )
                    webhook = ""
                    rows = await c.execute(
                        "SELECT url FROM webhooks_urls WHERE id=?", (target.id,)
                    )
                    row = await rows.fetchone()
                    if row is None:
                        webhook = await target.create_webhook(
                            name=f"Intercom_{target.name}"
                        )
                        await c.execute(
                            "INSERT INTO webhooks_urls VALUES (?, ?, ?)",
                            (target.id, webhook.url, target.guild.id),
                        )
                        await db.commit()
                        webhook = webhook.url
                    else:
                        webhook = row[0]

                    print(webhook)

                    webhook = discord.Webhook.from_url(
                        webhook, adapter=discord.AsyncWebhookAdapter(session)
                    )
                    await webhook.send(
                        content=message.content,
                        files=files,
                        embeds=embeds,
                        avatar_url=message.author.avatar_url,
                        username=f"{message.author.name} @ {message.guild.name}",
                    )

    @commands.Cog.listener()
    async def on_guild_join(self, guild):
        self.all_channels = [channel for channel in self.client.get_all_channels()]

    @commands.Cog.listener()
    async def on_guild_channel_delete(self, channel):
        async with aiosqlite.connect("runtime/intercom.db") as db:
            c = await db.cursor()
            await c.execute(
                "DELETE FROM intercom WHERE peer1=? OR peer2=?",
                (channel.id, channel.id),
            )
            await c.execute("DELETE FROM webhooks_urls WHERE id=?", (channel.id,))
            self.all_channels = [channel for channel in self.client.get_all_channels()]
            await db.commit()

    @commands.Cog.listener()
    async def on_guild_remove(self, guild):
        async with aiosqlite.connect("runtime/intercom.db") as db:
            c = await db.cursor()
            await c.execute(
                "DELETE FROM intercom WHERE peer1_gid=? OR peer2_gid=?",
                (guild.id, guild.id),
            )
            await c.execute("DELETE FROM webhooks_urls WHERE gid=?", (guild.id,))
            self.all_channels = [channel for channel in self.client.get_all_channels()]
            return await db.commit()


def setup(client):
    client.add_cog(Intercom(client))
