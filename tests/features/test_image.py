# async load_image를 httpx.MockTransport로 본다. 성공, 5MB 초과, 형식 오류, 다운로드 실패, 전체 5초 초과. 네트워크는 쓰지 않는다.
import asyncio
import logging
from functools import partial
from io import BytesIO

import httpx
import pytest
from PIL import Image

from app.core.exceptions import ImageFetchError, ImageSizeError, ImageTypeError
from app.features.receipt import image as receipt
from app.features.taxi_meter import image as taxi

URL = "http://img.test/a.png"


def picture(fmt: str) -> bytes:
    buf = BytesIO()
    Image.new("RGB", (3, 2)).save(buf, fmt)
    return buf.getvalue()


def serve(monkeypatch, handler):
    monkeypatch.setattr(httpx, "AsyncClient", partial(httpx.AsyncClient, transport=httpx.MockTransport(handler)))


def load(module, caplog):
    with caplog.at_level(logging.INFO, logger="app"):
        return asyncio.run(module.load_image(URL))


def last(caplog, prefix):
    return [r.getMessage() for r in caplog.records if r.getMessage().startswith(prefix)][-1]


@pytest.mark.parametrize("module", [taxi, receipt])
def test_load_image_ok(monkeypatch, caplog, module):
    content = picture("PNG")
    serve(monkeypatch, lambda req: httpx.Response(200, content=content))
    image = load(module, caplog)
    assert (image.content, image.width, image.height, image.media_type, image.source) == (content, 3, 2, "image/png", URL)
    assert "image loaded: host=img.test bytes=" in last(caplog, "image loaded")


@pytest.mark.parametrize("module", [taxi, receipt])
def test_load_image_too_big(monkeypatch, caplog, module):
    serve(monkeypatch, lambda req: httpx.Response(200, content=b"x" * (module.MAX_SIZE + 1)))
    with pytest.raises(ImageSizeError):
        load(module, caplog)
    assert last(caplog, "image size is invalid").startswith(f"image size is invalid: host=img.test bytes>{module.MAX_SIZE}")


@pytest.mark.parametrize("module", [taxi, receipt])
def test_load_image_bad_type(monkeypatch, caplog, module):
    serve(monkeypatch, lambda req: httpx.Response(200, content=picture("GIF")))
    with pytest.raises(ImageTypeError):
        load(module, caplog)
    assert "reason=wrong_type type=image/gif" in last(caplog, "image must be jpeg or png")
    serve(monkeypatch, lambda req: httpx.Response(200, content=b"not an image"))
    with pytest.raises(ImageTypeError):
        load(module, caplog)
    assert "reason=not_image" in last(caplog, "image must be jpeg or png")


@pytest.mark.parametrize("module", [taxi, receipt])
def test_load_image_fetch_failed(monkeypatch, caplog, module):
    for res, reason, status in [
        (httpx.Response(404), "http_4xx", 404),
        (httpx.Response(302, headers={"Location": "http://img.test/b.png"}), "redirect", 302),  # 리다이렉트는 따라가지 않는다
    ]:
        serve(monkeypatch, lambda req, res=res: res)
        with pytest.raises(ImageFetchError):
            load(module, caplog)
        assert f"reason={reason} status={status} " in last(caplog, "image download failed")


@pytest.mark.parametrize("module", [taxi, receipt])
def test_load_image_total_timeout(monkeypatch, caplog, module):
    async def slow(req):
        await asyncio.sleep(1)
        return httpx.Response(200, content=picture("PNG"))

    monkeypatch.setattr(module, "TIMEOUT", 0.05)
    serve(monkeypatch, slow)
    with pytest.raises(ImageFetchError):
        load(module, caplog)
    assert "reason=timeout status=None " in last(caplog, "image download failed")
