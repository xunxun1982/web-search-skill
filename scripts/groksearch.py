#!/usr/bin/env python3
from __future__ import annotations

import argparse
import dataclasses
import html
import ipaddress
import json
import os
import queue
import random
import re
import socket
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover
    tomllib = None


USER_AGENT = "web-research-direct-skill/1.0"
SUPPORTED_ENV_NAMES = [
    "GROK_SEARCH_API_KEY",
    "GROK_SEARCH_URL",
    "GROK_SEARCH_MODEL",
    "GROK_SEARCH_WEB_SEARCH",
    "TAVILY_API_KEY",
    "TAVILY_API_URL",
    "FIRECRAWL_API_KEY",
    "FIRECRAWL_API_URL",
    "GITHUB_TOKEN",
    "GROK_SEARCH_TIMEOUT_SECONDS",
    "GROK_SEARCH_MAX_RETRIES",
    "SEARCH_CACHE_DIR",
    "GROK_SEARCH_FETCH_MAX_CHARS",
    "GROK_SEARCH_ALLOW_INTERNAL_FETCH",
    "GROK_SEARCH_RESPONSE_MAX_CHARS",
]

UPSTREAM_DEFAULTS = {
    "grok_search": {
        "grok_search_url": "https://api.x.ai",
        "grok_search_model": "grok-4.20-fast",
    },
    "tavily": {
        "tavily_api_url": "https://api.tavily.com",
    },
    "firecrawl": {
        "firecrawl_api_url": "https://api.firecrawl.dev",
    },
}
UPSTREAM_REQUIRED_KEYS = {
    "grok_search": ("grok_search_api_key", "grok_search_model", "grok_search_url"),
    "tavily": ("tavily_api_key", "tavily_api_url"),
    "firecrawl": ("firecrawl_api_key", "firecrawl_api_url"),
}


def eprint(message: str) -> None:
    print(message, file=sys.stderr)


def configure_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")


def clamp_text(text: str, max_chars: int | None) -> tuple[str, bool]:
    if max_chars and max_chars > 0 and len(text) > max_chars:
        return text[:max_chars] + "\n\n[truncated]", True
    return text, False


def strip_html(value: str) -> str:
    value = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", value)
    value = re.sub(r"(?s)<[^>]+>", " ", value)
    value = html.unescape(value)
    return re.sub(r"\s+", " ", value).strip()


def normalize_base(url: str, suffix: str) -> str:
    base = (url or "").strip().rstrip("/")
    if not base:
        return ""
    if base.endswith(suffix):
        return base
    if base.endswith("/v1"):
        return base + suffix
    if "/v1/" in base:
        return base.rsplit("/v1/", 1)[0] + "/v1" + suffix
    return base + "/v1" + suffix


def normalize_v1_base(url: str) -> str:
    base = (url or "").strip().rstrip("/")
    if not base:
        return ""
    for suffix in ("/responses", "/chat/completions"):
        if base.endswith(suffix):
            base = base[: -len(suffix)]
            break
    if base.endswith("/v1"):
        return base
    if "/v1/" in base:
        return base.rsplit("/v1/", 1)[0] + "/v1"
    return base + "/v1"


def default_cache_dir(explicit: str = "") -> Path:
    if explicit:
        return Path(explicit).expanduser()
    return Path(tempfile.gettempdir()) / "grok-search-skill" / "cache"


def skill_dir() -> Path:
    return Path(__file__).resolve().parents[1]


def dedupe_paths(paths: list[Path]) -> list[Path]:
    seen: set[str] = set()
    result: list[Path] = []
    for path in paths:
        key = str(path.expanduser()).lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(path)
    return result


def skill_config_candidates() -> list[Path]:
    root = skill_dir()
    return [root / "config.toml"]


def user_config_candidates() -> list[Path]:
    paths: list[Path] = []
    userprofile = os.environ.get("USERPROFILE")
    if userprofile:
        paths.append(Path(userprofile) / ".config" / "grok-search-skill" / "config.toml")
    home = os.environ.get("HOME")
    if home:
        paths.append(Path(home) / ".config" / "grok-search-skill" / "config.toml")
    else:
        paths.append(Path.home() / ".config" / "grok-search-skill" / "config.toml")
    return dedupe_paths(paths)


def fallback_config_candidates() -> list[Path]:
    paths: list[Path] = []
    explicit = os.environ.get("WEB_RESEARCH_CONFIG")
    if explicit:
        explicit_path = Path(explicit).expanduser()
        if explicit_path.suffix.lower() == ".toml":
            paths.append(explicit_path)
    return dedupe_paths(paths)


