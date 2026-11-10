# RAG-V1: 문서 파이프라인(변환·나누기·정리·중복·반영), 저장소 문서 동기화, 대화 압축, 문서 명령·업로드, 예상 문제 검증.
import hashlib
import io
import zipfile
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage
from qdrant_client import QdrantClient

from app.Server_Monitor_Bot.channels.telegram import render
from app.Server_Monitor_Bot.channels.telegram.receiver import to_event
from app.Server_Monitor_Bot.config.settings import load
from app.Server_Monitor_Bot.docs.chunk import split
from app.Server_Monitor_Bot.docs.clean import clean
from app.Server_Monitor_Bot.docs.convert import Converter, convert, doc_id
from app.Server_Monitor_Bot.docs.index import DocIndex
from app.Server_Monitor_Bot.docs.models import Source
from app.Server_Monitor_Bot.docs.pipeline import REPO, ingest, repo_done, sync_repo
from app.Server_Monitor_Bot.knowledge.schema import signal_text
from app.Server_Monitor_Bot.ops_agent.actions import Actions
from app.Server_Monitor_Bot.ops_agent.chat.context import build
from app.Server_Monitor_Bot.ops_agent.common.validators import BY_SCHEMA, common
from app.Server_Monitor_Bot.store.vector import VectorDB

MD = "# 배포 절차\n\n소개 문단\n\n## 롤백\n이전 이미지로 되돌린다\n\n## 연락\n클라우드팀에 요청"


class FakeEmbed:
    def __init__(self):
        self.calls = 0

    def one(self, text):
        return self.many([text])[0]

    def many(self, texts):
        self.calls += len(texts)
        return [[b / 255 + 0.01 for b in hashlib.sha256(t.encode()).digest()[:8]] for t in texts]


@pytest.fixture
def index():
    return DocIndex(VectorDB(stage="t", url=None, api_key=None, dim=8, client=QdrantClient(":memory:")), FakeEmbed())


