# 이벤트 타입과 담당(배분 표), 기능 스위치 대응.
ROUTES = {"alert.pipeline": "diagnose", "user.message": "chat", "user.trace": "chat", "knowledge.edit": "knowledge",
          "signal.policy": "propose", "adapt.approved": "apply", "adapt.observe": "apply"}
ACTIONS = ("action.knowledge_save", "action.knowledge_cancel", "action.adapt_reject", "action.adapt_rollback",
           "action.drill_start", "action.drill_stop", "action.drill_guess", "action.status", "action.flags",
           "action.knowledge_list", "action.help")
FEATURE = {"alert.pipeline": "F2", "user.message": "F3", "user.trace": "F4", "knowledge.edit": "F5", "signal.policy": "F7",
           "adapt.approved": "F7", "adapt.observe": "F7", "action.knowledge_save": "F5", "action.knowledge_cancel": "F5",
           "action.adapt_reject": "F7", "action.adapt_rollback": "F7", "action.drill_start": "F8", "action.drill_stop": "F8",
           "action.drill_guess": "F8", "action.status": "F9", "action.flags": "F9", "action.knowledge_list": "F9", "action.help": "F9"}
DRILL_OPERATOR_OK = ("action.drill_start", "action.drill_stop", "action.help")
LLM_ROUTES = ("diagnose", "chat", "knowledge", "propose")
SUB_NAME = {"diagnose": "S1", "chat": "S2", "knowledge": "S3", "propose": "S4a", "apply": "S4b", "action": "action"}


def known(type_: str) -> bool:
    return type_ in ROUTES or type_ in ACTIONS
