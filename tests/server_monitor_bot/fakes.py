# 테스트용 가짜 채팅 모델: 정해 둔 응답을 순서대로 돌려주고 bind_tools·with_structured_output을 흉내 낸다.
from langchain_core.messages import AIMessage


class FakeChat:
    def __init__(self, replies=(), structured=()):
        self.replies, self.structured = list(replies), list(structured)
        self.calls, self.tool_choices = [], []

    def bind_tools(self, tools, tool_choice=None, **kw):
        chat = self

        class Bound:
            def invoke(self, messages):
                chat.tool_choices.append(tool_choice)
                chat.calls.append(messages)
                reply = chat.replies.pop(0) if chat.replies else AIMessage(content="충분")
                if tool_choice == "none":
                    reply = AIMessage(content=reply.content or "결론")
                return reply
        return Bound()

    def with_structured_output(self, schema, include_raw=False, **kw):
        chat = self

        class Struct:
            def invoke(self, messages):
                chat.calls.append(messages)
                data = chat.structured.pop(0)
                raw = AIMessage(content="", usage_metadata={"input_tokens": 10, "output_tokens": 5, "total_tokens": 15})
                parsed = None if data is None else schema(**data)
                return {"raw": raw, "parsed": parsed, "parsing_error": None if parsed else "bad"}
        return Struct()

    def invoke(self, messages):
        return AIMessage(content="요약")


def call(name, args, cid):
    return {"name": name, "args": args, "id": cid, "type": "tool_call"}
