# 1단계 변환: 파일 bytes → Markdown 문서. md·txt는 직접 읽고, pdf·docx는 MarkItDown을 자식 프로세스(subprocess, daemon 분석 프로세스에서도 가능)에서 시간 제한을 두고 돌린다.
import io
import re
import subprocess
import sys
from pathlib import PurePath

from .models import Document, Source

TEXT = (".md", ".txt")
RICH = (".pdf", ".docx")


def doc_id(name: str) -> str:
    return re.sub(r"[^\w.-]+", "_", PurePath(name).stem).strip("_.")[:120] or "doc"


def title_of(markdown: str) -> str | None:
    return next((line[2:].strip() for line in markdown.splitlines() if line.startswith("# ")), None)


def markitdown(name: str, data: bytes) -> str:
    try:
        from markitdown import MarkItDown, StreamInfo

        ext = PurePath(name).suffix.lower()
        return MarkItDown(enable_plugins=False).convert_stream(io.BytesIO(data), stream_info=StreamInfo(extension=ext)).text_content
    except Exception as exc:
        raise RuntimeError(type(exc).__name__) from None


class Converter:
    def __init__(self, *, max_mb: int = 20, timeout_s: int = 60):
        self.max_mb, self.timeout_s = max_mb, timeout_s

    def __call__(self, source: Source) -> Document:
        ext = PurePath(source.name).suffix.lower()
        if ext not in TEXT + RICH:
            raise ValueError(f"지원하지 않는 형식: {ext or '확장자 없음'} (md·txt·pdf·docx만)")
        if not source.data:
            raise ValueError("빈 파일")
        if len(source.data) > self.max_mb * 1024 * 1024:
            raise ValueError(f"{self.max_mb}MB 초과")
        text = source.data.decode("utf-8", errors="replace") if ext in TEXT else self.rich(source.name, source.data)
        if ext in RICH and text.lstrip().startswith(("%PDF", "PK")):
            raise ValueError("변환 실패: 깨진 파일")
        did = doc_id(source.name)
        return Document(did, title_of(text) or did, text, source.origin, source.by)

    def rich(self, name: str, data: bytes) -> str:
        try:
            out = subprocess.run([sys.executable, "-m", __name__, PurePath(name).suffix.lower()], input=data, capture_output=True,
                                 timeout=self.timeout_s)
        except subprocess.TimeoutExpired:
            raise ValueError(f"변환 시간 초과({self.timeout_s}초)") from None
        if out.returncode:
            lines = out.stderr.decode(errors="replace").strip().splitlines()
            raise ValueError(f"변환 실패: {lines[-1] if lines else out.returncode}")
        return out.stdout.decode("utf-8")


def convert(source: Source, *, max_mb: int = 20, timeout_s: int = 60) -> Document:
    return Converter(max_mb=max_mb, timeout_s=timeout_s)(source)


if __name__ == "__main__":
    try:
        sys.stdout.buffer.write(markitdown(f"x{sys.argv[1]}", sys.stdin.buffer.read()).encode("utf-8"))
    except RuntimeError as exc:
        sys.exit(str(exc))
