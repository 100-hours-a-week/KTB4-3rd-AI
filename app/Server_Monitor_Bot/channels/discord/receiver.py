# 디스코드 수신(리더만): 게이트웨이로 설정한 채널의 사람 메시지·버튼·첨부를 텔레그램 업데이트 모양으로 바꿔 같은 to_event로 이벤트를 만든다. 버튼은 3초 안에 먼저 응답한다.
import asyncio
import logging
import re
import threading
from pathlib import PurePath

from ...ops_agent.roles import role_of
from ...runtime.leader import backoff
from ..telegram.receiver import to_event

log = logging.getLogger("app.Server_Monitor_Bot.discord")


def message_update(message_id: int, channel_id: int, user_id: int, name: str | None, username: str | None, text: str,
                   reply_to: int | None = None, attachment: dict | None = None) -> dict:
    msg = {"message_id": message_id, "from": {"id": user_id, "first_name": name, "username": username}, "chat": {"id": channel_id},
           "text": text, "discord": True}
    if reply_to:
        msg["reply_to_message"] = {"message_id": reply_to}
    if attachment:
        msg["document"] = {"file_id": attachment["url"], "file_name": attachment["filename"], "file_size": attachment["size"]}
    return {"update_id": message_id, "message": msg}


def filename(name: str, title: str | None) -> str:
    ext = PurePath(name).suffix
    return name if not title else title if title.endswith(ext) else f"{title}{ext}"


def button_update(interaction_id: int, channel_id: int, message_id: int, user_id: int, name: str | None, data: str) -> dict:
    return {"update_id": interaction_id, "callback_query": {"id": f"dc:{interaction_id}", "from": {"id": user_id, "first_name": name},
                                                           "data": re.sub(r"~\d+$", "", data), "message": {"message_id": message_id, "chat": {"id": channel_id}}}}


class DiscordReceiver(threading.Thread):
    def __init__(self, ctx, stop_event: threading.Event):
        super().__init__(name="server-bot-discord", daemon=True)
        self.ctx, self.stop_event = ctx, stop_event

    def push(self, update: dict) -> None:
        settings, repos = self.ctx.settings, self.ctx.repos
        event = to_event(update, lambda uid: role_of(uid, settings, repos.members, settings.discord_channel_id), repos.coord.ref_of)
        if event:
            self.ctx.enqueue(event, None)

    def run(self) -> None:
        fails = 0
        while not self.stop_event.is_set():
            if not self.ctx.is_leader():
                self.stop_event.wait(5)
                continue
            self.ready = False
            try:
                asyncio.run(self.session())
                fails = 0
            except Exception as exc:
                fails = 0 if self.ready else fails + 1
                log.warning("discord receiver failed: error=%s fails=%s", type(exc).__name__, fails)
            self.stop_event.wait(backoff(5, fails))

    async def session(self) -> None:
        import discord

        channel = self.ctx.settings.discord_channel_id
        intents = discord.Intents.none()
        intents.guilds = intents.guild_messages = intents.message_content = True
        client = discord.Client(intents=intents)
        loop = asyncio.get_running_loop()

        @client.event
        async def on_ready():
            self.ready = True
            if client.get_channel(channel) is None:
                log.warning("discord channel not visible: channel=%s guilds=%s", channel, len(client.guilds))
            else:
                log.info("discord connected: channel=%s", channel)

        @client.event
        async def on_message(m):
            if m.author.bot or m.channel.id != channel:
                return
            att = m.attachments[0] if m.attachments else None
            update = message_update(m.id, channel, m.author.id, m.author.display_name, m.author.name, m.content or "",
                                    m.reference.message_id if m.reference else None,
                                    {"url": att.url, "filename": filename(att.filename, getattr(att, "title", None)), "size": att.size}
                                    if att else None)
            await loop.run_in_executor(None, self.push, update)

        @client.event
        async def on_interaction(i):
            if i.type != discord.InteractionType.component or i.channel_id != channel:
                return
            try:
                await i.response.defer()
            except discord.HTTPException as exc:
                log.warning("discord defer failed: status=%s", exc.status)
            update = button_update(i.id, channel, i.message.id if i.message else 0, i.user.id, i.user.display_name,
                                   (i.data or {}).get("custom_id", ""))
            await loop.run_in_executor(None, self.push, update)

        async def watch():
            while not self.stop_event.is_set() and self.ctx.is_leader():
                await asyncio.sleep(5)
            await client.close()

        async with client:
            watcher = asyncio.create_task(watch())
            try:
                await client.start(self.ctx.settings.discord_token)
            finally:
                watcher.cancel()
