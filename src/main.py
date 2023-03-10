"""
Main script to launch the bot
"""
import configparser
import os
import sys

import discord
from discord.ext import commands

if not os.path.isdir("runtime"):
    os.mkdir("runtime")
    config = configparser.ConfigParser()
    config["Credentials"] = {
        "discord_token": "",
    }
    with open("runtime/config.ini", "w", encoding="utf-8") as f:
        config.write(f)
    print("Created runtime directory. Please populate your credentials")
    sys.exit(0)

config = configparser.ConfigParser()
config.read("runtime/config.ini")

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.bans = True


client = commands.Bot(command_prefix="$linktool.", intents=intents)

# Request the Message Content Privileged Intents


@client.event
async def on_ready():
    """
    Run when the bot is ready
    """
    print("Logged in as")
    print(client.user.name)
    print(client.user.id)
    print("------")
    await client.change_presence(
        activity=discord.Game(name="aboard the Universal Cereal Bus")
    )


client.load_extension("intercom")

client.run(config["Credentials"]["discord_token"])
