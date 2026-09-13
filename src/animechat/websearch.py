"""联网搜表情图。

Tenor / Giphy 需要免费 key；Bing 与 DuckDuckGo 不需要 key。
provider="auto" 顺序：有 key 的先走 → bing → duckduckgo。
Bing 走 /images/async 片段接口，结果藏在 HTML 属性里（&quot; 转义的 JSON），
带水印的素材站会被降权，表情/贴纸类 CDN 优先。
所有回源都做基本 SSRF 防护：只允许 http(s)、拒绝内网/环回地址、手动跟最多 3 跳并逐跳复查。
"""

from __future__ import annotations

import html
import ipaddress
import json
import re
import socket
from dataclasses import dataclass
from urllib.parse import quote_plus, urljoin, urlparse

import httpx

from .config import Settings

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36 animechat/0.1")
MAX_IMAGE_BYTES = 8 * 1024 * 1024


class SearchError(RuntimeError):
    pass


@dataclass
class RemoteSticker:
    label: str
    image_url: str
    thumb_url: str
    source: str
    page: str = ""
    width: int = 0
    height: int = 0

    def as_dict(self) -> dict:
        return {
            "label": self.label, "image_url": self.image_url, "thumb_url": self.thumb_url,
            "source": self.source, "page": self.page, "width": self.width, "height": self.height,
        }


