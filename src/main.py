import discord
from discord.ext import commands
import configparser
import os

if not os.path.isdir("runtime"):
    os.mkdir("runtime")
    config = configparser.ConfigParser()
    config["Credentials"] = {
        "discord_token": "",
    }
    with open("runtime/config.ini", "w") as f:
        config.write(f)
    print("Created runtime directory. Please populate your credentials")
    exit(0)

config = configparser.ConfigParser()
config.read("runtime/config.ini")

client = commands.Bot(command_prefix="$linktool.")


@client.event
async def on_ready():
    print("Logged in as")
    print(client.user.name)
    print(client.user.id)
    print("------")
    await client.change_presence(
        activity=discord.Game(name="aboard the Universal Cereal Bus")
    )


client.load_extension("intercom")

client.run(config["Credentials"]["discord_token"])
