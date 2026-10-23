"""联网工具服务（Phase B 模块 B1）：官方搜索 + 自研抓取 + 来源分级。

Implements docs/联网与子智能体工具设计.md §4/§6/§7 的服务端部分：

- 来源分级注册表（content/web_sources.json）：域名 → T1–T4，未注册一律 T4；
- web_search：经 DeepSeek 官方 Search（Anthropic 格式端点的
  web_search_20250305 服务端工具）检索，结果带出处结构与分级过滤；
- web_fetch：自研抓取——重定向逐跳复检、SSRF 防护、注入模式扫描都
  在本地完成，服务端 Fetch 不暴露这些控制点，故不用；
- 隐私纪律（§4.4）：患者路径的查询只能由 build_education_query 从
  受控部分拼装；自由查询必须先过 scrub_free_query 脱敏管线（命中
  禁止片段整条拒绝，隐私 fail-closed）。本模块不做病例感知，病例
  感知（把病例事实/转录作为 forbidden_fragments 传入）由教育层负责；
- 成本纪律（§4.5）：search+fetch 合计的日熔断（进程内按自然日计数），
  超限直接拒绝并落 web.blocked 事件。

本模块只提供 advisory 数据：返回值永不直接改变分诊与处置（红线 1），
消费方（教育层/复核层）负责后续的引用与呈现。
"""
from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import re
import socket
from datetime import datetime
from html.parser import HTMLParser
from importlib import resources
from threading import Lock
from time import perf_counter
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib import request
from urllib.error import HTTPError
from urllib.parse import urljoin, urlparse
from uuid import uuid4

from .events import Event, EventStore

_WEB_MODES = ("off", "education", "review", "all")
_TIER_RANK = {"T1": 1, "T2": 2, "T3": 3, "T4": 4}
_SEARCH_TIMEOUT_SECONDS = 8
_FETCH_TIMEOUT_SECONDS = 10
_FETCH_MAX_BYTES = 512 * 1024
_FETCH_MAX_REDIRECTS = 3
_FETCH_CHAR_HARD_CAP = 20000
_DEFAULT_BASE_URL = "https://api.deepseek.com/anthropic"
# 事件 session 占位：联网层不是某个问诊会话的一部分，归因信息
# （caller/search_id）放在 payload 里。
_EVENT_SESSION = "web-tools"

_cache: Dict[str, Any] = {}


def web_mode() -> str:
    """off | education | review | all — env override, default off（设计 §4.5）。"""
    mode = os.environ.get("MEDAGENT_WEB_TOOLS", "off").strip().lower()
    return mode if mode in _WEB_MODES else "off"


def load_web_sources() -> Dict[str, Any]:
    """来源分级注册表（模块级缓存，仿 conclusion._load_json）。"""
    if "web_sources" not in _cache:
        raw = (
            resources.files("medagent.content")
            .joinpath("web_sources.json")
            .read_text(encoding="utf-8")
        )
        _cache["web_sources"] = json.loads(raw)
    return _cache["web_sources"]


def sources_version() -> str:
    return str(load_web_sources().get("version", ""))


def _domain_entries() -> Dict[str, Dict[str, Any]]:
    if "web_source_entries" not in _cache:
        entries: Dict[str, Dict[str, Any]] = {}
        for item in load_web_sources().get("sources", []):
            domain = str(item.get("domain", "")).strip().lower()
            if domain:
                entries[domain] = item
        _cache["web_source_entries"] = entries
    return _cache["web_source_entries"]


def _normalize_domain(raw: str) -> str:
    text = str(raw or "").strip().lower()
    if "://" in text:
        text = urlparse(text).hostname or ""
    return text.split(":")[0].rstrip(".")


def _entry_for_domain(domain: str) -> Optional[Dict[str, Any]]:
    """域名等于注册域名或为其子域名时命中（evilwho.int 不命中 who.int）。"""
    host = _normalize_domain(domain)
    if not host:
        return None
    entries = _domain_entries()
    if host in entries:
        return entries[host]
    for registered, entry in entries.items():
        if host.endswith("." + registered):
            return entry
    return None