def docx(text: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("[Content_Types].xml", '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                   '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                   '<Default Extension="xml" ContentType="application/xml"/>'
                   '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>')
        z.writestr("_rels/.rels", '<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                   '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
                   '</Relationships>')
        z.writestr("word/document.xml", '<?xml version="1.0"?><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                   f'<w:body><w:p><w:r><w:t>{text}</w:t></w:r></w:p></w:body></w:document>')
    return buf.getvalue()


def pdf(text: str) -> bytes:
    stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode()
    objs = [b"<< /Type /Catalog /Pages 2 0 R >>", b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
            b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream", b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    out, offsets = b"%PDF-1.4\n", []
    for i, body in enumerate(objs, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % i + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1) + b"".join(b"%010d 00000 n \n" % o for o in offsets)
    return out + b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref)


def test_convert_formats_and_limits():
    d = convert(Source("06 배포/절차.md", MD.encode()))
    assert d.doc_id == "절차" and d.title == "배포 절차" and doc_id("a b.txt") == "a_b"
    assert "hello docx" in convert(Source("x.docx", docx("hello docx"))).markdown
    assert "Hello PDF" in convert(Source("x.pdf", pdf("Hello PDF"))).markdown
    for src, word in ((Source("x.png", b"1"), "지원하지 않는 형식"), (Source("x.md", b""), "빈 파일"), (Source("x.md", b"1" * 2048), "MB 초과")):
        with pytest.raises(ValueError, match=word):
            Converter(max_mb=0.001)(src) if word == "MB 초과" else convert(src)
    with pytest.raises(ValueError, match="시간 초과"):
        Converter(timeout_s=0.001)(Source("x.pdf", pdf("slow")))


def test_broken_files_and_fenced_code():
    with pytest.raises(ValueError, match="변환 실패"):
        convert(Source("x.pdf", b"%PDF-1.7\n%broken"))
    md = "# T\n\n## A\n```bash\n# not a heading\n```\n\n## B\nb"
    assert [c.section for c in split(convert(Source("f.md", md.encode())))] == ["A", "B"]


def test_split_and_clean():
    chunks = clean(split(convert(Source("a.md", MD.encode())), max_chars=1500))
    assert [(c.section, c.text) for c in chunks] == [("배포 절차", "소개 문단"), ("롤백", "이전 이미지로 되돌린다"), ("연락", "클라우드팀에 요청")]
    long = split(convert(Source("b.txt", ("가" * 900 + "\n\n" + "나" * 900 + "\n\n" + "다" * 4000).encode())), max_chars=1500)
    assert all(len(c.text) <= 1500 for c in long) and len(long) == 5 and long[0].section == "b"
    masked = clean(split(convert(Source("c.md", b"## k\napi_key=abc123 https://s3/x?X-Amz-Signature=zz")), max_chars=1500))
    assert "abc123" not in masked[0].text and "zz" not in masked[0].text and masked[0].order == 0


def test_ingest_dedupe_replace_and_delete(index):
    r = ingest(Source("ops.md", MD.encode(), "telegram", 1), index)
    assert (r.added, r.same, r.removed, r.overlap) == (3, 0, 0, [])
    calls = index.embedder.calls
    again = ingest(Source("ops.md", MD.replace("클라우드팀에 요청", "인프라팀에 요청").encode(), "telegram", 1), index)
    assert (again.added, again.same, again.removed) == (1, 2, 1) and index.embedder.calls == calls + 1
    copy = ingest(Source("ops_copy.md", MD.encode(), "telegram", 1), index)
    assert len(copy.overlap) == 2 and copy.overlap[1] == "롤백 → ops#1"
    hits = index.search("롤백", limit=5, per_doc=1)
    assert len({h["doc_id"] for h in hits}) == len(hits) == 2
    key = index.chunk("ops", 1)["key"]
    assert index.replace(key, "새 롤백 절차", 7) and index.chunk("ops", 1)["text"] == "새 롤백 절차" and index.get(key) is None
    assert index.delete_doc("ops_copy") == 3 and [d["doc_id"] for d in index.docs()] == ["ops"]


def test_sync_repo_docs(index, tmp_path):
    (tmp_path / "a.md").write_text(MD)
    (tmp_path / "b.md").write_text("# B\n\n## 하나\n내용")
    assert sync_repo(index, tmp_path)["docs"] == 2 and repo_done(index, tmp_path)
    (tmp_path / "b.md").unlink()
    assert not repo_done(index, tmp_path)
    stats = sync_repo(index, tmp_path)
    assert stats["removed"] == 1 and [d["doc_id"] for d in index.docs()] == ["a"] and repo_done(index, tmp_path)
    with pytest.raises(ValueError, match="저장소 문서와 이름이 같습니다"):
        ingest(Source("a.md", b"# x\n\nbody", "telegram", 1), index)


def test_repo_docs_fit_chunk_limit():
    paths = sorted(REPO.glob("*.md"))
    assert len(paths) == 9
    for path in paths:
        chunks = clean(split(convert(Source(path.name, path.read_bytes(), "repo")), max_chars=1500))
        assert chunks and all(len(c.text) <= 1500 for c in chunks), path.name


class ChatRepo:
    def __init__(self, n):
        self.rows = [{"id": i, "role": "user" if i % 2 else "bot", "text": "가" * 600} for i in range(1, n + 1)]
        self.saved = {"summary": "", "upto_id": 0}

    def summary_of(self, chat_id):
        return self.saved

    def since(self, chat_id, after):
        return [r for r in self.rows if r["id"] > after]

    def save_summary(self, chat_id, summary, upto):
        self.saved = {"summary": summary, "upto_id": upto}


class Llm:
    def __init__(self, fail=False):
        self.fail = fail

    def invoke(self, messages):
        if self.fail:
            raise RuntimeError("down")
        return AIMessage(content=[{"type": "text", "text": "압축 요약"}])


def test_context_folds_over_high_mark():
    cfg = load().section("chat")
    small = ChatRepo(10)
    assert build(small, 1, Llm(), None, cfg) == {"summary": "", "turns": [{"role": r["role"], "text": r["text"]} for r in small.rows]}
    big = ChatRepo(30)
    out = build(big, 1, Llm(), None, cfg)
    kept = sum(len(t["text"]) for t in out["turns"]) / cfg["chars_per_token"]
    assert out["summary"] == "압축 요약" and kept <= cfg["context_low_tokens"] and big.saved["upto_id"] == 30 - len(out["turns"])
    assert build(big, 1, Llm(), None, cfg)["turns"] == out["turns"]
    failed = build(ChatRepo(30), 1, Llm(fail=True), None, cfg)
    assert failed["summary"] == "" and sum(len(t["text"]) for t in failed["turns"]) / cfg["chars_per_token"] <= cfg["context_low_tokens"]


def test_receiver_documents_and_docs_commands():
    roles = {1: "admin", 2: "member"}.get
    upd = {"update_id": 1, "message": {"message_id": 3, "from": {"id": 1}, "chat": {"id": 1},
                                        "document": {"file_id": "F", "file_name": "a.pdf", "file_size": 10}}}
    e = to_event(upd, roles)
    assert e["type"] == "action.doc_upload" and e["payload"]["file_name"] == "a.pdf"
    upd["message"]["from"]["id"] = 2
    assert to_event(upd, roles) is None
    cmd = {"update_id": 2, "message": {"message_id": 4, "from": {"id": 2}, "chat": {"id": 2}, "text": "/docs ops"}}
    assert to_event(cmd, roles)["type"] == "action.doc_list" and to_event(cmd, roles)["payload"]["query"] == "ops"
    cb = {"update_id": 3, "callback_query": {"id": "c", "from": {"id": 1}, "data": "dd:ops", "message": {"message_id": 5, "chat": {"id": 1}}}}
    assert to_event(cb, roles)["payload"]["doc_id"] == "ops"


class Tg:
    def __init__(self, data):
        self.data = data

    def fetch_file(self, file_id, max_bytes):
        return self.data

    def answer_callback(self, *a):
        pass


def actions(index, data=MD.encode()):
    coord = SimpleNamespace(messages_of=lambda kind, ref, hours=None: [(1, 50)] if kind == "doc" else [])
    knowledge = SimpleNamespace(get_draft=lambda d: {"kind": "doc"}, apply_draft=lambda d, u: {"ok": True, "doc": {}, "message": "수정했습니다: x"})
    repos = SimpleNamespace(docs=index, coord=coord, knowledge=knowledge)
    return Actions(SimpleNamespace(ctx=SimpleNamespace(repos=repos, telegram=Tg(data), settings=load({"Admin_Telegram_ID": "1"}))))


def ev(type_, who=1, **payload):
    return {"type": type_, "from_id": who, "chat_id": who, "payload": {"message_id": 9, **payload}}


def test_doc_actions(index):
    a = actions(index)
    out = a.handle(ev("action.doc_upload", file_id="F", file_name="ops.md", file_size=100))
    assert out["outbox"][0]["text"].startswith("📄 문서 저장 · ops") and "새 절 3" in out["outbox"][0]["text"]
    assert "MB 초과" in a.handle(ev("action.doc_upload", file_id="F", file_name="x.md", file_size=10**9))["outbox"][0]["text"]
    assert "지원하지 않는 형식" in a.handle(ev("action.doc_upload", file_id="F", file_name="x.exe", file_size=1))["outbox"][0]["text"]
    assert "ops" in a.handle(ev("action.doc_list", query=""))["outbox"][0]["text"]
    detail = a.handle(ev("action.doc_list", query="ops"))["outbox"][0]
    assert detail["buttons"] == [[("삭제", "dd:ops")]] and detail["ref"] == ("doc", "ops")
    deleted = a.handle(ev("action.doc_delete", doc_id="ops", name="관리자A"))
    assert deleted["outbox"][0]["mark"] and "3절" in deleted["outbox"][1]["text"] and index.docs() == []
    long = "서버_모니터링_봇_장애_대응_운영_가이드_2026_최종본"
    a.handle(ev("action.doc_upload", file_id="F", file_name=f"{long}.md", file_size=100))
    key = a.handle(ev("action.doc_list", query=long))["outbox"][0]["buttons"][0][0][1]
    assert key.startswith("dd:#") and len(key.encode()) <= 64
    assert "삭제했습니다" in a.handle(ev("action.doc_delete", doc_id=key[3:]))["outbox"][-1]["text"] and index.docs() == []
    sync_repo(index, REPO)
    assert "git에서 수정" in a.handle(ev("action.doc_delete", doc_id=index.docs()[0]["doc_id"]))["outbox"][0]["text"]
    assert a.handle(ev("action.knowledge_save", who=2, draft_id="D-1"))["outbox"][0]["text"] == render.say("denied")
    assert "수정했습니다" in a.handle(ev("action.knowledge_save", draft_id="D-1"))["outbox"][-1]["text"]


def test_docs_are_admin_only():
    from app.Server_Monitor_Bot.ops_agent.roles import allowed
    from .test_main_graph import nodes

    assert not allowed("member", "action.doc_list") and not allowed("member", "action.doc_upload") and allowed("admin", "action.doc_list")
    n = nodes()
    n.ctx.settings = load({"Admin_Telegram_ID": "1"})
    s = {"event": {"type": "user.message", "from_id": 2, "chat_id": 2, "payload": {}}, "budget": None, "node_path": [], "outbox": [],
         "edit_request": {"intent": "doc_update", "target_doc": "doc:ops#0", "edit_request": "x", "chat_id": 2}}
    out = n.knowledge(s)
    assert out["outbox"][-1]["text"] == render.say("denied") and out["outbox"][-1]["chat_id"] == 2


def test_expected_issues_need_evidence_and_render():
    ev_ = [{"id": "kn:KN_A@v1,kn:KN_B@v1", "summary": "s", "data": "{}"}]
    ok = {"answer_text": "근거가 없어 모릅니다", "evidence_ids": [], "expected_issues": [{"problem": "p", "solution": "s", "evidence_ids": ["kn:KN_B@v1"]}]}
    assert common(ok, ev_, {}) == []
    bare = {**ok, "expected_issues": [{"problem": "p", "solution": "s", "evidence_ids": []}]}
    assert any("근거 없는 예상 문제" in e for e in common(bare, ev_, {}))
    assert any("없는 근거 ID" in e for e in common({**ok, "expected_issues": [{"problem": "p", "solution": "s", "evidence_ids": ["doc:x#1"]}]}, ev_, {}))
    assert BY_SCHEMA["chat_answer"]({"intent": "doc_update", "target_doc": None, "edit_request": "x"}, {})
    assert BY_SCHEMA["chat_answer"]({"intent": "doc_update", "target_doc": "doc:ops#1", "edit_request": "x"}, {}) == []
    assert BY_SCHEMA["doc_draft"]({"op": "update", "text": " "}, {"op": "update"}) == ["고친 본문이 비었음"]
    assert render.with_issues("답", ok["expected_issues"]) == "답\n\n예상 문제\n· p → s"
    assert render.with_issues("답", []) == "답"
    report = {"summary": "s", "cause_text": "c", "resolution_text": "r", "step_id": "luna_call", "confidence": "low", "unknown": True,
              "evidence_ids": [], "knowledge_ids": [], "expected_issues": []}
    assert "예상 문제: 근거가 없어 모릅니다" in render.plain(render.diagnosis(report, [], "receipt"))
    assert signal_text({"step_id": ["luna_call"], "status": [429], "min_ratio": 0.5}) == "단계 luna_call, HTTP 상태 429, 실패 비율 50% 이상"
