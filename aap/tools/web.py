"""Web tools. Requests go through the egress proxy (AAP_EGRESS_PROXY) and the agent's domain allowlist."""
import html
import re
from urllib.parse import parse_qs, unquote, urlparse

from .. import settings
from ..clients import egress_client
from ..core.permissions import check_domain
from ..core.sdk import tool


def _text(page: str) -> str:
    page = re.sub(r"(?is)<(script|style|noscript|svg|head)[^>]*>.*?</\1>", " ", page)
    page = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</h\d>|</li>", "\n", page)
    t = html.unescape(re.sub(r"<[^>]+>", " ", page))
    t = re.sub(r"[ \t\r\f\v]+", " ", t)
    return re.sub(r"\n\s*\n+", "\n\n", t).strip()


@tool("web_fetch", "Fetch a URL and return readable text (allowlisted domains only).",
      {"url": {"type": "string"}, "max_chars": {"type": "integer", "default": 20000}}, needs="network")
def web_fetch(ctx, url: str, max_chars: int = 20000):
    with egress_client() as c:
        r = c.get(url)
    ct = r.headers.get("content-type", "")
    body = _text(r.text) if "html" in ct else r.text
    title = re.search(r"(?is)<title[^>]*>(.*?)</title>", r.text or "")
    return {"url": str(r.url), "status": r.status_code, "title": html.unescape(title.group(1).strip()) if title else "",
            "text": body[:max_chars], "truncated": len(body) > max_chars}


@tool("web_search", "Search the web. Returns [{title, url, snippet}].",
      {"query": {"type": "string"}, "n": {"type": "integer", "default": 5}}, needs="network")
def web_search(ctx, query: str, n: int = 5):
    provider = settings.get("search_provider")
    if provider == "tavily" and settings.get("search_api_key"):
        check_domain(ctx.spec, "https://api.tavily.com")
        with egress_client() as c:
            r = c.post("https://api.tavily.com/search", json={"api_key": settings.get("search_api_key"),
                                                              "query": query, "max_results": n})
        return {"results": [{"title": x.get("title"), "url": x.get("url"), "snippet": x.get("content", "")[:400]}
                            for x in r.json().get("results", [])]}
    check_domain(ctx.spec, "https://html.duckduckgo.com")
    with egress_client() as c:
        r = c.post("https://html.duckduckgo.com/html/", data={"q": query})
    out = []
    for m in re.finditer(r'(?s)<a[^>]+class="result__a"[^>]+href="([^"]+)"[^>]*>(.*?)</a>.*?'
                         r'class="result__snippet"[^>]*>(.*?)</', r.text):
        href = m.group(1)
        if "uddg=" in href:
            href = unquote(parse_qs(urlparse(href).query).get("uddg", [href])[0])
        out.append({"title": _text(m.group(2)), "url": href, "snippet": _text(m.group(3))[:400]})
        if len(out) >= n:
            break
    return {"results": out}