def tier_for_domain(domain: str) -> str:
    """T1|T2|T3|T4 — 未注册域名一律 T4（设计 §4.3）。"""
    entry = _entry_for_domain(domain)
    if entry is None:
        return "T4"
    tier = str(entry.get("tier", "T4"))
    return tier if tier in _TIER_RANK else "T4"


def publisher_for_domain(domain: str) -> Optional[str]:
    entry = _entry_for_domain(domain)
    if entry is None:
        return None
    publisher = entry.get("publisher")
    return str(publisher) if publisher else None


# ---------------------------------------------------------------------------
# 查询构造与脱敏（设计 §4.4）
# ---------------------------------------------------------------------------

# topic 只许汉字、拉丁字母与空格组成的短语；含数字串或任何标点即
# 视为不受控内容，整段丢弃（查询宁可泛化，不可夹带患者原文）。
_TOPIC_OK = re.compile(r"^[一-鿿A-Za-z ]+$")


def build_education_query(patient_label: str, topic: Optional[str] = None) -> str:
    """患者教育查询的唯一合法构造方式：受控词表拼装，患者原文不参与。"""
    label = str(patient_label or "").strip()
    cleaned = ""
    if topic:
        candidate = " ".join(str(topic).split())
        if candidate and _TOPIC_OK.match(candidate):
            cleaned = candidate
    if cleaned:
        return f"{label} {cleaned} 患者教育"
    return f"{label} 患者教育"


_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_ID_CARD_RE = re.compile(r"(?<!\d)\d{17}[0-9Xx](?![0-9Xx])|(?<!\d)\d{15}(?!\d)")
_MOBILE_RE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
_DATE_RES = (
    re.compile(r"\d{4}[-/]\d{1,2}[-/]\d{1,2}"),
    re.compile(r"\d{4}年\d{1,2}月(?:\d{1,2}日)?"),
    re.compile(r"\d{1,2}月\d{1,2}日"),
)
_AGE_RE = re.compile(r"\d{1,3}\s*岁")


def _contains_forbidden_fragment(text: str, fragments: Tuple[str, ...]) -> bool:
    """任一 forbidden_fragment 的连续 ≥8 字子串出现在文本中即命中。"""
    for fragment in fragments:
        frag = str(fragment or "")
        if len(frag) < 8:
            if frag and frag in text:
                return True
            continue
        for start in range(len(frag) - 7):
            if frag[start : start + 8] in text:
                return True
    return False


def scrub_free_query(
    text: str, *, forbidden_fragments: Tuple[str, ...] = ()
) -> Optional[str]:
    """自由查询脱敏管线（设计 §4.4）：清掉可识别信息后仍命中病例
    原文片段（≥8 字）则整条拒绝返回 None——隐私 fail-closed。

    姓名无法靠规则可靠识别，靠 forbidden_fragments 兜底：调用方应把
    结构化姓名等标识字段作为禁止片段传入。
    """
    cleaned = str(text or "")
    cleaned = _EMAIL_RE.sub("", cleaned)
    cleaned = _ID_CARD_RE.sub("", cleaned)
    cleaned = _MOBILE_RE.sub("", cleaned)
    for pattern in _DATE_RES:
        cleaned = pattern.sub("", cleaned)
    cleaned = _AGE_RE.sub("", cleaned)
    cleaned = " ".join(cleaned.split())
    if not cleaned:
        return None
    if _contains_forbidden_fragment(cleaned, tuple(forbidden_fragments)):
        return None
    return cleaned


# ---------------------------------------------------------------------------
# 引用（设计 §4.2 Citation）
# ---------------------------------------------------------------------------


def make_citation(item: Dict[str, Any], quote: str) -> Dict[str, Any]:
    """把一条搜索结果包装为结论单统一引用结构。

    URL 只能来自供应商响应（item 由 WebTools.search 产出），模型永不
    生成 URL——引用真实性靠这条结构纪律，而非事后检查。
    """
    domain = str(item.get("domain") or _normalize_domain(str(item.get("url", ""))))
    return {
        "citation_id": "cit_" + uuid4().hex[:12],
        "title": item.get("title", ""),
        "url": item.get("url", ""),
        "publisher": publisher_for_domain(domain) or domain,
        "published_at": item.get("published_at"),
        "fetched_at": item.get("fetched_at"),
        "tier": item.get("tier") or tier_for_domain(domain),
        "quote": quote,
    }


