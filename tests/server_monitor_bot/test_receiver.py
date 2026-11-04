# P7 수신: 텔레그램 업데이트 → 이벤트 매핑과 from.id 권한 확인(관리자·팀원, 미등록은 /join만).
from app.Server_Monitor_Bot.channels.telegram.receiver import to_event

ROLES = {1: "admin", 2: "member"}
CFG = ROLES.get


def msg(text, user=1, reply=None):
    m = {"message_id": 10, "from": {"id": user}, "chat": {"id": 99}, "text": text}
    if reply:
        m["reply_to_message"] = {"message_id": reply}
    return {"update_id": 5, "message": m}


def test_messages_and_commands():
    e = to_event(msg("왜 실패했어?", reply=3), CFG, lambda c, m: ("incident", "I-1") if m == 3 else None)
    assert e["type"] == "user.message" and e["payload"]["reply_ref"] == "incident:I-1" and e["chat_id"] == 99
    assert to_event(msg("/trace abc123"), CFG)["payload"]["request_id"] == "abc123"
    assert to_event(msg("/knowledge 429"), CFG)["type"] == "action.knowledge_list"
    assert to_event(msg("/drill start S-ab12", user=2), CFG)["type"] == "action.drill_start"
    assert to_event(msg("/drill guess OpenAI 한도"), CFG)["payload"]["arg"] == "OpenAI 한도"
    assert to_event(msg("/unknown"), CFG)["type"] == "action.help"


def test_permissions_and_callbacks():
    assert to_event(msg("hi", user=3), CFG) is None
    cb = {"update_id": 6, "callback_query": {"id": "c1", "from": {"id": 1}, "data": "ks:D-20261026-ab12",
                                             "message": {"message_id": 4, "chat": {"id": 99}}}}
    e = to_event(cb, CFG)
    assert e["type"] == "action.knowledge_save" and e["payload"]["draft_id"] == "D-20261026-ab12"
    cb["callback_query"]["data"] = "aa:A-20261026-zz99"
    assert to_event(cb, CFG)["type"] == "adapt.approved"
    cb["callback_query"]["from"]["id"] = 3
    assert to_event(cb, CFG) is None
    cb["callback_query"]["from"]["id"] = 1
    cb["callback_query"]["data"] = "ma:555"
    assert to_event(cb, CFG)["type"] == "action.member_approve" and to_event(cb, CFG)["payload"]["user_id"] == "555"


def test_unregistered_can_only_join():
    m = msg("/join", user=7)
    m["message"]["from"].update(first_name="길동", last_name="홍", username="hong")
    e = to_event(m, CFG)
    assert e["type"] == "action.member_join" and e["payload"]["name"] == "길동 홍" and e["payload"]["username"] == "hong"
    assert to_event(msg("/status", user=7), CFG) is None
    assert to_event(msg("/request"), CFG)["type"] == "action.member_list"
