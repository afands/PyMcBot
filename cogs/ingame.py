import time
import asyncio
from collections import deque

import discord
from discord.ext import commands
from concurrent.futures.thread import ThreadPoolExecutor

from minecraft.exceptions import YggdrasilError

from utils.Player import Player


class PlayerWrapper(Player):
    def __init__(self, username, password, bot, channel):
        """
        Setup both the Parent & self init's. This init sets
        up all required things for maintaining a discord bot cog
        and sending messages to said cog at regular intervals.

        Parameters
        ----------
        username : str
            The Username / Email used to login
        password : str
            The password used to login to the account
        bot : commands.Bot
            Our bot instance, required for creating an instance of a discord cog
        guildId : int
            The id this Player is associated with

        See Also
        --------
        pyCraft.Player.Player.__init__()
            Called internally to set username & password easily.
        """
        super().__init__(username, password)

        self.queue = deque()
        self.ingame_cog = Ingame(bot)

        self.bot = bot
        self.channel = channel
        self.chat_breakout = False
        self.loop = asyncio.get_event_loop()
        self.ingame_cog.isPycraftInstance = True

    def SetServer(self, ip, port=25565, handler=None):
        """
        Override the parent SetServer so we can send chats to discord instead

        See Also
        --------
        pyCraft.Player.Player.SetServer()
            The parent class method we are overriding,
            but then calling within this method.
        """
        super().SetServer(ip, port=port, handler=self.ReceiveChat)

    def ReceiveChat(self, chat_packet):
        """
        Override the parent ReceiveChat functionality
        so we can send chats to discord instead

        See Also
        --------
        pyCraft.Player.Player.ReceiveChat()
            The parent class method we are overriding here.
        """
        message = self.Parser(chat_packet.json_data)
        if not message:
            # This means our Parser failed to extract the message
            return

        self.queue.append(message)

    def HandleChat(self):
        """Handles the queue and sends all relevant chats to discord every second

        This queries an internal queue of messages every second, taking up to
        5 messages per second to be processed before being passed off to a
        further method that handles the discord interaction.

        Notes
        -----
        This was built to avoid ratelimits and the need to sanitize output.

        In theory, this can maintain the flow of conversation generated by
        a minecraft chat while adhering to the ratelimits discord imposes.
        """
        while True:
            if self.chat_breakout:
                return

            time.sleep(1)
            messages = ""
            for i in range(5):
                try:
                    messages += f"{self.queue.popleft()}\n"
                except IndexError:
                    # Queue is empty but no worries
                    continue

            if messages != "":
                self.loop.create_task(
                    self.ingame_cog.SendChatToDiscord(self.bot, self.channel, messages)
                )


class Ingame(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.executor = ThreadPoolExecutor()
        self.player = None
        self.isPycraftInstance = False

    async def SendChatToDiscord(self, bot, channel, message):
        if not self.isPycraftInstance:
            # This should only be used by PlayerWrapper instances
            return

        if bot.player is None:
            return

        if channel is None:
            channel = bot.get_channel(bot.channel)
        await channel.send(embed=discord.Embed.from_dict({"description": message}))

    @commands.Cog.listener()
    async def on_ready(self):
        print(f"{self.__class__.__name__} cog has been loaded\n-----")

        # Connect the account on ready
        if self.isPycraftInstance is False and self.bot.player is None:
            channel = await self.bot.fetch_channel(self.bot.channel)
            await self.connect(channel)

    @commands.Cog.listener()
    async def on_message(self, message):
        if message.author.bot:
            return

        if not message.guild:
            return

        if self.bot.player is None:
            return

        if message.content.startswith(self.bot.PREFIX):
            return

        # TODO clean this content so it sends names rather then <@123413412> etc shit
        # msg = f"{message.author.display_name} -> {message.content}"
        self.bot.player.SendChat(message.content)

        try:
            await message.delete()
        except discord.errors.NotFound:
            pass

    @commands.command(
        name="connect", description="Connect a minecraft account to the server",
    )
    @commands.guild_only()
    @commands.has_guild_permissions(administrator=True)
    async def connect(self, ctx):
        try:
            await ctx.message.delete()
        except discord.HTTPException:
            pass
        except AttributeError:
            # For our initial setup, this will trip
            pass

        if self.bot.player is not None:
            await ctx.send(
                "A connection should already be established. Kill it with the `disconnect` command if wanted."
            )
            return

        try:
            channel = await self.bot.fetch_channel(self.bot.channel)
            player = PlayerWrapper(
                self.bot.username, self.bot.password, self.bot, channel
            )
        except YggdrasilError as e:
            await ctx.send(f"Login failure: `{e}`")
        else:
            if " " in self.bot.server:
                ip, port = self.bot.server.split(" ")
                player.SetServer(ip, port=int(port))
            else:
                player.SetServer(self.bot.server)
            futures = []
            futures.append(self.executor.submit(player.Connect))
            futures.append(self.executor.submit(player.HandleChat))
            self.bot.player = player
            await ctx.send(
                f"`{self.bot.player.auth_token.username}` should have connected to `{self.bot.server}`",
                delete_after=15,
            )

            # Check for thread errors
            """
            for _ in range(15):
                await asyncio.sleep(2.5)
                for future in concurrent.futures.as_completed(futures):
                    try:
                        print(future.result())
                    except ProxyConnection as e:
                        print(e)
                    except Exception as e:
                        print(e)
            """

    @commands.command(
        name="disconnect", description="Disconnect your account from the server"
    )
    @commands.guild_only()
    @commands.has_guild_permissions(administrator=True)
    async def disconnect(self, ctx):
        try:
            await ctx.message.delete()
        except discord.HTTPException:
            pass

        if self.bot.player is None:
            await ctx.send("A connection is not already established.")
            return

        try:
            self.bot.player.Disconnect()
        except OSError:
            pass
        self.bot.player = None
        await ctx.send("The account should have disconnected", delete_after=15)

    @commands.command(
        name="sudo", description="Get your account to say something!", usage="<message>"
    )
    @commands.guild_only()
    @commands.has_guild_permissions(administrator=True)
    async def sudo(self, ctx, *, message):
        if self.bot.player is None:
            await ctx.send("A connection is not already established.")
            return

        self.bot.player.SendChat(message)

        await ctx.send(
            f"`{self.bot.player.auth_token.username}` should have said: `{message}`"
        )


def setup(bot):
    bot.add_cog(Ingame(bot))