# ---------------------------------------------------------------------------
# 抓取正文的极简 HTML 提取
# ---------------------------------------------------------------------------


class _TextExtractor(HTMLParser):
    """去 script/style 后提取正文文本、<title> 与发布时间 meta。"""

    _PUBLISHED_KEYS = {"article:published_time", "date", "publishdate", "pubdate"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._skip_depth = 0
        self._in_title = False
        self.title_parts: List[str] = []
        self.text_parts: List[str] = []
        self.published_at: Optional[str] = None

    def handle_starttag(self, tag: str, attrs: List[Tuple[str, Optional[str]]]) -> None:
        if tag in ("script", "style", "noscript"):
            self._skip_depth += 1
        elif tag == "title":
            self._in_title = True
        elif tag == "meta" and self.published_at is None:
            attr = dict(attrs)
            key = (attr.get("property") or attr.get("name") or "").lower()
            if key in self._PUBLISHED_KEYS and attr.get("content"):
                self.published_at = attr["content"]

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style", "noscript") and self._skip_depth:
            self._skip_depth -= 1
        elif tag == "title":
            self._in_title = False

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        if self._in_title:
            self.title_parts.append(data)
        else:
            self.text_parts.append(data)


# 抓取正文中的指令型文本（提示注入）特征：命中只标记、不删除，
# 消费方据 injection_suspect 决定是否退出患者展示（设计 §4.5/§5）。
_INJECTION_PATTERNS = (
    "ignore previous",
    "ignore the above",
    "ignore all previous",
    "disregard previous",
    "system prompt",
    "you are now",
    "忽略以上",
    "忽略前面",
    "忽略之前",
    "忽略上述",
    "你现在是",
    "系统提示",
    "系统指令",
    "新的指令",
)


def _looks_injected(text: str) -> bool:
    lowered = text.lower()
    return any(pattern in lowered for pattern in _INJECTION_PATTERNS)


def _parse_page_date(value: Any) -> Optional[str]:
    """供应商 page_age（如 "March 31, 2025"）尽力转 ISO 日期，失败为 None。"""
    if not value:
        return None
    text = str(value).strip()
    for fmt in ("%Y-%m-%d", "%B %d, %Y", "%d %B %Y", "%b %d, %Y"):
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue
    return None


# ---------------------------------------------------------------------------
# WebTools 服务
# ---------------------------------------------------------------------------

Transport = Callable[
    [str, str, Dict[str, str], bytes, float], Tuple[int, Dict[str, str], bytes]
]


def _default_transport(
    method: str, url: str, headers: Dict[str, str], body: bytes, timeout: float
) -> Tuple[int, Dict[str, str], bytes]:
    """urllib 实现（仿 backends.py 的 urlopen 风格）；HTTP 错误状态
    以返回值而非异常呈现，供调用方映射为 provider_error。"""
    req = request.Request(url, data=body or None, headers=headers, method=method)
    try:
        with request.urlopen(req, timeout=timeout) as response:
            return (
                response.status,
                dict(response.headers.items()),
                response.read(),
            )
    except HTTPError as error:
        return (
            error.code,
            dict(error.headers.items()) if error.headers else {},
            error.read(),
        )


class _NoRedirectHandler(request.HTTPRedirectHandler):
    """抓取的重定向由 WebTools.fetch 手动逐跳复检，传输层不自动跟随。"""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D102
        return None


class WebTools:
    """联网工具服务：search（官方 Search）+ fetch（自研抓取）。

    events 为 EventStore 或 None（None 时不落事件）；transport 可注入
    以便离线测试，签名 transport(method, url, headers, body_bytes,
    timeout) -> (status, headers, body_bytes)。fetch 不走 transport，
    其网络出口收敛在 _http_get，便于测试替换。
    """

    # 与模块级函数同一实现，两种调用形态都可用。
    make_citation = staticmethod(make_citation)

    def __init__(
        self,
        events: Optional[EventStore] = None,
        *,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        transport: Optional[Transport] = None,
        daily_cap: int = 500,
        now: Optional[Callable[[], datetime]] = None,
    ) -> None:
        self.events = events
        self.api_key = (
            api_key if api_key is not None else os.environ.get("MEDAGENT_LLM_API_KEY", "")
        )
        base = (
            base_url
            if base_url is not None
            else os.environ.get("MEDAGENT_LLM_BASE_URL", "")
        )
        self.base_url = (base or _DEFAULT_BASE_URL).rstrip("/")
        self._transport: Transport = transport or _default_transport
        self.daily_cap = daily_cap
        self._now = now or (lambda: datetime.now().astimezone())
        self._day_key: Optional[Any] = None
        self._day_count = 0
        # 同一实例被教育层线程与子智能体线程池共用：日熔断的
        # 检查+自增必须在锁内完成，否则并发下会超额放行。
        self._daily_lock = Lock()

    # -- 内部工具 ------------------------------------------------------

    def _emit(self, event_type: str, payload: Dict[str, Any]) -> None:
        if self.events is not None:
            self.events.append(Event(_EVENT_SESSION, event_type, payload))

    def _claim_daily(self, op: str) -> bool:
        """日熔断（设计 §4.5）：search+fetch 合计，进程内按自然日计数。"""
        with self._daily_lock:
            today = self._now().date()
            if today != self._day_key:
                self._day_key = today
                self._day_count = 0
            if self._day_count >= self.daily_cap:
                self._emit("web.blocked", {"reason": "daily_cap", "op": op})
                return False
            self._day_count += 1
            return True

    def _fetched_at(self) -> str:
        return self._now().isoformat(timespec="seconds")

    # -- search --------------------------------------------------------

    def search(
        self,
        query: str,
        *,
        purpose: str,
        min_tier: str = "T3",
        max_results: int = 5,
        caller: str = "education",
    ) -> Dict[str, Any]:
        """官方 Search 检索：结果带分级与出处，低级别只计数不返回。

        status ∈ ok | insufficient_authoritative_results |
        provider_error | timeout | daily_budget_exceeded。
        """
        search_id = "ws_" + uuid4().hex[:12]
        base: Dict[str, Any] = {
            "search_id": search_id,
            "query_as_sent": query,
            "provider": "deepseek-search",
            "results": [],
            "filtered_below_tier": 0,
            "status": "provider_error",
        }
        if not self._claim_daily("search"):
            base["status"] = "daily_budget_exceeded"
            return base
        self._emit(
            "web.search_requested",
            {
                "search_id": search_id,
                "query_as_sent": query,
                "purpose": purpose,
                "caller": caller,
                "min_tier": min_tier,
            },
        )
        started = perf_counter()
        status, results, filtered = self._run_search(query, min_tier, max_results)
        duration_ms = int((perf_counter() - started) * 1000)
        base.update(
            {"results": results, "filtered_below_tier": filtered, "status": status}
        )
        tiers: Dict[str, int] = {}
        for item in results:
            tiers[item["tier"]] = tiers.get(item["tier"], 0) + 1
        self._emit(
            "web.search_completed",
            {
                "search_id": search_id,
                "caller": caller,
                "result_count": len(results),
                "tiers": tiers,
                "duration_ms": duration_ms,
                "status": status,
            },
        )
        return base

    def _run_search(
        self, query: str, min_tier: str, max_results: int
    ) -> Tuple[str, List[Dict[str, Any]], int]:
        model = os.environ.get("MEDAGENT_LLM_MODEL", "")
        payload = {
            "model": model,
            "max_tokens": 1024,
            "messages": [{"role": "user", "content": query}],
            "tools": [
                {"type": "web_search_20250305", "name": "web_search", "max_uses": 1}
            ],
        }
        headers = {
            "Content-Type": "application/json",
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
        }
        try:
            status_code, _resp_headers, raw = self._transport(
                "POST",
                self.base_url + "/messages",
                headers,
                json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                _SEARCH_TIMEOUT_SECONDS,
            )
        except (TimeoutError, socket.timeout):
            return "timeout", [], 0
        except Exception:
            return "provider_error", [], 0
        if status_code != 200:
            return "provider_error", [], 0
        try:
            document = json.loads(raw.decode("utf-8"))
        except Exception:
            return "provider_error", [], 0
        raw_items, saw_error = self._extract_search_items(document)
        if saw_error and not raw_items:
            return "provider_error", [], 0
        fetched_at = self._fetched_at()
        threshold = _TIER_RANK.get(min_tier, 3)
        seen_urls = set()
        results: List[Dict[str, Any]] = []
        filtered = 0
        for item in raw_items:
            url = str(item.get("url") or "")
            if not url or url in seen_urls:
                continue
            seen_urls.add(url)
            domain = _normalize_domain(url)
            tier = tier_for_domain(domain)
            if _TIER_RANK[tier] > threshold:
                filtered += 1
                continue
            results.append(
                {
                    "title": str(item.get("title") or ""),
                    "url": url,
                    "domain": domain,
                    "snippet": str(item.get("snippet") or "")[:300],
                    "published_at": item.get("published_at")
                    or _parse_page_date(item.get("page_age")),
                    "fetched_at": fetched_at,
                    "tier": tier,
                    "source_id": domain,
                }
            )
        results = results[: max(1, min(8, max_results))]
        if not results:
            return "insufficient_authoritative_results", [], filtered
        return "ok", results, filtered

    @staticmethod
    def _extract_search_items(
        document: Dict[str, Any],
    ) -> Tuple[List[Dict[str, Any]], bool]:
        """解析 Anthropic 响应 content 中的 web_search_tool_result 块。"""
        items: List[Dict[str, Any]] = []
        saw_error = False
        for block in document.get("content") or []:
            if not isinstance(block, dict):
                continue
            if block.get("type") != "web_search_tool_result":
                continue
            inner = block.get("content")
            if isinstance(inner, dict):
                if inner.get("type") == "web_search_tool_result_error":
                    saw_error = True
                continue
            for entry in inner or []:
                if isinstance(entry, dict) and entry.get("type") == "web_search_result":
                    items.append(entry)
        return items, saw_error

    # -- fetch ---------------------------------------------------------

    def fetch(
        self, url: str, *, max_chars: int = 8000, caller: str = "education"
    ) -> Dict[str, Any]:
        """自研抓取：全量安全校验（设计 §4.2/§6 红线 2），失败也落事件。"""
        result: Dict[str, Any] = {
            "url": url,
            "final_url": url,
            "title": "",
            "published_at": None,
            "fetched_at": self._fetched_at(),
            "content": "",
            "content_sha256": hashlib.sha256(b"").hexdigest(),
            "tier": tier_for_domain(_normalize_domain(url)),
            "injection_suspect": False,
            "truncated": False,
            "status": "provider_error",
        }
        if not self._claim_daily("fetch"):
            result["status"] = "daily_budget_exceeded"
            return result
        try:
            status, final_url, content, truncated, title, published = self._fetch_follow(
                url, max(1, min(_FETCH_CHAR_HARD_CAP, max_chars))
            )
            result.update(
                {
                    "status": status,
                    "final_url": final_url,
                    "content": content,
                    "truncated": truncated,
                    "title": title,
                    "published_at": published,
                }
            )
        except Exception:
            result["status"] = "provider_error"
        result["tier"] = tier_for_domain(_normalize_domain(result["final_url"]))
        result["content_sha256"] = hashlib.sha256(
            result["content"].encode("utf-8")
        ).hexdigest()
        result["injection_suspect"] = _looks_injected(result["content"])
        self._emit(
            "web.fetch_completed",
            {
                "domain": _normalize_domain(result["final_url"]),
                "content_sha256": result["content_sha256"],
                "tier": result["tier"],
                "injection_suspect": result["injection_suspect"],
                "status": result["status"],
                "caller": caller,
            },
        )
        return result

    def _fetch_follow(
        self, url: str, char_cap: int
    ) -> Tuple[str, str, str, bool, str, Optional[str]]:
        """手动跟随重定向（≤3 跳），每一跳重做全部安全校验。"""
        current = url
        hops = 0
        while True:
            blocked = self._validate_target(current)
            if blocked is not None:
                return blocked, current, "", False, "", None
            try:
                status_code, headers, body = self._http_get(
                    current, _FETCH_TIMEOUT_SECONDS
                )
            except (TimeoutError, socket.timeout):
                return "timeout", current, "", False, "", None
            except Exception:
                return "provider_error", current, "", False, "", None
            lowered = {str(k).lower(): v for k, v in headers.items()}
            if status_code in (301, 302, 303, 307, 308):
                location = lowered.get("location")
                if not location:
                    return "http_error", current, "", False, "", None
                hops += 1
                if hops > _FETCH_MAX_REDIRECTS:
                    return "too_many_redirects", current, "", False, "", None
                current = urljoin(current, location)
                continue
            if status_code != 200:
                return "http_error", current, "", False, "", None
            content_type = str(lowered.get("content-type", "")).split(";")[0].strip()
            if content_type not in ("text/html", "text/plain"):
                return "unsupported_content_type", current, "", False, "", None
            text = body.decode("utf-8", errors="replace")
            title = ""
            published = None
            if content_type == "text/html":
                extractor = _TextExtractor()
                extractor.feed(text)
                title = " ".join(" ".join(extractor.title_parts).split())
                published = extractor.published_at
                text = " ".join(" ".join(extractor.text_parts).split())
            else:
                text = " ".join(text.split())
            truncated = len(text) > char_cap
            return "ok", current, text[:char_cap], truncated, title, published

    def _validate_target(self, url: str) -> Optional[str]:
        """抓取目标校验：通过返回 None，拒绝返回 status 字符串。

        规则（设计 §4.4/§6）：仅 https；禁 URL credentials；主机名不得
        是 IP 字面量；DNS 解析后任一地址非公网即拒（防 DNS rebinding
        的完整答案集检查——本层校验解析结果，连接层用同一主机名，
        残余 TOCTOU 风险记在设计文档的已知限制内）。
        """
        parsed = urlparse(url)
        if parsed.scheme != "https":
            return "blocked_url"
        if parsed.username or parsed.password:
            return "blocked_url"
        host = parsed.hostname
        if not host:
            return "blocked_url"
        try:
            ipaddress.ip_address(host)
            return "blocked_url"  # IP 字面量主机一律拒绝
        except ValueError:
            pass
        try:
            infos = socket.getaddrinfo(host, 443)
        except Exception:
            return "provider_error"
        if not infos:
            return "provider_error"
        for info in infos:
            address = info[4][0]
            try:
                ip = ipaddress.ip_address(address)
            except ValueError:
                return "blocked_ssrf"
            if (
                ip.is_private
                or ip.is_loopback
                or ip.is_link_local
                or ip.is_multicast
                or ip.is_reserved
                or ip.is_unspecified
            ):
                return "blocked_ssrf"
        return None

    def _http_get(
        self, url: str, timeout: float
    ) -> Tuple[int, Dict[str, str], bytes]:
        """抓取的唯一网络出口：不自动跟随重定向，读取上限 512KB。

        测试以 monkeypatch 替换本方法（并替换 socket.getaddrinfo）
        即可全程离线驱动 fetch。
        """
        req = request.Request(
            url,
            headers={"User-Agent": "chengxin-web-tools/1.0"},
            method="GET",
        )
        opener = request.build_opener(_NoRedirectHandler())
        try:
            with opener.open(req, timeout=timeout) as response:
                return (
                    response.status,
                    dict(response.headers.items()),
                    response.read(_FETCH_MAX_BYTES),
                )
        except HTTPError as error:
            return (
                error.code,
                dict(error.headers.items()) if error.headers else {},
                error.read(_FETCH_MAX_BYTES),
            )