def _is_public_ip(ip: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return not (addr.is_private or addr.is_loopback or addr.is_link_local
                or addr.is_multicast or addr.is_reserved or addr.is_unspecified)


def assert_public_url(url: str) -> str:
    p = urlparse(url)
    if p.scheme not in ("http", "https") or not p.hostname:
        raise SearchError("只允许 http(s) 图片地址")
    host = p.hostname
    if host.lower().startswith("localhost"):
        raise SearchError("拒绝本机地址")
    try:
        infos = socket.getaddrinfo(host, p.port or (443 if p.scheme == "https" else 80), proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise SearchError("域名解析失败：" + str(exc)) from exc
    if not any(_is_public_ip(i[4][0]) for i in infos):
        raise SearchError("目标是内网地址，已拒绝")
    return url


def sniff_ext(data: bytes) -> str:
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return ".png"
    if data[:3] == b"\xff\xd8\xff":
        return ".jpg"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return ".gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    if data[:2] == b"BM":
        return ".bmp"
    return ""


async def _tenor(client: httpx.AsyncClient, key: str, q: str, limit: int) -> list[RemoteSticker]:
    r = await client.get("https://tenor.googleapis.com/v2/search", params={
        "q": q, "key": key, "client_version": "animechat-0.1", "limit": min(limit, 45),
        "media_filter": "minimal", "contentfilter": "high",
    })
    if r.status_code >= 400:
        raise SearchError("Tenor HTTP " + str(r.status_code) + " " + r.text[:160])
    out: list[RemoteSticker] = []
    for item in r.json().get("results", []):
        formats = item.get("media_formats", {}) or {}
        pick = formats.get("tinygif") or formats.get("gif") or formats.get("mediumgif") or formats.get("nanogif")
        if not pick or not pick.get("url"):
            continue
        dims = pick.get("dims") or [0, 0]
        out.append(RemoteSticker(
            label=(item.get("content_description") or item.get("title") or q)[:40],
            image_url=pick["url"], thumb_url=pick["url"], source="tenor",
            width=int(dims[0] or 0), height=int(dims[1] or 0),
        ))
    return out


async def _giphy(client: httpx.AsyncClient, key: str, q: str, limit: int) -> list[RemoteSticker]:
    r = await client.get("https://api.giphy.com/v1/gifs/search", params={
        "api_key": key, "q": q, "limit": min(limit, 50), "rating": "g", "lang": "zh",
    })
    if r.status_code >= 400:
        raise SearchError("Giphy HTTP " + str(r.status_code) + " " + r.text[:160])
    out: list[RemoteSticker] = []
    for item in r.json().get("data", []):
        images = item.get("images", {}) or {}
        pick = images.get("fixed_height_small") or images.get("fixed_height") or images.get("original")
        if not pick or not pick.get("url"):
            continue
        out.append(RemoteSticker(
            label=(item.get("title") or q)[:40], image_url=pick["url"], thumb_url=pick["url"],
            source="giphy", page=(item.get("user") or {}).get("url", ""),
            width=int(pick.get("width") or 0), height=int(pick.get("height") or 0),
        ))
    return out


_VQD = re.compile(r'vqd["\']?\s*[:=]\s*["\']?([0-9a-zA-Z\-_]{6,40})')

# Bing 的 async 片段里图片直链长这样（整页先 html.unescape 再匹配）：
#   ..."murl":"https://cdn3.emoji.gg/emojis/28517-nanahappy.png"...
_MURL = re.compile(r'"murl"\s*:\s*"(https?://[^"]+?)"')

# 表情/贴纸站：透明底、尺寸合适，优先给结果
_STICKER_HOSTS = ("emoji.gg", "tenor.com", "giphy.com", "imgur.com", "telegram.org",
                  "githubusercontent.com", "github.com", "wikimedia.org", "neko-tools",
                  "static.wikia", "pbs.twimg.com", "reddit")
# 素材站：常常带水印或要注册，降权到末尾（不是禁止，只是别先拿它）
_WATERMARK_HOSTS = ("freepik", "pngtree", "pngall", "clipartmax", "clipartlibrary", "toppng",
                    "kindpng", "stickpng", "seekpng", "storyset", "flaticon", "cleanpng",
                    "pngkey", "pngwing", "lovepng", "588ku", "nipic", "7nmp", "816pics")


def _rank_bing(url: str) -> int:
    host = (urlparse(url).hostname or "").lower()
    if any(h in host for h in _WATERMARK_HOSTS):
        return 4
    if not any(host.endswith(h) or ("." + h) in host for h in _STICKER_HOSTS):
        return 2
    return 1 if re.search(r"\.(png|gif|webp)(\?|$)", url, re.I) else 3


async def _bing(client: httpx.AsyncClient, q: str, limit: int) -> list[RemoteSticker]:
    """免 key 通道：Bing 图片的 async 片段接口（不是完整页面，返回 HTML 片段）。"""
    page = await client.get("https://www.bing.com/images/async", params={
        "q": q, "first": "0", "count": str(max(10, min(35, limit * 2))),
    })
    if page.status_code >= 400:
        raise SearchError("Bing HTTP " + str(page.status_code))
    body = html.unescape(page.text or "")
    found: list[str] = []
    seen: set[str] = set()
    for url in _MURL.findall(body):
        clean = url.replace("\\/", "/").strip()
        if clean and clean not in seen and re.search(r"\.(png|gif|webp|jpe?g)(\?|$)", clean, re.I):
            seen.add(clean)
            found.append(clean)
    if not found:
        raise SearchError("Bing 返回里没有图片直链（页面结构可能变了）")
    found.sort(key=_rank_bing)
    out: list[RemoteSticker] = []
    for url in found[:limit]:
        stem = urlparse(url).path.rsplit("/", 1)[-1].rsplit(".", 1)[0]
        stem = re.sub(r"^\d{2,}[-_]", "", stem).replace("-", " ").strip()
        out.append(RemoteSticker(
            label=(stem or q)[:40], image_url=url, thumb_url=url,
            source="bing", page="https://www.bing.com/images/search?q=" + quote_plus(q),
        ))
    return out


async def _duckduckgo(client: httpx.AsyncClient, q: str, limit: int) -> list[RemoteSticker]:
    """无 key 通道：先从搜索页拿 vqd，再打 i.js。上游改结构就如实报错，不假装成功。"""
    page = await client.get("https://duckduckgo.com/", params={"q": q, "iax": "images", "ia": "images", "kl": "cn-zh"})
    if page.status_code >= 400:
        raise SearchError("DuckDuckGo 搜索页 HTTP " + str(page.status_code))
    body = page.text
    m = _VQD.search(body) or _VQD.search(html.unescape(body))
    if not m:
        raise SearchError("DuckDuckGo 接口变了（拿不到 vqd）。建议在设置里填一个免费的 Tenor Key。")
    api = await client.get("https://duckduckgo.com/i.js", params={
        "l": "zh-cn", "o": "json", "q": q, "vqd": m.group(1), "f": ",,,,", "p": "1", "s": "1",
    })
    if api.status_code >= 400:
        raise SearchError("DuckDuckGo 图片接口 HTTP " + str(api.status_code))
    try:
        data = json.loads(api.text)
    except ValueError as exc:
        raise SearchError("DuckDuckGo 返回不是 JSON（可能被风控）。可以改用 Tenor/Giphy Key。") from exc
    out: list[RemoteSticker] = []
    for item in data.get("results", [])[:limit]:
        img = item.get("image")
        if not img:
            continue
        out.append(RemoteSticker(
            label=str(item.get("title") or q)[:40], image_url=img,
            thumb_url=item.get("thumbnail") or img, source="duckduckgo",
            page=str(item.get("url") or ""), width=int(item.get("width") or 0),
            height=int(item.get("height") or 0),
        ))
    return out


# --------------------------------------------------------------- 头像（肖像）搜索
# 表情包和头像想要的根本不是一类图：表情包偏好透明底小贴纸站，头像要的是
# 「这个角色的肖像 / 立绘」。所以排序规则、过滤规则、甚至该问哪些引擎都得另配一套。
_PORTRAIT_HOSTS = ("static.wikia", "fandom", "myanimelist", "anilist", "kitsu", "wikimedia",
                   "pbs.twimg.com", "zerochan", "anime-figures", "notify", "pmgen",
                   "cdn.manga", "i.pximg", "bdstatic", "zhimg", "bilibili")
# 一眼就不是肖像的：logo、图标、缩略图、占位图
_PORTRAIT_JUNK = ("logo", "icon", "sprite", "banner", "watermark", "placeholder", "blank",
                  "noimage", "no_image", "default-avatar", "default_avatar", "1x1", "loading",
                  "_thumb", "thumb/", "/thumbs/", "avatar-default")
MIN_PORTRAIT_PX = 160


def _rank_portrait(item: "RemoteSticker") -> int:
    url = item.image_url
    low = url.lower()
    host = (urlparse(url).hostname or "").lower()
    if any(j in low for j in _PORTRAIT_JUNK):
        return 9
    # 知道尺寸就把小图甩到后面：搜索结果里混着一堆 64px 的站点头像
    if item.width and item.height and min(item.width, item.height) < MIN_PORTRAIT_PX:
        return 8
    if any(h in host for h in _PORTRAIT_HOSTS):
        return 1
    return 3


def _order(settings: Settings, portrait: bool = False) -> list[str]:
    """按设置排出要试的 provider。找肖像时不碰 Tenor / Giphy：那是动图库，
       搜「角色名 立绘」基本只会给回一堆表情包。"""
    want = settings.sticker_search_provider
    if want == "tenor" and not settings.tenor_api_key.strip():
        raise SearchError("Tenor 需要在设置里填 API Key（tenor.com 控制台免费申请）")
    if want == "giphy" and not settings.giphy_api_key.strip():
        raise SearchError("Giphy 需要在设置里填 API Key（developers.giphy.com 免费申请）")
    if want not in ("", "auto"):
        return [want]
    order: list[str] = []
    if not portrait:
        if settings.tenor_api_key.strip():
            order.append("tenor")
        if settings.giphy_api_key.strip():
            order.append("giphy")
    order.append("bing")        # 实测这台机器上 Bing 比 DuckDuckGo 稳
    order.append("duckduckgo")
    return order


async def _gather(order: list[str], q: str, limit: int, settings: Settings,
                  rank=None) -> tuple[list[RemoteSticker], str, str]:
    """依次试各家，第一个有结果的就返回 (结果, 实际用的 provider, 备注)。全失败抛 SearchError。"""
    notes: list[str] = []
    async with httpx.AsyncClient(timeout=20.0, headers={"User-Agent": UA, "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.6"},
                                 follow_redirects=False) as client:
        for provider in order:
            try:
                if provider == "tenor":
                    res = await _tenor(client, settings.tenor_api_key.strip(), q, limit)
                elif provider == "giphy":
                    res = await _giphy(client, settings.giphy_api_key.strip(), q, limit)
                elif provider == "bing":
                    res = await _bing(client, q, limit)
                else:
                    res = await _duckduckgo(client, q, limit)
                if rank is not None:
                    res = sorted(res, key=rank)
                if res:
                    return res[:limit], provider, "；".join(notes)
                notes.append(provider + " 无结果")
            except SearchError as exc:
                notes.append(str(exc))
            except httpx.HTTPError as exc:
                notes.append(provider + " 网络错误 " + exc.__class__.__name__)
            except Exception as exc:
                notes.append(provider + " 解析失败 " + str(exc)[:80])
    raise SearchError("没搜到：" + "；".join(notes) if notes else "没搜到结果")


async def search(q: str, settings: Settings, limit: int = 24) -> tuple[list[RemoteSticker], str, str]:
    """搜表情包。"""
    q = (q or "").strip()
    if not q:
        return [], "none", "关键词为空"
    return await _gather(_order(settings), q, limit, settings)


async def search_portrait(q: str, settings: Settings, limit: int = 18) -> tuple[list[RemoteSticker], str, str]:
    """给角色搜头像：走同一批免 Key 引擎，但按「像不像一张肖像」重排。"""
    q = (q or "").strip()
    if not q:
        return [], "none", "关键词为空"
    return await _gather(_order(settings, portrait=True), q, limit, settings, rank=_rank_portrait)


def _referer_candidates(referer: str, current: str) -> list[str]:
    """按优先级给出防盗链重试要试的 Referer 清单。
    很多图站（Pinterest、素材站）会用真实浏览器页面地址做白名单；
    Bing 给的 page 是假的，直接照搬会被原站当成跨站引用拒绝。"""
    out: list[str] = []
    if referer:
        out.append(referer)
    base = urlparse(current)
    origin = base.scheme + "://" + (base.netloc or "")
    if origin and origin not in out:
        out.append(origin)
    return out


async def download(url: str, referer: str = "") -> tuple[bytes, str]:
    """下载一张图，返回 (bytes, 扩展名)。
    目标带防盗链时按 Referer 优先级自动重试（页地址 → 图源站根地址 → 不带 Referer），
    而不是第一次 4xx 就报错。"""
    current = assert_public_url(url)
    referers = _referer_candidates(referer, current)
    status = 0
    ctype = ""
    data = b""
    async with httpx.AsyncClient(timeout=30.0, follow_redirects=False) as client:
        for ref in referers + [None]:
            headers = {"User-Agent": UA}
            if ref:
                headers["Referer"] = ref
            got = False
            for _ in range(4):
                r = await client.get(current, headers=headers)
                status = r.status_code
                ctype = (r.headers.get("content-type") or "").split(";")[0].strip().lower()
                if status in (301, 302, 303, 307, 308):
                    nxt = r.headers.get("location") or ""
                    if not nxt:
                        raise SearchError("重定向没有目标")
                    current = assert_public_url(urljoin(current, nxt))
                    continue
                if status >= 400:
                    break  # 换一个 Referer 再试，别急着报错
                data = r.content
                got = True
                break
            if got:
                break
            status = r.status_code if not got else status
        else:
            raise SearchError("原站拒绝下载（HTTP " + str(status) + "）：这张图有防盗链，换个结果或换个关键词")
    if not data:
        raise SearchError("图片内容为空")
    if len(data) > MAX_IMAGE_BYTES:
        raise SearchError("图片超过 8MB")
    ext = sniff_ext(data)
    if not ext and not ctype.startswith("image/"):
        raise SearchError("目标不是可识别的图片（content-type=" + (ctype or "?") + "）")
    return data, ext or ".png"