def config_file_sources() -> list[tuple[str, Path]]:
    entries = [
        *(("user", path) for path in user_config_candidates()),
        *(("skill-local", path) for path in skill_config_candidates()),
        *(("fallback", path) for path in fallback_config_candidates()),
    ]
    seen: set[str] = set()
    result: list[tuple[str, Path]] = []
    for source, path in entries:
        key = str(path.expanduser()).lower()
        if key in seen:
            continue
        seen.add(key)
        result.append((source, path))
    return result


def config_file_candidates() -> list[Path]:
    return [path for _, path in config_file_sources()]


def config_file_statuses() -> list[dict[str, Any]]:
    return [
        {"priority": index, "source": source, "path": str(path), "exists": path.exists()}
        for index, (source, path) in enumerate(config_file_sources(), start=1)
    ]


def read_config_file(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    if tomllib is None:  # pragma: no cover - Python < 3.11 only
        raise RuntimeError("Python 3.11+ is required to parse TOML configuration")
    return tomllib.loads(text)


def config_value_is_effective(key: str, value: Any) -> bool:
    if isinstance(value, str):
        return bool(value.strip())
    if key.endswith("_upstreams") and isinstance(value, list):
        provider = key[: -len("_upstreams")]
        return any(
            isinstance(raw, dict)
            and has_required_upstream_values(provider, {**UPSTREAM_DEFAULTS.get(provider, {}), **lower_keys(raw)})
            for raw in value
        )
    return True


def merge_missing(target: dict[str, Any], source: dict[str, Any]) -> None:
    for key, value in lower_keys(source).items():
        if not config_value_is_effective(key, value):
            continue
        target.setdefault(key, value)


def env_config_values() -> dict[str, str]:
    return {name.lower(): os.environ[name] for name in SUPPORTED_ENV_NAMES if name in os.environ}


def env_presence() -> dict[str, bool]:
    names = [*SUPPORTED_ENV_NAMES, "WEB_RESEARCH_CONFIG"]
    return {name: name in os.environ for name in names}


def load_file_config() -> dict[str, Any]:
    merged: dict[str, Any] = {}
    for path in user_config_candidates():
        if not path.exists():
            continue
        merge_missing(merged, read_config_file(path))
    merge_missing(merged, env_config_values())
    for path in skill_config_candidates():
        if not path.exists():
            continue
        merge_missing(merged, read_config_file(path))
    for path in fallback_config_candidates():
        if not path.exists():
            continue
        merge_missing(merged, read_config_file(path))
    return merged


def lower_keys(data: dict[str, Any]) -> dict[str, Any]:
    return {str(k).lower(): v for k, v in data.items()}


def has_required_upstream_values(provider: str, upstream: dict[str, Any]) -> bool:
    return all(str(upstream.get(key, "")).strip() for key in UPSTREAM_REQUIRED_KEYS.get(provider, ()))


def random_upstream(cfg: "Config", provider: str) -> dict[str, str] | None:
    table_key = f"{provider}_upstreams"
    raw_upstreams = cfg.file_values.get(table_key)
    upstreams: list[dict[str, str]] = []
    defaults = UPSTREAM_DEFAULTS.get(provider, {})

    if isinstance(raw_upstreams, list):
        for raw in raw_upstreams:
            if not isinstance(raw, dict):
                continue
            item = {**defaults, **lower_keys(raw)}
            if has_required_upstream_values(provider, item):
                upstreams.append({str(k): str(v) for k, v in item.items()})

    if upstreams:
        return random.choice(upstreams)

    key_name = f"{provider}_api_key"
    if key_name not in cfg.file_values:
        return None
    fallback = {**defaults}
    for key in [key_name, f"{provider}_api_url", f"{provider}_url", f"{provider}_model"]:
        if key in cfg.file_values:
            fallback[key] = cfg.file_values[key]
    if not has_required_upstream_values(provider, fallback):
        return None
    return {str(k): str(v) for k, v in fallback.items()}


def complete_upstream_count(cfg: "Config", provider: str) -> int:
    raw_upstreams = cfg.file_values.get(f"{provider}_upstreams")
    if isinstance(raw_upstreams, list):
        return sum(
            1
            for raw in raw_upstreams
            if isinstance(raw, dict)
            and has_required_upstream_values(provider, {**UPSTREAM_DEFAULTS.get(provider, {}), **lower_keys(raw)})
        )
    return int(random_upstream(cfg, provider) is not None)


@dataclasses.dataclass
class Config:
    file_values: dict[str, Any]

    def get(self, env_name: str, default: str = "") -> str:
        key = env_name.lower()
        if key in self.file_values:
            return str(self.file_values[key])
        return default

    def get_int(self, env_name: str, default: int) -> int:
        raw = self.get(env_name, str(default))
        try:
            return int(raw)
        except ValueError:
            return default

    def get_bool(self, env_name: str, default: bool) -> bool:
        raw = self.get(env_name, "true" if default else "false").strip().lower()
        return raw in {"1", "true", "yes", "on"}

    @property
    def timeout(self) -> int:
        return self.get_int("GROK_SEARCH_TIMEOUT_SECONDS", 120)

    @property
    def grok_search_max_retries(self) -> int:
        return max(self.get_int("GROK_SEARCH_MAX_RETRIES", 5), 0)

    @property
    def fetch_max_chars(self) -> int:
        return self.get_int("GROK_SEARCH_FETCH_MAX_CHARS", 0)

    @property
    def allow_internal_fetch(self) -> bool:
        return self.get_bool("GROK_SEARCH_ALLOW_INTERNAL_FETCH", False)

    @property
    def response_max_chars(self) -> int:
        return self.get_int("GROK_SEARCH_RESPONSE_MAX_CHARS", 60000)

    @property
    def cache_dir(self) -> Path:
        return default_cache_dir(self.get("SEARCH_CACHE_DIR"))


class HttpError(RuntimeError):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


def is_timeout_error(exc: BaseException) -> bool:
    if isinstance(exc, TimeoutError):
        return True
    if isinstance(exc, urllib.error.URLError):
        reason = getattr(exc, "reason", None)
        return isinstance(reason, TimeoutError) or "timed out" in str(reason).lower()
    return "timed out" in str(exc).lower()


def is_internal_address(address: str) -> bool:
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return False
    return not ip.is_global


def resolve_host(host: str, port: int | None, timeout: float) -> list[Any]:
    result: queue.Queue[tuple[bool, Any]] = queue.Queue(maxsize=1)

    def worker() -> None:
        try:
            result.put((True, socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)), block=False)
        except OSError as exc:
            result.put((False, exc), block=False)

    # DNS preflight runs before urlopen, so it needs its own caller-bound timeout.
    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    try:
        ok, value = result.get(timeout=max(float(timeout), 0.001))
    except queue.Empty as exc:
        raise HttpError(0, "network failure: DNS lookup timed out") from exc
    if not ok:
        raise HttpError(0, f"network failure: {value}") from value
    return value


