from swap import FILE_QUEUE, RECORDER
from configs import DISCORD_CONFIG
from utils import Thread, Json

from asyncio import all_tasks, CancelledError, create_task, gather, get_event_loop, sleep as asleep
from io import BytesIO
from logging import getLogger
from os import stat
from os.path import split
from traceback import format_exception as os_format_exception, format_exc
from typing import Optional

from aiofiles import open as aopen
from aiohttp import ClientSession
from discord import ApplicationContext, DiscordException, File, Intents
from discord.ext.bridge import Bot
from discord.ext.commands import CommandError, Context, when_mentioned_or


MAIN_LOGGER = getLogger("main")

def format_exception(exc: Exception):
    return os_format_exception(type(exc), exc, exc.__traceback__)


class DiscordBot(Bot):
    def __init__(self, *args, **kwargs):
        intents = Intents.default()
        intents.message_content = True
        super().__init__(*args, command_prefix=when_mentioned_or(*DISCORD_CONFIG.prefixs), intents=intents, **kwargs)

        self.__logger = getLogger("discord")
        self.__thread = None
        self.__first_connect = True

        self.load_extension("discord_bot.cog_manger")

    async def on_ready(self):
        if self.__first_connect:
            self.__first_connect = False
            self.__logger.warning(f"Discord Bot {self.user} Start.")

            self.loop = get_event_loop()
            self.loop.create_task(self.send_video())
            self.loop.create_task(self.crawer())
            
            self.channel = self.get_channel(DISCORD_CONFIG.channel)
        else:
            self.__logger.warning(f"Discord Bot {self.user} Reconnect.")

    async def on_disconnect(self):
        self.__logger.warning(f"Discord Bot {self.user} Disconnect.")

    async def send_video(self):
        while True:
            file_path = await FILE_QUEUE.get()
            file_size = stat(file_path).st_size
            self.__logger.info("Get File: {}".format(file_path))
            if file_size > 8000000:
                self.__logger.warning("File {} too big to send...".format(file_path))
                await self.channel.send("File {} too big to send...".format(file_path))
            else:
                self.__logger.info("Send File...")
                file_name = split(file_path)
                io = BytesIO(b"")
                async with aopen(file_path, mode="rb") as video:
                    io.write(await video.read())
                io.seek(0)
                await self.channel.send(content="People Detect!", file=File(io, file_name[1]))
                self.__logger.info("Send File Successful!")

    async def crawer(self):
        client = ClientSession()

        w = False
        s = False
        c = 0
        while True:
            try:
                res = await client.get("http://localhost:8080/face-data")
                data = await res.json(loads=Json.loads)
                if len(data) != 0:
                    if not w:
                        MAIN_LOGGER.warning(f"Detect People: {len(data)}")
                    c = 0
                    w = True
                else:
                    c += 1

                if w and c > 25:
                    res = RECORDER.stop_record()
                    if res:
                        await FILE_QUEUE.put(res)
                    s = False
                    w = False

                if w and not s:
                    RECORDER.start_record()
                    s = True
                await asleep(0.2)
            except CancelledError:
                break

        await client.close()

    # Log Handler
    async def on_command(self, ctx: Context):
        self.__logger.info(f"[Command] {ctx.author}: {ctx.message.content}")

    async def on_application_command(self, ctx: ApplicationContext):
        self.__logger.info(
            f"[Command] {ctx.author}: {ctx.command.qualified_name}")

    # Error Handler
    async def on_error(self, event_method: str, *args, **kwargs) -> None:
        message = f"Error in {event_method}\n"
        message += format_exc()
        self.__logger.error(message)

    async def on_command_error(self, ctx: Context, exception: CommandError) -> None:
        res = "".join(format_exception(exception))
        self.__logger.error(res)
        error_message = "Error:```" + res + "```"
        if len(error_message) >= 2000:
            io = BytesIO(res.encode())
            await ctx.reply(content="Error:", file=File(io, filename="error.log"))
        else:
            await ctx.reply(content=error_message, mention_author=False)

    async def on_application_command_error(self, ctx: ApplicationContext, exception: DiscordException) -> None:
        res = "".join(format_exception(exception))
        self.__logger.error(res)
        error_message = "Error:```" + res + "```"
        if len(error_message) >= 2000:
            io = BytesIO(res.encode())
            await ctx.respond(content="Error:", file=File(io, filename="error.log"), ephemeral=True)
        else:
            await ctx.respond(content=error_message, ephemeral=True)

    def __thread_job(self):
        try:
            self.run(token=DISCORD_CONFIG.token)
        except SystemExit:
            for task in all_tasks(self.loop):
                task.cancel()
            self.loop.stop()
        except Exception as exc:
            MAIN_LOGGER.error(format_exception(exc))
        self.loop.close()

    def startup(self):
        if self.__thread != None:
            return
        self.__thread = Thread(target=self.__thread_job, name="Discord")
        self.__thread.start()
