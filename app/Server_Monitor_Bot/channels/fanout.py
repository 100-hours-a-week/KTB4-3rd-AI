# 텔레그램·디스코드 클라이언트 묶음: chat_id가 디스코드 채널이면 디스코드로, 아니면 텔레그램으로 보내고, 받는 곳 없는 발송은 둘 다로 보낸다. 나머지 속성은 텔레그램 것을 쓴다.


class Channels:
    def __init__(self, telegram, discord):
        self.telegram, self.discord = telegram, discord

    def __getattr__(self, name):
        return getattr(self.telegram, name)

    def pick(self, chat_id):
        return self.discord if chat_id is not None and chat_id == self.discord.channel_id else self.telegram

    def send(self, chat_id, text: str, buttons=None, reply_to=None, html: bool = False):
        if chat_id is None:
            sent = self.broadcast(text, buttons, html)
            return sent[0][1] if sent else None
        return self.pick(chat_id).send(chat_id, text, buttons, reply_to=reply_to, html=html)

    def broadcast(self, text: str, buttons=None, html: bool = False) -> list[tuple[int, int]]:
        return self.telegram.broadcast(text, buttons, html) + self.discord.broadcast(text, buttons, html)

    def edit(self, chat_id, message_id: int, text: str, buttons=None, html: bool = False) -> bool:
        return self.pick(chat_id).edit(chat_id, message_id, text, buttons, html=html)

    def edit_markup(self, chat_id, message_id: int, buttons=None) -> bool:
        return self.pick(chat_id).edit_markup(chat_id, message_id, buttons)

    def answer_callback(self, callback_id: str, text: str = "") -> None:
        if not str(callback_id).startswith("dc:"):
            self.telegram.answer_callback(callback_id, text)

    def fetch_file(self, file_id, max_bytes: int):
        return (self.discord if str(file_id).startswith("https://") else self.telegram).fetch_file(file_id, max_bytes)
