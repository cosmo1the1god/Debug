import discord
from discord.ext import commands
from discord.commands import slash_command


class Ping(commands.Cog):
    def __init__(self, bot: discord.Bot):
        self.bot = bot

    @slash_command(name="ping", description="Get the latency of the bot")
    async def ping(self, ctx: discord.ApplicationContext):
        await ctx.respond(f"Pong! {round(self.bot.latency * 1000)} ms", ephemeral=True)


def setup(bot: discord.Bot):
    bot.add_cog(Ping(bot))
