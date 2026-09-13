"""联网搜表情图：Bing 源的离线测试（结构一旦变了，这里先红，别到用户界面才发现）。"""

import pytest

from animechat import websearch as w
from animechat.config import Settings


# 真实形状： Bing 把整段 JSON 塞在 HTML 属性里，引号是 &quot; 转义的
BING_PAGE = (
    '<div class="iusc" m="{&quot;cid&quot;:&quot;ABCD&quot;,'
    '&quot;murl&quot;:&quot;https://www.freepik.com/premium/vector/anime-emoji_123.png&quot;,'
    '&quot;turl&quot;:&quot;https://example.com/x.png&quot;}"></div>'
    '<div class="iusc" m="{&quot;murl&quot;:&quot;https://cdn3.emoji.gg/emojis/28517-nanahappy.png&quot;}"></div>'
    '<div class="iusc" m="{&quot;murl&quot;:&quot;https://media.tenor.com/9jU-CvLRXcoAAAAC/cute-anime-crying.gif&quot;}"></div>'
    '<div class="iusc" m="{&quot;murl&quot;:&quot;https://blog.example.com/cover.jpg&quot;}"></div>'
    '<div class="iusc" m="{&quot;murl&quot;:&quot;https://skip-me.example/page&quot;}"></div>'
)


class _Resp:
    def __init__(self, text, status=200, headers=None):
        self.text = text
        self.status_code = status
        self.headers = headers or {}
        self.content = text if isinstance(text, bytes) else text.encode()


class _Client:
    """只用到 await client.get(url, params=...)，够喂进 _bing。"""

    def __init__(self, text, status=200):
        self._text = text
        self._status = status
        self.calls = []

    async def get(self, url, params=None, **kw):
        self.calls.append((url, params))
        return _Resp(self._text, self._status)


@pytest.mark.asyncio
async def test_bing_parses_escaped_murl_and_prefers_sticker_cdns():
    client = _Client(BING_PAGE)
    res = await w._bing(client, "anime cry emoji gif", 10)
    urls = [r.image_url for r in res]
    assert "https://cdn3.emoji.gg/emojis/28517-nanahappy.png" in urls
    assert "https://media.tenor.com/9jU-CvLRXcoAAAAC/cute-anime-crying.gif" in urls
    assert all("page" not in u for u in urls), "没有图片扩展名的结果要丢掉"
    assert urls[-1].startswith("https://www.freepik.com"), "素材站（水印）排最后：" + str(urls)
    assert urls[0].startswith("https://cdn3.emoji.gg"), "表情站透明图排最前：" + str(urls)
    assert res[0].source == "bing" and res[0].page.startswith("https://www.bing.com/images/search")
    assert res[0].label == "nanahappy", "标签从文件名里扒：" + res[0].label
    assert client.calls[0][0].endswith("/images/async")


@pytest.mark.asyncio
async def test_bing_empty_page_raises_instead_of_lying():
    with pytest.raises(w.SearchError):
        await w._bing(_Client("<html><body>nothing</body></html>"), "q", 5)

    with pytest.raises(w.SearchError) as exc:
        await w._bing(_Client("", status=429), "q", 5)
    assert "429" in str(exc.value)


@pytest.mark.asyncio
async def test_auto_order_puts_bing_before_duckduckgo(monkeypatch):
    ran: list[str] = []

    async def fake_bing(client, q, limit):
        ran.append("bing")
        return [w.RemoteSticker(label="x", image_url="https://cdn.example/a.gif",
                               thumb_url="", source="bing")]

    async def fake_ddg(client, q, limit):
        ran.append("duckduckgo")
        return []

    async def fake_tenor(client, key, q, limit):
        ran.append("tenor")
        raise w.SearchError("没网")

    monkeypatch.setattr(w, "_bing", fake_bing)
    monkeypatch.setattr(w, "_duckduckgo", fake_ddg)
    monkeypatch.setattr(w, "_tenor", fake_tenor)

    res, provider, note = await w.search("anime emoji", Settings(), limit=5)
    assert ran == ["bing"], "无 Key 时应当第一个就命中 Bing，不必再敲 DuckDuckGo：" + str(ran)
    assert provider == "bing" and res

    ran.clear()
    s = Settings(tenor_api_key="k")
    await w.search("anime emoji", s, limit=5)
    assert ran == ["tenor", "bing"], "有 Key 时先 Key 源，再 Bing，再退 DDG：" + str(ran)


def test_bing_is_a_valid_provider_value():
    assert Settings(sticker_search_provider="bing").sticker_search_provider == "bing"
    with pytest.raises(ValueError):
        Settings(sticker_search_provider="not-a-provider")


@pytest.mark.parametrize("bad", [
    "http://127.0.0.1:8899/media/stickers/x.png",
    "http://localhost/a.png",
    "http://169.254.1.1/a.png",
    "file:///etc/passwd",
])
def test_import_target_must_be_public(bad):
    with pytest.raises(w.SearchError):
        w.assert_public_url(bad)


def test_sniff_ext_matches_headers_not_filename():
    assert w.sniff_ext(b"\x89PNG\r\n\x1a\n...") == ".png"
    assert w.sniff_ext(b"GIF89a....") == ".gif"
    assert w.sniff_ext(b"RIFFxxxxWEBP") == ".webp"
    assert w.sniff_ext(b"<html>") == ""


def test_download_retry_with_referer(monkeypatch):
    """目标带防盗链时，按 Referer 优先级自动重试（页地址 → 图源站根地址 → 不带 Referer），
       而不是第一次 4xx 就报错。"""
    import asyncio
    calls = []
    async def fake_get(self, url, headers=None):
        calls.append(headers.get("Referer") if headers else None)
        if len(calls) == 1:
            return _Resp("", status=403)
        elif len(calls) == 2:
            return _Resp("", status=403)
        else:
            return _Resp(b"\x89PNG\r\n\x1a\nfake image data", status=200)
    monkeypatch.setattr(w.httpx.AsyncClient, "get", fake_get)
    data, ext = asyncio.run(w.download("https://i.pinimg.com/a.png", "https://www.bing.com/x"))
    assert len(calls) == 3, "应试三种 Referer：Bing页、图源站、无Referer"
    assert calls[0] == "https://www.bing.com/x"
    assert calls[1] == "https://i.pinimg.com"
    assert calls[2] is None
    assert data.startswith(b"\x89PNG\r\n\x1a\n") and ext == ".png"
