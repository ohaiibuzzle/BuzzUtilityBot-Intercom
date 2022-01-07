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
        self.all_channels = self.client.get_all_channels()

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

                target = discord.utils.get(self.all_channels, id=channel)
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
                unlink_candidate.append(await c.fetchone())
                await c.execute(
                    "SELECT * FROM intercom WHERE peer1=? AND peer2=?",
                    (channel, ctx.channel.id),
                )
                unlink_candidate.append(await c.fetchone())

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
                await db.commit()
                await ctx.send("Successfully unlinked!")

    @commands.command()
    async def togglelink(self, ctx, channel: int):
        """
        Change the state of the link between channels (toggle active bit)
        """
        if ctx.author.permissions_in(ctx.channel).manage_channels:
            async with aiosqlite.connect("runtime/intercom.db") as db:
                c = await db.cursor()
                await c.execute(
                    "SELECT * FROM intercom WHERE peer1=? AND peer2=?",
                    (ctx.channel.id, channel),
                )
                if c.fetchone() is None:
                    return await ctx.send("You are not linked!")
                await c.execute(
                    "SELECT * FROM intercom WHERE peer1=? AND peer2=?",
                    (channel, ctx.channel.id),
                )
                if await c.fetchone() is None:
                    return await ctx.send("You are not linked!")
                await c.execute(
                    "UPDATE intercom SET active=? WHERE peer1=? AND peer2=?",
                    (1 - c.fetchone()[3], ctx.channel.id, channel),
                )
                await c.execute(
                    "UPDATE intercom SET active=? WHERE peer1=? AND peer2=?",
                    (1 - c.fetchone()[3], channel, ctx.channel.id),
                )
                await db.commit()
                await ctx.send("Successfully toggled!")

    @commands.Cog.listener()
    async def on_ready(self):
        self.update_channels.start()

    @commands.Cog.listener()
    async def on_message(self, message):
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
                targets.append(target[2])
            await c.execute(
                "SELECT * FROM intercom WHERE peer2=? AND active=1",
                (message.channel.id,),
            )
            for target in await c.fetchall():
                targets.append(target[1])

            if targets.__len__() == 0:
                return

            async with aiohttp.ClientSession() as session:
                for target in targets:
                    target = discord.utils.get(self.all_channels, id=target)
                    webhook = ""
                    webhook_urls = await c.execute(
                        "SELECT * FROM webhooks_urls WHERE id=?", (target.id,)
                    )
                    webhook_url = await webhook_urls.fetchone()
                    if webhook_url is None:
                        webhook = await target.create_webhook(
                            name=f"Intercom_{target.name}"
                        )
                        await c.execute(
                            "INSERT INTO webhooks_urls VALUES (?, ?, ?)",
                            (target.id, webhook.url, target.guild.id),
                        )
                        webhook = webhook.url
                    else:
                        webhook = webhook_url[1]

                    # print(webhook)

                    webhook = discord.Webhook.from_url(
                        webhook, adapter=discord.AsyncWebhookAdapter(session)
                    )
                    await webhook.send(
                        content=message.content,
                        files=files,
                        embeds=embeds,
                        avatar_url=message.author.avatar_url,
                        username=f"{message.author.name}",
                    )
                    await webhook.delete()

    @commands.Cog.listener()
    async def on_guild_join(self):
        self.all_channels = self.client.get_all_channels()

    @commands.Cog.listener()
    async def on_guild_remove(self, guild):
        async with aiosqlite.connect("runtime/intercom.db") as db:
            c = await db.cursor()
            await c.execute(
                "DELETE FROM intercom WHERE peer1_gid=? OR peer2_gid=?",
                (guild.id, guild.id),
            )
            await c.execute("DELETE FROM webhooks_urls WHERE gid=?", (guild.id,))
            return await db.commit()


def setup(client):
    client.add_cog(Intercom(client))
