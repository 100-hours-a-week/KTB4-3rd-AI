# 텔레그램·디스코드 권한: env Admin_Telegram_ID·Admin_Discord_ID(쉼표로 여러 명) = admin, tg_members approved·디스코드 채널에서 쓴 사람 = member, 그 밖은 None(/join만 가능).
import logging

log = logging.getLogger("app.Server_Monitor_Bot.roles")
ADMIN_ONLY = ("action.member_list", "action.member_approve", "action.member_reject", "action.drill_guess", "action.doc_upload",
              "action.doc_delete", "action.doc_list")
ANYONE = ("action.member_join",)


def role_of(user_id: int | None, settings, members, chat_id: int | None = None) -> str | None:
    if user_id is None:
        return None
    if settings.is_admin(user_id):
        return "admin"
    if chat_id is not None and chat_id == settings.discord_channel_id:
        return "member"
    try:
        return "member" if members.is_member(user_id) else None
    except Exception as exc:
        log.warning("member lookup failed: error=%s", type(exc).__name__)
        return None


def allowed(role: str | None, event_type: str) -> bool:
    if event_type in ANYONE:
        return True
    if role == "admin":
        return True
    return role == "member" and event_type not in ADMIN_ONLY
