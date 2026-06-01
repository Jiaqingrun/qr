from __future__ import annotations

import html as _html
import re

import httpx

from . import config

_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
_HEADERS = {"User-Agent": _UA, "Accept-Language": "zh-CN,zh;q=0.9"}


def _strip(s: str) -> str:
    return re.sub(r"\s+", " ", _html.unescape(re.sub(r"<[^>]+>", "", s))).strip()


def _get(url: str, params: dict) -> str:
    # trust_env=False：直连，避免被本机代理(Clash 等)拦截导致 SSL/502
    r = httpx.get(url, params=params, headers=_HEADERS, timeout=15.0,
                  follow_redirects=True, trust_env=False)
    r.raise_for_status()
    return r.text


def _baidu(query: str, n: int) -> list[dict]:
    t = _get("https://www.baidu.com/s", {"wd": query, "rn": n})
    pairs = re.findall(
        r'<h3[^>]*class="[^"]*\bt\b[^"]*"[^>]*>\s*<a[^>]*href="([^"]*)"[^>]*>(.*?)</a>',
        t, re.DOTALL)
    abstracts = re.findall(r'class="[^"]*c-abstract[^"]*"[^>]*>(.*?)</div>', t, re.DOTALL)
    out = []
    for i, (url, title) in enumerate(pairs[:n]):
        out.append({"title": _strip(title), "url": url,
                    "snippet": _strip(abstracts[i]) if i < len(abstracts) else "",
                    "engine": "baidu"})
    return [r for r in out if r["title"]]


def _bing(query: str, n: int) -> list[dict]:
    t = _get("https://cn.bing.com/search", {"q": query})
    pairs = re.findall(r'<h2[^>]*><a[^>]*href="([^"]+)"[^>]*>(.*?)</a></h2>', t, re.DOTALL)
    snips = re.findall(r'<p class="b_lineclamp\d*">(.*?)</p>', t, re.DOTALL)
    out = []
    for i, (url, title) in enumerate(pairs[:n]):
        out.append({"title": _strip(title), "url": url,
                    "snippet": _strip(snips[i]) if i < len(snips) else "",
                    "engine": "bing"})
    return [r for r in out if r["title"]]


def _baidu_api(query: str, n: int) -> list[dict]:
    """百度智能云千帆官方「百度搜索」API（需配置 baidu_api_key）。"""
    key = config.load_config().get("baidu_api_key", "").strip()
    if not key:
        return []
    r = httpx.post(
        "https://qianfan.baidubce.com/v2/ai_search/web_search",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        json={"messages": [{"role": "user", "content": query}],
              "search_source": "baidu_search_v2",
              "resource_type_filter": [{"type": "web", "top_k": n}]},
        timeout=20.0, trust_env=False)
    r.raise_for_status()
    data = r.json()
    items = (data.get("references") or data.get("results")
             or data.get("data") or [])
    out = []
    for it in items[:n]:
        if not isinstance(it, dict):
            continue
        title = it.get("title") or it.get("web_anchor") or ""
        url = it.get("url") or it.get("web_url") or ""
        snippet = it.get("content") or it.get("abstract") or it.get("summary") or ""
        if title or snippet:
            out.append({"title": _strip(str(title)), "url": str(url),
                        "snippet": _strip(str(snippet)), "engine": "baidu-api"})
    return out


_ENGINES = {"baidu": _baidu, "bing": _bing, "baidu_api": _baidu_api}


def search(query: str, n: int = 5, engine: str | None = None) -> list[dict]:
    """联网搜索。优先用官方百度 API(若配置了 baidu_api_key)，否则用免费抓取，
    主引擎无结果时回退必应。"""
    cfg = config.load_config()
    # 配置了官方 Key 则优先官方 API
    if cfg.get("baidu_api_key", "").strip():
        try:
            res = _baidu_api(query, n)
            if res:
                return res
        except Exception:
            pass
    engine = engine or cfg.get("search_engine", "baidu")
    primary = _ENGINES.get(engine, _baidu)
    try:
        res = primary(query, n)
    except Exception:
        res = []
    if not res and engine != "bing":
        try:
            res = _bing(query, n)
        except Exception:
            res = []
    return res