def validate_web_url(url: str, *, allow_internal: bool = False, timeout: float = 60) -> None:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise HttpError(400, "URL must use http or https")
    if parsed.username or parsed.password:
        raise HttpError(400, "URL credentials are not allowed")
    try:
        port = parsed.port
    except ValueError as exc:
        raise HttpError(400, "invalid URL port") from exc
    host = parsed.hostname.strip().rstrip(".").lower()
    if not allow_internal:
        # User-supplied fetch URLs must stay on public web targets to avoid SSRF.
        if host in {"localhost", "localhost.localdomain"}:
            raise HttpError(400, "internal URL targets are not allowed")
        if is_internal_address(host):
            raise HttpError(400, "internal URL targets are not allowed")

    # Internal targets skip only address rejection; DNS still stays timeout-bound.
    resolved = resolve_host(host, port, timeout)
    if not allow_internal:
        for *_, sockaddr in resolved:
            if sockaddr and is_internal_address(str(sockaddr[0])):
                raise HttpError(400, "internal URL targets are not allowed")


class PublicRedirectHandler(urllib.request.HTTPRedirectHandler):
    def __init__(self, timeout: float = 60):
        self.timeout = timeout

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D102
        validate_web_url(newurl, timeout=self.timeout)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def request_text(
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    payload: dict[str, Any] | None = None,
    timeout: int = 60,
    allow_internal: bool = False,
) -> str:
    validate_web_url(url, allow_internal=allow_internal, timeout=timeout)
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    request_headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/json, text/plain, */*",
        **(headers or {}),
    }
    if payload is not None:
        request_headers.setdefault("Content-Type", "application/json")
    req = urllib.request.Request(url, data=body, headers=request_headers, method=method)
    try:
        opener = urllib.request if allow_internal else urllib.request.build_opener(PublicRedirectHandler(timeout))
        with opener.urlopen(req, timeout=timeout) as resp:
            charset = resp.headers.get_content_charset() or "utf-8"
            return resp.read().decode(charset, errors="replace")
    except urllib.error.HTTPError as exc:
        error_text = exc.read().decode("utf-8", errors="replace")
        raise HttpError(exc.code, error_text[:2000]) from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise HttpError(0, f"network failure: {exc}") from exc


def request_json(
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    payload: dict[str, Any] | None = None,
    timeout: int = 60,
    allow_internal: bool = False,
) -> dict[str, Any]:
    text = request_text(
        method,
        url,
        headers=headers,
        payload=payload,
        timeout=timeout,
        allow_internal=allow_internal,
    )
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        return {"text": text}
    return value if isinstance(value, dict) else {"value": value}


def bearer_headers(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}"} if key else {}


def find_urls(obj: Any) -> list[str]:
    found: list[str] = []

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                if key.lower() in {"url", "href"} and isinstance(item, str) and item.startswith("http"):
                    found.append(item)
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)
        elif isinstance(value, str):
            found.extend(re.findall(r"https?://[^\s\[\])>\"']+", value))

    walk(obj)
    deduped: list[str] = []
    seen: set[str] = set()
    for url in found:
        clean = url.rstrip(".,;*")
        if clean not in seen:
            seen.add(clean)
            deduped.append(clean)
    return deduped


def extract_ai_text(data: dict[str, Any]) -> str:
    if isinstance(data.get("output_text"), str):
        return data["output_text"].strip()
    choices = data.get("choices")
    if isinstance(choices, list) and choices:
        message = choices[0].get("message", {}) if isinstance(choices[0], dict) else {}
        content = message.get("content")
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, list):
            parts = []
            for item in content:
                if isinstance(item, dict):
                    text = item.get("text") or item.get("content")
                    if isinstance(text, str):
                        parts.append(text)
            return "\n".join(parts).strip()
    output = data.get("output")
    parts: list[str] = []
    if isinstance(output, list):
        for item in output:
            if not isinstance(item, dict):
                continue
            content = item.get("content")
            if isinstance(content, list):
                for chunk in content:
                    if isinstance(chunk, dict):
                        text = chunk.get("text") or chunk.get("output_text")
                        if isinstance(text, str):
                            parts.append(text)
            elif isinstance(content, str):
                parts.append(content)
    return "\n".join(parts).strip()


def ai_search(cfg: Config, query: str) -> dict[str, Any] | None:
    upstream = random_upstream(cfg, "grok_search")
    if not upstream:
        return None
    key = upstream.get("grok_search_api_key", "")
    if not key:
        return None
    base = upstream.get("grok_search_url", "https://api.x.ai")
    model = upstream.get("grok_search_model", "grok-4.20-fast")
    use_web_tool = cfg.get_bool("GROK_SEARCH_WEB_SEARCH", True)

    endpoint = normalize_v1_base(base) + "/chat/completions"
    payload: dict[str, Any] = {
        "model": model,
        "messages": [{"role": "user", "content": query}],
        "stream": False,
    }
    if use_web_tool:
        payload["tools"] = [{"type": "web_search"}]
    data = request_json(
        "POST",
        endpoint,
        headers=bearer_headers(key),
        payload=payload,
        timeout=cfg.timeout,
        allow_internal=True,
    )

    return {
        "answer": extract_ai_text(data),
        "urls": find_urls(data),
        "provider": "ai",
    }


def tavily_search(
    cfg: Config,
    query: str,
    *,
    max_sources: int,
    detailed: bool,
    include_domains: list[str],
    exclude_domains: list[str],
    recency_days: int | None,
) -> dict[str, Any] | None:
    upstream = random_upstream(cfg, "tavily")
    if not upstream:
        return None
    key = upstream.get("tavily_api_key", "")
    if not key:
        return None
    base = upstream.get("tavily_api_url", "https://api.tavily.com").rstrip("/")
    endpoint = base + "/search"

    payload: dict[str, Any] = {
        "query": query,
        "max_results": max_sources,
        "search_depth": "advanced",
        "include_answer": True,
        "include_raw_content": detailed,
    }
    if include_domains:
        payload["include_domains"] = include_domains
    if exclude_domains:
        payload["exclude_domains"] = exclude_domains
    if recency_days:
        payload["topic"] = "news"
        payload["days"] = recency_days

    data = request_json(
        "POST",
        endpoint,
        headers=bearer_headers(key),
        payload=payload,
        timeout=cfg.timeout,
        allow_internal=True,
    )
    results = data.get("results", [])
    sources: list[dict[str, Any]] = []
    if isinstance(results, list):
        for item in results:
            if not isinstance(item, dict):
                continue
            url = str(item.get("url") or "").strip()
            if not url:
                continue
            sources.append(
                {
                    "title": item.get("title") or url,
                    "url": url,
                    "content": item.get("raw_content") or item.get("content") or "",
                    "score": item.get("score"),
                    "published_date": item.get("published_date"),
                    "provider": "tavily",
                }
            )
    return {
        "answer": data.get("answer") or "",
        "sources": sources,
        "provider": "tavily",
    }


def save_session(cfg: Config, payload: dict[str, Any]) -> str:
    session_id = uuid.uuid4().hex[:16]
    cfg.cache_dir.mkdir(parents=True, exist_ok=True)
    path = cfg.cache_dir / f"{session_id}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return session_id


def read_session(cfg: Config, session_id: str) -> dict[str, Any]:
    if not re.fullmatch(r"[a-fA-F0-9]{16,32}", session_id):
        raise SystemExit("invalid session id")
    path = cfg.cache_dir / f"{session_id}.json"
    if not path.exists():
        raise SystemExit(f"session not found: {session_id}")
    return json.loads(path.read_text(encoding="utf-8"))


def command_search(args: argparse.Namespace, cfg: Config) -> None:
    detailed = args.format == "detailed"
    max_chars = args.max_chars or cfg.response_max_chars
    max_retries = args.grok_max_retries if args.grok_max_retries is not None else cfg.grok_search_max_retries
    max_retries = max(max_retries, 0)
    warnings: list[str] = []
    ai_result: dict[str, Any] | None = None
    tavily_result: dict[str, Any] | None = None
    ai_failed = False

    for attempt in range(max_retries + 1):
        try:
            ai_result = ai_search(cfg, args.query)
            if ai_result is None:
                ai_failed = True
                warnings.append("AI provider not configured; using Tavily fallback.")
                break
            ai_failed = False
            break
        except Exception as exc:  # noqa: BLE001
            ai_failed = True
            error_type = "timed out" if is_timeout_error(exc) else "failed"
            warnings.append(f"AI provider attempt {attempt + 1}/{max_retries + 1} {error_type}: {exc}")

    if ai_failed:
        try:
            tavily_result = tavily_search(
                cfg,
                args.query,
                max_sources=args.max_sources,
                detailed=detailed,
                include_domains=args.include_domain,
                exclude_domains=args.exclude_domain,
                recency_days=args.recency_days,
            )
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"Tavily failed: {exc}")

    answer_parts: list[str] = []
    if ai_result and ai_result.get("answer"):
        answer_parts.append(str(ai_result["answer"]).strip())
    if tavily_result and tavily_result.get("answer"):
        answer_parts.append(str(tavily_result["answer"]).strip())
    answer = "\n\n".join(part for part in answer_parts if part) or "No answer text returned."

    sources: list[dict[str, Any]] = []
    seen: set[str] = set()
    if ai_result:
        for url in ai_result.get("urls", []):
            if url not in seen:
                seen.add(url)
                sources.append({"title": url, "url": url, "provider": "ai"})
    if tavily_result:
        for item in tavily_result.get("sources", []):
            url = item.get("url")
            if url and url not in seen:
                seen.add(url)
                sources.append(item)

    session_payload = {
        "query": args.query,
        "created_at": int(time.time()),
        "answer": answer,
        "sources": sources,
        "warnings": warnings,
    }
    session_id = save_session(cfg, session_payload)
    answer, truncated = clamp_text(answer, max_chars)
    output = {
        "session_id": session_id,
        "query": args.query,
        "answer": answer,
        "sources_count": len(sources),
        "sources": sources if detailed else [{k: v for k, v in s.items() if k != "content"} for s in sources],
        "truncated": truncated,
        "warnings": warnings,
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))


def github_fetch(url: str, cfg: Config) -> str | None:
    match = re.search(r"github\.com/([^/]+)/([^/]+)/(issues|pull)/(\d+)", url)
    if not match:
        return None
    owner, repo, kind, number = match.groups()
    api = f"https://api.github.com/repos/{owner}/{repo}/issues/{number}"
    headers = {"Accept": "application/vnd.github+json"}
    token = cfg.get("GITHUB_TOKEN")
    if token:
        headers.update(bearer_headers(token))
    issue = request_json("GET", api, headers=headers, timeout=cfg.timeout)
    labels = ", ".join(label.get("name", "") for label in issue.get("labels", []) if isinstance(label, dict))
    lines = [
        f"# {issue.get('title', url)}",
        "",
        f"- Type: {kind}",
        f"- State: {issue.get('state', '')}",
        f"- Author: {(issue.get('user') or {}).get('login', '')}",
        f"- Labels: {labels}",
        f"- URL: {url}",
        "",
        issue.get("body") or "",
    ]
    comments_url = issue.get("comments_url")
    if comments_url:
        comments = request_json("GET", comments_url, headers=headers, timeout=cfg.timeout)
        if isinstance(comments.get("value"), list):
            comment_items = comments["value"]
        elif isinstance(comments, list):
            comment_items = comments
        else:
            comment_items = []
        for comment in comment_items[:10]:
            if isinstance(comment, dict):
                author = (comment.get("user") or {}).get("login", "")
                lines.extend(["", f"## Comment by {author}", "", comment.get("body") or ""])
    return "\n".join(lines).strip()


def stackexchange_site(host: str) -> str | None:
    mapping = {
        "stackoverflow.com": "stackoverflow",
        "superuser.com": "superuser",
        "serverfault.com": "serverfault",
        "mathoverflow.net": "mathoverflow",
        "askubuntu.com": "askubuntu",
    }
    if host in mapping:
        return mapping[host]
    if host.endswith(".stackexchange.com"):
        return host.split(".", 1)[0]
    return None


def stackexchange_fetch(url: str, cfg: Config) -> str | None:
    parsed = urllib.parse.urlparse(url)
    site = stackexchange_site(parsed.netloc.lower())
    match = re.search(r"/questions/(\d+)", parsed.path)
    if not site or not match:
        return None
    question_id = match.group(1)
    base = "https://api.stackexchange.com/2.3"
    q_url = f"{base}/questions/{question_id}?site={site}&filter=withbody"
    a_url = f"{base}/questions/{question_id}/answers?site={site}&filter=withbody&sort=votes&order=desc&pagesize=5"
    question = request_json("GET", q_url, timeout=cfg.timeout).get("items", [])
    answers = request_json("GET", a_url, timeout=cfg.timeout).get("items", [])
    if not question:
        return None
    q = question[0]
    lines = [
        f"# {strip_html(q.get('title', url))}",
        "",
        f"- Score: {q.get('score', '')}",
        f"- Answered: {q.get('is_answered', '')}",
        f"- URL: {url}",
        "",
        strip_html(q.get("body", "")),
    ]
    if isinstance(answers, list):
        for answer in answers:
            if not isinstance(answer, dict):
                continue
            accepted = " accepted" if answer.get("is_accepted") else ""
            lines.extend(["", f"## Answer score {answer.get('score', '')}{accepted}", "", strip_html(answer.get("body", ""))])
    return "\n".join(lines).strip()


def arxiv_fetch(url: str, cfg: Config) -> str | None:
    match = re.search(r"arxiv\.org/(?:abs|pdf)/([^/?#]+)", url)
    if not match:
        return None
    arxiv_id = match.group(1).removesuffix(".pdf")
    api = "https://export.arxiv.org/api/query?id_list=" + urllib.parse.quote(arxiv_id)
    text = request_text("GET", api, timeout=cfg.timeout)
    root = ET.fromstring(text)
    ns = {"atom": "http://www.w3.org/2005/Atom"}
    entry = root.find("atom:entry", ns)
    if entry is None:
        return None
    title = (entry.findtext("atom:title", default="", namespaces=ns) or "").strip()
    summary = (entry.findtext("atom:summary", default="", namespaces=ns) or "").strip()
    authors = [a.findtext("atom:name", default="", namespaces=ns) for a in entry.findall("atom:author", ns)]
    return "\n".join(
        [
            f"# {re.sub(r'\\s+', ' ', title)}",
            "",
            f"- ID: {arxiv_id}",
            f"- Authors: {', '.join(a for a in authors if a)}",
            f"- URL: {url}",
            "",
            re.sub(r"\s+", " ", summary),
        ]
    ).strip()


def wikipedia_fetch(url: str, cfg: Config) -> str | None:
    parsed = urllib.parse.urlparse(url)
    if "wikipedia.org" not in parsed.netloc or "/wiki/" not in parsed.path:
        return None
    title = urllib.parse.unquote(parsed.path.split("/wiki/", 1)[1])
    api = f"https://{parsed.netloc}/api/rest_v1/page/summary/{urllib.parse.quote(title)}"
    try:
        data = request_json("GET", api, timeout=cfg.timeout)
        extract = data.get("extract") or ""
        page_url = ((data.get("content_urls") or {}).get("desktop") or {}).get("page") or url
        return "\n".join([f"# {data.get('title', title)}", "", f"- URL: {page_url}", "", extract]).strip()
    except Exception:  # noqa: BLE001
        action = (
            f"https://{parsed.netloc}/w/api.php?action=query&prop=extracts&explaintext=1&format=json"
            f"&titles={urllib.parse.quote(title)}"
        )
        data = request_json("GET", action, timeout=cfg.timeout)
        pages = ((data.get("query") or {}).get("pages") or {}).values()
        for page in pages:
            return "\n".join([f"# {page.get('title', title)}", "", f"- URL: {url}", "", page.get("extract", "")]).strip()
    return None


def tavily_extract(url: str, cfg: Config) -> str | None:
    upstream = random_upstream(cfg, "tavily")
    if not upstream:
        return None
    key = upstream.get("tavily_api_key", "")
    if not key:
        return None
    endpoint = upstream.get("tavily_api_url", "https://api.tavily.com").rstrip("/") + "/extract"
    payload = {"urls": [url], "extract_depth": "advanced"}

    data = request_json(
        "POST",
        endpoint,
        headers=bearer_headers(key),
        payload=payload,
        timeout=cfg.timeout,
        allow_internal=True,
    )
    results = data.get("results") or []
    if isinstance(results, list) and results:
        first = results[0]
        if isinstance(first, dict):
            return first.get("raw_content") or first.get("content")
    return None


def firecrawl_extract(url: str, cfg: Config) -> str | None:
    upstream = random_upstream(cfg, "firecrawl")
    if not upstream:
        return None
    key = upstream.get("firecrawl_api_key", "")
    if not key:
        return None
    base = upstream.get("firecrawl_api_url", "https://api.firecrawl.dev").rstrip("/")
    endpoint = base if base.endswith("/scrape") else base + "/v1/scrape"
    data = request_json(
        "POST",
        endpoint,
        headers=bearer_headers(key),
        payload={"url": url, "formats": ["markdown"]},
        timeout=cfg.timeout,
        allow_internal=True,
    )
    payload = data.get("data") if isinstance(data.get("data"), dict) else data
    return payload.get("markdown") or payload.get("content")


def plain_fetch(url: str, cfg: Config, *, allow_internal: bool = False) -> str:
    text = request_text("GET", url, timeout=cfg.timeout, allow_internal=allow_internal)
    return strip_html(text)


def command_fetch(args: argparse.Namespace, cfg: Config) -> None:
    validate_web_url(args.url, allow_internal=cfg.allow_internal_fetch, timeout=cfg.timeout)
    max_chars = args.max_chars or cfg.fetch_max_chars or 0
    warnings: list[str] = []
    source_type = "generic"
    content: str | None = None
    use_external_extract = not cfg.allow_internal_fetch

    for name, fetcher in [
        ("github", github_fetch),
        ("stackexchange", stackexchange_fetch),
        ("arxiv", arxiv_fetch),
        ("wikipedia", wikipedia_fetch),
    ]:
        try:
            content = fetcher(args.url, cfg)
            if content:
                source_type = name
                break
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"{name} specialized fetch failed: {exc}")

    if content is None and use_external_extract:
        for name, fetcher in [("tavily", tavily_extract), ("firecrawl", firecrawl_extract)]:
            try:
                content = fetcher(args.url, cfg)
                if content:
                    source_type = name
                    break
            except Exception as exc:  # noqa: BLE001
                warnings.append(f"{name} fetch failed: {exc}")

    if content is None:
        content = plain_fetch(args.url, cfg, allow_internal=cfg.allow_internal_fetch)
        source_type = "plain-http"

    original_length = len(content)
    content, truncated = clamp_text(content, max_chars or None)
    print(
        json.dumps(
            {
                "url": args.url,
                "content": content,
                "original_length": original_length,
                "truncated": truncated,
                "source_type": source_type,
                "warnings": warnings,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


def command_sources(args: argparse.Namespace, cfg: Config) -> None:
    session = read_session(cfg, args.session_id)
    sources = session.get("sources", [])
    offset = max(args.offset, 0)
    limit = args.limit if args.limit and args.limit > 0 else len(sources)
    page = sources[offset : offset + limit]
    next_offset = offset + limit if offset + limit < len(sources) else None
    print(
        json.dumps(
            {
                "session_id": args.session_id,
                "query": session.get("query"),
                "sources": page,
                "sources_count": len(page),
                "total_sources": len(sources),
                "offset": offset,
                "next_offset": next_offset,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


def command_map(args: argparse.Namespace, cfg: Config) -> None:
    validate_web_url(args.url, allow_internal=cfg.allow_internal_fetch, timeout=cfg.timeout)
    upstream = random_upstream(cfg, "tavily")
    if not upstream or not upstream.get("tavily_api_key"):
        raise SystemExit("TAVILY_API_KEY is required for map")
    endpoint = upstream.get("tavily_api_url", "https://api.tavily.com").rstrip("/") + "/map"
    payload = {"url": args.url, "max_results": args.max_results}
    data = request_json(
        "POST",
        endpoint,
        headers=bearer_headers(upstream["tavily_api_key"]),
        payload=payload,
        timeout=cfg.timeout,
        allow_internal=True,
    )
    raw_results = data.get("results") or data.get("urls") or []
    urls: list[str] = []
    if isinstance(raw_results, list):
        for item in raw_results:
            if isinstance(item, str):
                urls.append(item)
            elif isinstance(item, dict) and item.get("url"):
                urls.append(str(item["url"]))
    print(json.dumps({"url": args.url, "urls": urls[: args.max_results], "count": len(urls[: args.max_results])}, indent=2))


def command_doctor(args: argparse.Namespace, cfg: Config) -> None:
    del args
    cache_dir = cfg.cache_dir
    checks: dict[str, Any] = {
        "config_file_checked": [str(path) for path in config_file_candidates()],
        "config_files": config_file_statuses(),
        "cache_dir": str(cache_dir),
        "cache_dir_exists": cache_dir.exists(),
        "grok_search_upstreams": complete_upstream_count(cfg, "grok_search"),
        "tavily_upstreams": complete_upstream_count(cfg, "tavily"),
        "firecrawl_upstreams": complete_upstream_count(cfg, "firecrawl"),
        "has_github_token": bool(cfg.get("GITHUB_TOKEN")),
        "environment": env_presence(),
    }
    probes: list[dict[str, Any]] = []
    grok = random_upstream(cfg, "grok_search")
    if grok and grok.get("grok_search_api_key"):
        try:
            api_url = normalize_v1_base(grok.get("grok_search_url", "https://api.x.ai"))
            probes.append({"name": "ai-provider", "api_url": api_url, "configured": True})
        except Exception as exc:  # noqa: BLE001
            probes.append({"name": "ai-provider", "configured": True, "error": str(exc)})
    tavily = random_upstream(cfg, "tavily")
    if tavily and tavily.get("tavily_api_key"):
        probes.append({"name": "tavily", "endpoint": tavily.get("tavily_api_url", "https://api.tavily.com"), "configured": True})
    firecrawl = random_upstream(cfg, "firecrawl")
    if firecrawl and firecrawl.get("firecrawl_api_key"):
        probes.append({"name": "firecrawl", "endpoint": firecrawl.get("firecrawl_api_url", "https://api.firecrawl.dev"), "configured": True})
    checks["probes"] = probes
    print(json.dumps(checks, ensure_ascii=False, indent=2))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Direct web research helper")
    sub = parser.add_subparsers(dest="command", required=True)

    def add_search(name: str, help_text: str | None) -> None:
        p = sub.add_parser(name, help=help_text)
        p.add_argument("--query", required=True)
        p.add_argument("--format", choices=["concise", "detailed"], default="concise")
        p.add_argument("--include-domain", action="append", default=[])
        p.add_argument("--exclude-domain", action="append", default=[])
        p.add_argument("--recency-days", type=int)
        p.add_argument("--max-sources", type=int, default=8)
        p.add_argument("--max-chars", type=int)
        p.add_argument("--grok-max-retries", type=int)
        p.set_defaults(func=command_search)

    def add_fetch(name: str, help_text: str | None) -> None:
        p = sub.add_parser(name, help=help_text)
        p.add_argument("--url", required=True)
        p.add_argument("--max-chars", type=int)
        p.set_defaults(func=command_fetch)

    def add_sources(name: str, help_text: str | None) -> None:
        p = sub.add_parser(name, help=help_text)
        p.add_argument("--session-id", required=True)
        p.add_argument("--offset", type=int, default=0)
        p.add_argument("--limit", type=int)
        p.set_defaults(func=command_sources)

    def add_map(name: str, help_text: str | None) -> None:
        p = sub.add_parser(name, help=help_text)
        p.add_argument("--url", required=True)
        p.add_argument("--max-results", type=int, default=20)
        p.set_defaults(func=command_map)

    add_search("web_search", "discover live sources")
    add_fetch("web_fetch", "fetch a known URL")
    add_sources("get_sources", "read cached search sources")
    add_map("web_map", "discover URLs under a site")

    p = sub.add_parser("doctor", help="show redacted configuration")
    p.set_defaults(func=command_doctor)

    return parser


def main(argv: list[str] | None = None) -> int:
    configure_stdio()
    cfg = Config(lower_keys(load_file_config()))
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        args.func(args, cfg)
    except HttpError as exc:
        eprint(f"HTTP {exc.status}: {exc}")
        return 2
    except KeyboardInterrupt:
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
