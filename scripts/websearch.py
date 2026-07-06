#!/usr/bin/env python3
from __future__ import annotations

import argparse
import dataclasses
import html
import ipaddress
import json
import os
import random
import re
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover
    tomllib = None


USER_AGENT = "web-search-skill/1.0"
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
    "SEARCH_PROVIDER_PRIORITY",
    "FETCH_PROVIDER_PRIORITY",
    "MAP_PROVIDER_PRIORITY",
    "SEARCH_CACHE_DIR",
    "GROK_SEARCH_FETCH_MAX_CHARS",
    "GROK_SEARCH_ALLOW_INTERNAL_FETCH",
    "GROK_SEARCH_RESPONSE_MAX_CHARS",
]
APP_DIR_NAME = "web-search-skill"
EXA_MCP_URL = "https://mcp.exa.ai/mcp?tools=web_search_exa,web_fetch_exa,web_search_advanced_exa"
DUCKDUCKGO_HTML_URL = "https://html.duckduckgo.com/html"
DUCKDUCKGO_INSTANT_ANSWER_URL = "https://api.duckduckgo.com/"
MCP_PROTOCOL_VERSION = "2025-11-25"
HTTP_READ_CHUNK_BYTES = 64 * 1024
HTTP_RESPONSE_MAX_BYTES = 20 * 1024 * 1024
HTTP_ERROR_MAX_BYTES = 2000
UTF8_BOM = b"\xef\xbb\xbf"
DEFAULT_SEARCH_PROVIDER_PRIORITY = ["grok", "tavily", "exa", "duckduckgo"]
DEFAULT_FETCH_PROVIDER_PRIORITY = ["tavily", "firecrawl", "exa", "plain"]
DEFAULT_MAP_PROVIDER_PRIORITY = ["tavily", "exa"]
SEARCH_MODES = ("general", "news", "academic")
CONFIG_SOURCE_META_KEY = "__config_source"
PROVIDER_ALIASES = {
    "ai": "grok",
    "search": "grok",
    "grok-search": "grok",
    "plain-http": "plain",
}
PROVIDER_LABELS = {
    "grok": "Grok",
    "tavily": "Tavily",
    "firecrawl": "Firecrawl",
    "exa": "Exa",
    "duckduckgo": "DuckDuckGo",
    "plain": "plain HTTP",
}

UPSTREAM_DEFAULTS = {
    "grok_search": {
        "grok_search_url": "https://api.x.ai",
        "grok_search_model": "grok-4.3",
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


def non_blank_text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    return value if value.strip() else None


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
    return Path(tempfile.gettempdir()) / APP_DIR_NAME / "cache"


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
        paths.append(Path(userprofile) / ".config" / APP_DIR_NAME / "config.toml")
    home = os.environ.get("HOME")
    if home:
        paths.append(Path(home) / ".config" / APP_DIR_NAME / "config.toml")
    else:
        paths.append(Path.home() / ".config" / APP_DIR_NAME / "config.toml")
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


class ConfigError(RuntimeError):
    pass


def read_config_file(path: Path) -> dict[str, Any]:
    if tomllib is None:  # pragma: no cover - Python < 3.11 only
        raise ConfigError("Python 3.11+ is required to parse TOML configuration")
    try:
        raw = path.read_bytes()
        if raw.startswith(UTF8_BOM):
            try:
                raw = normalize_utf8_bom_config(path, raw)
            except ConfigError:
                # On-disk cleanup is best-effort; this load can still parse the normalized bytes.
                raw = raw[len(UTF8_BOM) :]
        text = raw.decode("utf-8")
        return tomllib.loads(text)
    except OSError as exc:
        raise ConfigError(f"failed to read config file {path}: {exc}") from exc
    except UnicodeDecodeError as exc:
        raise ConfigError(f"failed to decode config file {path} as UTF-8: {exc}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"failed to parse config file {path}: {exc}") from exc


def normalize_utf8_bom_config(path: Path, raw: bytes) -> bytes:
    normalized = raw[len(UTF8_BOM) :]
    try:
        with path.open("r+b") as config_file:
            # Rewrite in place so existing ACLs, owner, group, and DACL stay attached.
            config_file.write(normalized)
            config_file.truncate()
            config_file.flush()
            os.fsync(config_file.fileno())
    except OSError as exc:
        raise ConfigError(f"failed to normalize UTF-8 BOM in config file {path}: {exc}") from exc
    return normalized


def config_value_is_effective(key: str, value: Any) -> bool:
    if isinstance(value, str):
        return bool(value.strip())
    if key.endswith("_provider_priority") and isinstance(value, list):
        return True
    if key.endswith("_upstreams") and isinstance(value, list):
        provider = key[: -len("_upstreams")]
        return any(
            isinstance(raw, dict)
            and has_required_upstream_values(provider, lower_keys(raw))
            for raw in value
        )
    return True


def merge_missing(target: dict[str, Any], source: dict[str, Any]) -> None:
    for key, value in lower_keys(source).items():
        if not config_value_is_effective(key, value):
            continue
        target.setdefault(key, value)


def effective_config_values(source: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in lower_keys(source).items() if config_value_is_effective(key, value)}


def env_config_values() -> dict[str, str]:
    return {name.lower(): os.environ[name] for name in SUPPORTED_ENV_NAMES if name in os.environ}


def env_presence() -> dict[str, bool]:
    names = [*SUPPORTED_ENV_NAMES, "WEB_RESEARCH_CONFIG"]
    return {name: name in os.environ for name in names}


def load_file_config_with_source() -> tuple[dict[str, Any], dict[str, str]]:
    for path in user_config_candidates():
        if not path.exists():
            continue
        values = effective_config_values(read_config_file(path))
        if values:
            return values, {"source": "user", "path": str(path)}
    values = effective_config_values(env_config_values())
    if values:
        return values, {"source": "environment", "path": ""}
    for path in skill_config_candidates():
        if not path.exists():
            continue
        values = effective_config_values(read_config_file(path))
        if values:
            return values, {"source": "skill-local", "path": str(path)}
    for path in fallback_config_candidates():
        if not path.exists():
            continue
        values = effective_config_values(read_config_file(path))
        if values:
            return values, {"source": "fallback", "path": str(path)}
    return {}, {"source": "defaults", "path": ""}


def load_file_config() -> dict[str, Any]:
    values, source = load_file_config_with_source()
    if source["source"] != "defaults":
        values[CONFIG_SOURCE_META_KEY] = source
    return values


def lower_keys(data: dict[str, Any]) -> dict[str, Any]:
    return {str(k).lower(): v for k, v in data.items()}


def has_required_upstream_values(provider: str, upstream: dict[str, Any]) -> bool:
    required = UPSTREAM_REQUIRED_KEYS.get(provider)
    if not required:
        return False
    return all(str(upstream.get(key, "")).strip() for key in required)


def random_upstream(cfg: "Config", provider: str) -> dict[str, str] | None:
    table_key = f"{provider}_upstreams"
    raw_upstreams = cfg.file_values.get(table_key)
    upstreams: list[dict[str, str]] = []
    defaults = UPSTREAM_DEFAULTS.get(provider, {})

    if isinstance(raw_upstreams, list):
        for raw in raw_upstreams:
            if not isinstance(raw, dict):
                continue
            raw_item = lower_keys(raw)
            # Array entries must be complete before defaults are merged; defaults only support scalar fallback config.
            if has_required_upstream_values(provider, raw_item):
                item = {**defaults, **raw_item}
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
            if isinstance(raw, dict) and has_required_upstream_values(provider, lower_keys(raw))
        )
    return int(random_upstream(cfg, provider) is not None)


def provider_priority(cfg: "Config", env_name: str, default: list[str]) -> list[str]:
    raw = cfg.file_values.get(env_name.lower())
    if raw is None:
        return list(default)
    values: list[Any] = []
    if isinstance(raw, list):
        values = raw
    elif isinstance(raw, str):
        values = raw.split(",")
    elif raw is not None:
        values = [raw]

    allowed = set(default)
    result: list[str] = []
    for value in values:
        normalized = PROVIDER_ALIASES.get(str(value).strip().lower(), str(value).strip().lower())
        if normalized in allowed and normalized not in result:
            result.append(normalized)
    return result


def search_priority_for_mode(cfg: "Config", mode: str) -> list[str]:
    del mode
    return provider_priority(cfg, "SEARCH_PROVIDER_PRIORITY", DEFAULT_SEARCH_PROVIDER_PRIORITY)


def recency_days_for_mode(mode: str, recency_days: int | None) -> int | None:
    if recency_days is not None:
        return recency_days
    if mode == "news":
        return 7
    return None


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
        return max(self.get_int("GROK_SEARCH_MAX_RETRIES", 2), 0)

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


def validate_web_url(url: str, *, allow_internal: bool = False, timeout: float = 60) -> None:
    del timeout
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
        # Do not DNS-resolve hostnames here. Local proxy DNS such as Clash may
        # return reserved proxy IPs like 198.18.0.0/15 for public domains, and
        # external extract APIs cannot use the user's local DNS result anyway.
        # Only reject literal internal hosts/IPs from the URL itself.
        if host in {"localhost", "localhost.localdomain"}:
            raise HttpError(400, "internal URL targets are not allowed")
        if is_internal_address(host):
            raise HttpError(400, "internal URL targets are not allowed")


class PublicRedirectHandler(urllib.request.HTTPRedirectHandler):
    def __init__(self, timeout: float = 60, *, allow_internal: bool = False):
        self.timeout = timeout
        self.allow_internal = allow_internal

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D102
        validate_web_url(newurl, allow_internal=self.allow_internal, timeout=self.timeout)
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
        opener = urllib.request.build_opener(PublicRedirectHandler(timeout, allow_internal=allow_internal))
        with opener.open(req, timeout=timeout) as resp:
            return decode_body(read_response_bytes(resp), response_charset(resp))
    except urllib.error.HTTPError as exc:
        raise HttpError(exc.code, read_http_error_text(exc)) from exc
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


def decode_body(body: bytes, charset: str) -> str:
    try:
        return body.decode(charset, errors="replace")
    except LookupError:
        # Servers sometimes advertise non-standard charset labels; keep the CLI
        # path stable by falling back to UTF-8 with replacement.
        return body.decode("utf-8", errors="replace")


def response_charset(resp: Any) -> str:
    get_charset = getattr(getattr(resp, "headers", None), "get_content_charset", None)
    if callable(get_charset):
        return get_charset() or "utf-8"
    return "utf-8"


def read_response_bytes(resp: Any, max_bytes: int | None = None) -> bytes:
    max_bytes = HTTP_RESPONSE_MAX_BYTES if max_bytes is None else max_bytes
    if max_bytes <= 0:
        max_bytes = HTTP_RESPONSE_MAX_BYTES
    headers = getattr(resp, "headers", None)
    headers_get = getattr(headers, "get", None)
    content_length = headers_get("Content-Length") if callable(headers_get) else None
    if content_length:
        try:
            if int(content_length) > max_bytes:
                raise HttpError(413, f"response body exceeds {max_bytes} bytes")
        except ValueError:
            pass

    chunks: list[bytes] = []
    total = 0
    while True:
        sized_read = True
        try:
            chunk = resp.read(HTTP_READ_CHUNK_BYTES)
        except TypeError:
            sized_read = False
            chunk = resp.read()
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise HttpError(413, f"response body exceeds {max_bytes} bytes")
        chunks.append(chunk)
        if not sized_read:
            break
    return b"".join(chunks)


def read_http_error_text(exc: urllib.error.HTTPError) -> str:
    body = exc.read(HTTP_ERROR_MAX_BYTES + 1)
    text = body[:HTTP_ERROR_MAX_BYTES].decode("utf-8", errors="replace")
    if len(body) > HTTP_ERROR_MAX_BYTES:
        text += "\n[truncated]"
    return text


def parse_sse_json_messages(text: str) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    data_lines: list[str] = []

    def flush_data() -> None:
        if not data_lines:
            return
        raw = "\n".join(data_lines).strip()
        data_lines.clear()
        if not raw:
            return
        try:
            value = json.loads(raw)
        except json.JSONDecodeError:
            return
        if isinstance(value, dict):
            messages.append(value)

    for line in text.splitlines():
        if not line.strip():
            flush_data()
            continue
        if line.startswith("data:"):
            data_lines.append(line[5:].lstrip())
    flush_data()

    if not messages:
        try:
            value = json.loads(text)
        except json.JSONDecodeError:
            return []
        if isinstance(value, dict):
            messages.append(value)
    return messages


def first_jsonrpc_message(text: str, request_id: int) -> dict[str, Any]:
    for message in parse_sse_json_messages(text):
        if message.get("id") == request_id:
            return message
    messages = parse_sse_json_messages(text)
    if messages:
        return messages[0]
    raise HttpError(0, "MCP server returned no JSON-RPC message")


def exa_mcp_post(endpoint: str, payload: dict[str, Any], cfg: Config, session_id: str = "") -> tuple[str, str]:
    validate_web_url(endpoint, allow_internal=False, timeout=cfg.timeout)
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
        "MCP-Protocol-Version": MCP_PROTOCOL_VERSION,
    }
    if session_id:
        headers["Mcp-Session-Id"] = session_id
    req = urllib.request.Request(endpoint, data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST")
    try:
        opener = urllib.request.build_opener(PublicRedirectHandler(cfg.timeout, allow_internal=False))
        with opener.open(req, timeout=cfg.timeout) as resp:
            return resp.headers.get("Mcp-Session-Id") or "", decode_body(
                read_response_bytes(resp),
                response_charset(resp),
            )
    except urllib.error.HTTPError as exc:
        raise HttpError(exc.code, read_http_error_text(exc)) from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise HttpError(0, f"network failure: {exc}") from exc


def exa_mcp_tool_call(endpoint: str, tool_name: str, arguments: dict[str, Any], cfg: Config) -> str:
    # This is only the MCP transport wrapper; callers must handle each tool's
    # coverage semantics, such as Exa map using ranked search instead of crawl.
    session_id, init_text = exa_mcp_post(
        endpoint,
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": MCP_PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": APP_DIR_NAME, "version": "1.0"},
            },
        },
        cfg,
    )
    init_message = first_jsonrpc_message(init_text, 1)
    if init_message.get("error"):
        raise HttpError(0, json.dumps(init_message["error"], ensure_ascii=False))
    if not session_id:
        raise HttpError(0, "MCP server did not return a session id")
    exa_mcp_post(
        endpoint,
        {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}},
        cfg,
        session_id=session_id,
    )
    call_id = 2
    _, call_text = exa_mcp_post(
        endpoint,
        {
            "jsonrpc": "2.0",
            "id": call_id,
            "method": "tools/call",
            "params": {"name": tool_name, "arguments": arguments},
        },
        cfg,
        session_id=session_id,
    )
    message = first_jsonrpc_message(call_text, call_id)
    if message.get("error"):
        raise HttpError(0, json.dumps(message["error"], ensure_ascii=False))
    result = message.get("result")
    if not isinstance(result, dict):
        return ""
    content = result.get("content")
    parts: list[str] = []
    if isinstance(content, list):
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text" and isinstance(item.get("text"), str):
                parts.append(item["text"])
    text = "\n".join(part for part in parts if part)
    if result.get("isError"):
        raise HttpError(0, text or "Exa MCP tool returned an error")
    return text


def search_result_status(result: dict[str, Any] | None) -> str:
    if result is None:
        return "not configured"
    skip_reason = result.get("skip_reason")
    if isinstance(skip_reason, str) and skip_reason:
        return skip_reason
    if result.get("answer") or result.get("sources") or result.get("urls"):
        return ""
    return "returned no usable results"


def fallback_warning(provider: str, status: str, next_provider: str = "") -> str:
    label = PROVIDER_LABELS.get(provider, provider)
    if next_provider:
        next_label = PROVIDER_LABELS.get(next_provider, next_provider)
        return f"{label} {status}; using {next_label} fallback."
    return f"{label} {status}."


def clean_found_url(url: str) -> str:
    clean = url.strip()
    for marker in ("\\n", "\\t", "\\", "`", "<"):
        clean = clean.split(marker, 1)[0]
    clean = clean.rstrip(".,;*]}")
    while clean.endswith(")") and clean.count("(") < clean.count(")"):
        clean = clean[:-1].rstrip(".,;*]}")
    return clean


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
            found.extend(re.findall(r"https?://[^\s\[\]>`\"']+", value))

    walk(obj)
    deduped: list[str] = []
    seen: set[str] = set()
    for url in found:
        clean = clean_found_url(url)
        if clean not in seen:
            seen.add(clean)
            deduped.append(clean)
    return deduped


def make_source(
    provider: str,
    url: str,
    *,
    title: Any = None,
    content: Any = "",
    score: Any = None,
    published_date: Any = None,
) -> dict[str, Any] | None:
    clean_url = clean_found_url(str(url or ""))
    if not clean_url:
        return None
    return {
        "title": str(title or clean_url),
        "url": clean_url,
        "content": str(content or ""),
        "score": score,
        "published_date": published_date,
        "provider": provider,
    }


def append_source(
    sources: list[dict[str, Any]],
    seen: set[str],
    provider: str,
    url: str,
    *,
    max_sources: int | None = None,
    title: Any = None,
    content: Any = "",
    score: Any = None,
    published_date: Any = None,
) -> bool:
    if max_sources is not None and len(sources) >= max_sources:
        return False
    source = make_source(
        provider,
        url,
        title=title,
        content=content,
        score=score,
        published_date=published_date,
    )
    if source is None or source["url"] in seen:
        return False
    seen.add(source["url"])
    sources.append(source)
    return True


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
    model = upstream.get("grok_search_model", UPSTREAM_DEFAULTS["grok_search"]["grok_search_model"])
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
    search_mode: str = "general",
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
    if search_mode == "news":
        payload["topic"] = "news"
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
    seen: set[str] = set()
    if isinstance(results, list):
        for item in results:
            if not isinstance(item, dict):
                continue
            append_source(
                sources,
                seen,
                "tavily",
                str(item.get("url") or ""),
                max_sources=max_sources,
                title=item.get("title"),
                content=item.get("raw_content") or item.get("content") or "",
                score=item.get("score"),
                published_date=item.get("published_date"),
            )
    return {
        "answer": data.get("answer") or "",
        "sources": sources,
        "provider": "tavily",
    }


def exa_sources_from_mcp_text(text: str) -> list[dict[str, Any]]:
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        data = None
    if isinstance(data, dict) and isinstance(data.get("results"), list):
        sources: list[dict[str, Any]] = []
        seen: set[str] = set()
        for item in data["results"]:
            if not isinstance(item, dict):
                continue
            content = ""
            if isinstance(item.get("text"), str):
                content = item["text"]
            elif isinstance(item.get("highlights"), list):
                content = "\n".join(part for part in item["highlights"] if isinstance(part, str))
            elif isinstance(item.get("summary"), str):
                content = item["summary"]
            url = str(item.get("url") or "")
            title = str(item.get("title") or url)
            block = "\n".join(part for part in [f"Title: {title}", f"URL: {url}", content] if part).strip()
            append_source(
                sources,
                seen,
                "exa",
                url,
                title=title,
                content=block,
                published_date=item.get("publishedDate"),
            )
        return sources

    sources: list[dict[str, Any]] = []
    seen: set[str] = set()

    current_title = ""
    current_url = ""
    current_lines: list[str] = []

    def flush_current() -> None:
        nonlocal current_title, current_url, current_lines
        if not current_url or current_url in seen:
            current_title = ""
            current_url = ""
            current_lines = []
            return
        block = "\n".join(current_lines).strip()
        published_match = re.search(r"(?m)^Published:\s*(.+)$", block)
        append_source(
            sources,
            seen,
            "exa",
            current_url,
            title=current_title or current_url,
            content=block,
            published_date=published_match.group(1).strip() if published_match else None,
        )
        current_title = ""
        current_url = ""
        current_lines = []

    for line in text.splitlines():
        title_match = re.match(r"^Title:\s*(.+)$", line)
        if title_match:
            flush_current()
            current_title = title_match.group(1).strip()
            current_lines = [line]
            continue
        if not current_lines:
            continue
        current_lines.append(line)
        if not current_url:
            url_match = re.match(r"^URL:\s*(\S+)", line)
            if url_match:
                current_url = clean_found_url(url_match.group(1))
    flush_current()

    if sources:
        return sources
    for url in find_urls({"text": text}):
        append_source(sources, seen, "exa", url)
    return sources


def exa_search(
    cfg: Config,
    query: str,
    *,
    max_sources: int,
    detailed: bool,
    include_domains: list[str],
    exclude_domains: list[str],
    recency_days: int | None,
    search_mode: str = "general",
) -> dict[str, Any] | None:
    del search_mode
    tool_name = "web_search_exa"
    payload: dict[str, Any] = {"query": query, "numResults": max_sources}
    use_advanced = detailed or bool(include_domains or exclude_domains or recency_days)
    if use_advanced:
        tool_name = "web_search_advanced_exa"
        payload["type"] = "auto"
        if detailed:
            payload["textMaxCharacters"] = 5000
        else:
            payload["enableHighlights"] = True
    if include_domains:
        payload["includeDomains"] = include_domains
    if exclude_domains:
        payload["excludeDomains"] = exclude_domains
    if recency_days:
        start = int(time.time()) - recency_days * 86400
        payload["startPublishedDate"] = time.strftime("%Y-%m-%d", time.gmtime(start))
    text = exa_mcp_tool_call(EXA_MCP_URL, tool_name, payload, cfg)
    sources = exa_sources_from_mcp_text(text)
    if text.lstrip().startswith("{") and sources:
        text = "\n\n---\n\n".join(str(item.get("content") or "") for item in sources if item.get("content"))
    return {
        "answer": text,
        "sources": sources,
        "provider": "exa",
    }


def duckduckgo_extract_result_url(href: str) -> str:
    value = html.unescape(str(href or "").strip())
    if not value:
        return ""
    absolute = urllib.parse.urljoin("https://duckduckgo.com", value)
    parsed = urllib.parse.urlparse(absolute)
    if parsed.netloc.endswith("duckduckgo.com") and parsed.path.startswith("/l/"):
        uddg = urllib.parse.parse_qs(parsed.query).get("uddg", [""])[0]
        if uddg:
            absolute = uddg
    return clean_found_url(absolute)


class DuckDuckGoHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.results: list[dict[str, str]] = []
        self._capture: tuple[str, int] | None = None
        self._capture_depth = 0
        self._parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_map = {key.lower(): value or "" for key, value in attrs}
        classes = set(attr_map.get("class", "").split())
        if "result__a" in classes:
            url = duckduckgo_extract_result_url(attr_map.get("href", ""))
            self.results.append({"title": "", "url": url, "content": ""})
            self._start_capture("title", len(self.results) - 1)
            return
        if "result__snippet" in classes and self.results:
            self._start_capture("content", len(self.results) - 1)
            return
        if self._capture is not None:
            self._capture_depth += 1

    def handle_data(self, data: str) -> None:
        if self._capture is not None:
            self._parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        del tag
        if self._capture is None:
            return
        self._capture_depth -= 1
        if self._capture_depth <= 0:
            self._finish_capture()

    def _start_capture(self, field: str, index: int) -> None:
        self._capture = (field, index)
        self._capture_depth = 1
        self._parts = []

    def _finish_capture(self) -> None:
        if self._capture is None:
            return
        field, index = self._capture
        text = re.sub(r"\s+", " ", "".join(self._parts)).strip()
        if 0 <= index < len(self.results):
            self.results[index][field] = text
        self._capture = None
        self._capture_depth = 0
        self._parts = []


def duckduckgo_time_filter(recency_days: int | None) -> str:
    if recency_days is None or recency_days <= 0:
        return ""
    if recency_days <= 1:
        return "d"
    if recency_days <= 7:
        return "w"
    if recency_days <= 31:
        return "m"
    return "y"


def host_matches_domain(host: str, domain: str) -> bool:
    normalized_host = host.lower().removeprefix("www.")
    normalized_domain = domain.lower().strip().removeprefix("www.")
    return normalized_host == normalized_domain or normalized_host.endswith("." + normalized_domain)


def url_matches_any_domain(url: str, domains: list[str]) -> bool:
    if not domains:
        return False
    host = urllib.parse.urlparse(url).hostname or ""
    return any(host_matches_domain(host, domain) for domain in domains if domain.strip())


def duckduckgo_query(query: str, include_domains: list[str]) -> str:
    domains = [domain.strip() for domain in include_domains if domain.strip()]
    if not domains:
        return query
    if len(domains) == 1:
        return f"site:{domains[0]} {query}"
    sites = " OR ".join(f"site:{domain}" for domain in domains)
    return f"({sites}) {query}"


def duckduckgo_parse_html_sources(
    text: str,
    *,
    max_sources: int,
    include_domains: list[str],
    exclude_domains: list[str],
) -> list[dict[str, Any]]:
    parser = DuckDuckGoHTMLParser()
    parser.feed(text)
    parser.close()
    sources: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in parser.results:
        url = item.get("url", "")
        if not url.startswith(("http://", "https://")):
            continue
        if include_domains and not url_matches_any_domain(url, include_domains):
            continue
        if exclude_domains and url_matches_any_domain(url, exclude_domains):
            continue
        append_source(
            sources,
            seen,
            "duckduckgo",
            url,
            max_sources=max_sources,
            title=item.get("title") or url,
            content=item.get("content") or "",
        )
    return sources


def duckduckgo_related_topics(items: Any) -> list[dict[str, str]]:
    topics: list[dict[str, str]] = []
    if not isinstance(items, list):
        return topics
    for item in items:
        if not isinstance(item, dict):
            continue
        text = non_blank_text(item.get("Text"))
        url = non_blank_text(item.get("FirstURL"))
        if text and url:
            topics.append({"title": text.split(" - ", 1)[0], "url": url, "content": text})
        topics.extend(duckduckgo_related_topics(item.get("Topics")))
    return topics


def duckduckgo_instant_answer_search(
    cfg: Config,
    query: str,
    *,
    max_sources: int,
    include_domains: list[str],
    exclude_domains: list[str],
) -> dict[str, Any] | None:
    params = urllib.parse.urlencode(
        {
            "q": query,
            "format": "json",
            "no_html": "1",
            "skip_disambig": "1",
            "t": APP_DIR_NAME,
        }
    )
    data = request_json(
        "GET",
        f"{DUCKDUCKGO_INSTANT_ANSWER_URL}?{params}",
        headers={"User-Agent": USER_AGENT},
        payload=None,
        timeout=cfg.timeout,
        allow_internal=False,
    )

    answer = (
        non_blank_text(data.get("AbstractText"))
        or non_blank_text(data.get("Answer"))
        or non_blank_text(data.get("Definition"))
        or ""
    )
    sources: list[dict[str, Any]] = []
    seen: set[str] = set()
    # Tie Instant Answer text to its source so domain filters cannot leave stale answers.
    answer_source_urls: set[str] = set()

    abstract_url = non_blank_text(data.get("AbstractURL"))
    if abstract_url and answer:
        if append_source(
            sources,
            seen,
            "duckduckgo",
            abstract_url,
            max_sources=max_sources,
            title=non_blank_text(data.get("Heading")) or abstract_url,
            content=answer,
        ):
            answer_source_urls.add(str(sources[-1].get("url", "")))

    definition_url = non_blank_text(data.get("DefinitionURL"))
    definition = non_blank_text(data.get("Definition"))
    if definition_url and definition:
        if append_source(
            sources,
            seen,
            "duckduckgo",
            definition_url,
            max_sources=max_sources,
            title=non_blank_text(data.get("Heading")) or definition_url,
            content=definition,
        ) and definition == answer:
            answer_source_urls.add(str(sources[-1].get("url", "")))

    for item in duckduckgo_related_topics(data.get("RelatedTopics")):
        url = item["url"]
        if include_domains and not url_matches_any_domain(url, include_domains):
            continue
        if exclude_domains and url_matches_any_domain(url, exclude_domains):
            continue
        append_source(
            sources,
            seen,
            "duckduckgo",
            url,
            max_sources=max_sources,
            title=item["title"],
            content=item["content"],
        )

    if include_domains or exclude_domains:
        sources = [
            source
            for source in sources
            if (not include_domains or url_matches_any_domain(str(source.get("url", "")), include_domains))
            and (not exclude_domains or not url_matches_any_domain(str(source.get("url", "")), exclude_domains))
        ]
        filtered_urls = {str(source.get("url", "")) for source in sources}
        if not sources or not (answer_source_urls & filtered_urls):
            answer = ""

    return {
        "answer": answer,
        "sources": sources,
        "provider": "duckduckgo",
    }


def duckduckgo_search(
    cfg: Config,
    query: str,
    *,
    max_sources: int,
    detailed: bool,
    include_domains: list[str],
    exclude_domains: list[str],
    recency_days: int | None,
    search_mode: str = "general",
) -> dict[str, Any] | None:
    del detailed, search_mode
    params = {
        "q": duckduckgo_query(query, include_domains),
        "kl": "us-en",
        "p": "-1",
    }
    time_filter = duckduckgo_time_filter(recency_days)
    if time_filter:
        params["df"] = time_filter
    html_error = ""
    try:
        text = request_text(
            "GET",
            f"{DUCKDUCKGO_HTML_URL}?{urllib.parse.urlencode(params)}",
            headers={"Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"},
            timeout=cfg.timeout,
            allow_internal=False,
        )
        sources = duckduckgo_parse_html_sources(
            text,
            max_sources=max_sources,
            include_domains=include_domains,
            exclude_domains=exclude_domains,
        )
        if sources:
            return {
                "answer": "",
                "sources": sources,
                "provider": "duckduckgo",
            }
    except Exception as exc:  # noqa: BLE001
        html_error = str(exc)

    # Instant Answer is only an error candidate. It cannot honor recency, so do
    # not silently return it for freshness-sensitive searches.
    if recency_days is None or recency_days <= 0:
        try:
            result = duckduckgo_instant_answer_search(
                cfg,
                query,
                max_sources=max_sources,
                include_domains=include_domains,
                exclude_domains=exclude_domains,
            )
            if result and search_result_status(result) == "":
                return result
        except Exception as exc:  # noqa: BLE001
            if html_error:
                html_error = f"{html_error}; Instant Answer failed: {exc}"
            else:
                html_error = f"Instant Answer failed: {exc}"

    if html_error:
        return {
            "answer": "",
            "sources": [],
            "provider": "duckduckgo",
            "skip_reason": f"html search failed: {html_error}",
        }
    return {
        "answer": "",
        "sources": [],
        "provider": "duckduckgo",
    }


def save_session(cfg: Config, payload: dict[str, Any]) -> str:
    session_id = uuid.uuid4().hex[:16]
    cfg.cache_dir.mkdir(parents=True, exist_ok=True)
    path = cfg.cache_dir / f"{session_id}.json"
    temp_path = cfg.cache_dir / f".{session_id}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
    try:
        temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temp_path, path)
    finally:
        try:
            temp_path.unlink()
        except FileNotFoundError:
            pass
    return session_id


def read_session(cfg: Config, session_id: str) -> dict[str, Any]:
    if not re.fullmatch(r"[a-fA-F0-9]{16,32}", session_id):
        raise SystemExit("invalid session id")
    path = cfg.cache_dir / f"{session_id}.json"
    if not path.exists():
        raise SystemExit(f"session not found: {session_id}")
    return json.loads(path.read_text(encoding="utf-8"))


def command_search(args: argparse.Namespace, cfg: Config) -> None:
    mode = getattr(args, "mode", "general")
    detailed = args.format == "detailed"
    max_chars = args.max_chars or cfg.response_max_chars
    max_retries = args.grok_max_retries if args.grok_max_retries is not None else cfg.grok_search_max_retries
    max_retries = max(max_retries, 0)
    recency_days = recency_days_for_mode(mode, args.recency_days)
    warnings: list[str] = []
    selected_result: dict[str, Any] | None = None
    priority = search_priority_for_mode(cfg, mode)
    if not priority:
        warnings.append("No search provider is enabled.")

    searchers = {
        "tavily": tavily_search,
        "exa": exa_search,
        "duckduckgo": duckduckgo_search,
    }

    for index, provider in enumerate(priority):
        next_provider = priority[index + 1] if index + 1 < len(priority) else ""
        if provider == "grok":
            grok_status = "unavailable"
            for attempt in range(max_retries + 1):
                try:
                    result = ai_search(cfg, args.query)
                    grok_status = search_result_status(result)
                    if grok_status == "not configured":
                        warnings.append("AI provider not configured; using search fallbacks.")
                    elif grok_status:
                        warnings.append(fallback_warning(provider, grok_status, next_provider))
                    break
                except Exception as exc:  # noqa: BLE001
                    error_type = "timed out" if is_timeout_error(exc) else "failed"
                    warnings.append(f"AI provider attempt {attempt + 1}/{max_retries + 1} {error_type}: {exc}")
            else:
                warnings.append(fallback_warning(provider, grok_status, next_provider))
            if not grok_status:
                selected_result = result
                break
            continue

        searcher = searchers.get(provider)
        if searcher is None:
            continue
        try:
            result = searcher(
                cfg,
                args.query,
                max_sources=args.max_sources,
                detailed=detailed,
                include_domains=args.include_domain,
                exclude_domains=args.exclude_domain,
                recency_days=recency_days,
                search_mode=mode,
            )
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"{PROVIDER_LABELS.get(provider, provider)} failed: {exc}")
            continue
        status = search_result_status(result)
        if not status:
            selected_result = result
            break
        warnings.append(fallback_warning(provider, status, next_provider))

    answer_parts: list[str] = []
    if selected_result and selected_result.get("answer"):
        answer_parts.append(str(selected_result["answer"]).strip())
    answer = "\n\n".join(part for part in answer_parts if part) or "No answer text returned."

    sources: list[dict[str, Any]] = []
    seen: set[str] = set()
    if selected_result:
        for url in selected_result.get("urls", []):
            if url not in seen:
                seen.add(url)
                sources.append({"title": url, "url": url, "provider": "ai"})
        for item in selected_result.get("sources", []):
            url = item.get("url") if isinstance(item, dict) else None
            if url and url not in seen:
                seen.add(url)
                sources.append(item)

    session_payload = {
        "query": args.query,
        "mode": mode,
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
        "mode": mode,
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
            return non_blank_text(first.get("raw_content")) or non_blank_text(first.get("content"))
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
    return non_blank_text(payload.get("markdown")) or non_blank_text(payload.get("content"))


def exa_extract(url: str, cfg: Config) -> str | None:
    return non_blank_text(exa_mcp_tool_call(EXA_MCP_URL, "web_fetch_exa", {"urls": [url]}, cfg))


def tavily_map(url: str, cfg: Config, max_results: int) -> list[str] | None:
    upstream = random_upstream(cfg, "tavily")
    if not upstream or not upstream.get("tavily_api_key"):
        return None
    endpoint = upstream.get("tavily_api_url", "https://api.tavily.com").rstrip("/") + "/map"
    payload = {"url": url, "max_results": max_results}
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
    return urls


def exa_map(url: str, cfg: Config, max_results: int) -> list[str] | None:
    """Return a best-effort URL map from Exa's ranked semantic search results."""
    parsed = urllib.parse.urlparse(url)
    # Exa MCP has no dedicated sitemap/crawl tool here; this is a best-effort
    # map fallback based on web_search_advanced_exa ranking, not exhaustive discovery.
    query = f"pages under {url}"
    payload: dict[str, Any] = {"query": query, "numResults": max_results}
    if parsed.hostname:
        payload["includeDomains"] = [parsed.hostname]
    text = exa_mcp_tool_call(EXA_MCP_URL, "web_search_advanced_exa", payload, cfg)
    urls = [item["url"] for item in exa_sources_from_mcp_text(text)]
    deduped: list[str] = []
    seen: set[str] = set()
    target_host = (parsed.hostname or "").lower()
    skipped_suffixes = (
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".webp",
        ".svg",
        ".ico",
        ".css",
        ".js",
        ".woff",
        ".woff2",
        ".ttf",
        ".map",
    )
    for item in urls:
        parsed_item = urllib.parse.urlparse(item)
        item_host = (parsed_item.hostname or "").lower()
        if target_host and item_host != target_host and not item_host.endswith("." + target_host):
            continue
        if parsed_item.path.lower().endswith(skipped_suffixes):
            continue
        if item not in seen:
            seen.add(item)
            deduped.append(item)
    return deduped


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
    fetch_priority = provider_priority(cfg, "FETCH_PROVIDER_PRIORITY", DEFAULT_FETCH_PROVIDER_PRIORITY)

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
        fetchers = {"tavily": tavily_extract, "firecrawl": firecrawl_extract, "exa": exa_extract}
        for name in fetch_priority:
            if name == "plain":
                content = plain_fetch(args.url, cfg, allow_internal=cfg.allow_internal_fetch)
                source_type = "plain-http"
                break
            fetcher = fetchers.get(name)
            if fetcher is None:
                continue
            try:
                content = fetcher(args.url, cfg)
                if content:
                    source_type = name
                    break
            except Exception as exc:  # noqa: BLE001
                warnings.append(f"{name} fetch failed: {exc}")

    if content is None:
        if not use_external_extract and "plain" in fetch_priority:
            content = plain_fetch(args.url, cfg, allow_internal=cfg.allow_internal_fetch)
            source_type = "plain-http"
        else:
            content = ""
            source_type = "none"
            warnings.append("No enabled fetch provider returned content.")

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
    warnings: list[str] = []
    urls: list[str] | None = None
    source_type = ""
    for name in provider_priority(cfg, "MAP_PROVIDER_PRIORITY", DEFAULT_MAP_PROVIDER_PRIORITY):
        try:
            if name == "tavily":
                urls = tavily_map(args.url, cfg, args.max_results)
                if urls is None:
                    warnings.append("Tavily map not configured.")
                    continue
            elif name == "exa":
                urls = exa_map(args.url, cfg, args.max_results)
            else:
                continue
            source_type = name
            if urls:
                break
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"{PROVIDER_LABELS.get(name, name)} map failed: {exc}")
    if urls is None:
        raise SystemExit("Map failed: " + ("; ".join(warnings) or "no map provider is configured"))
    page = urls[: args.max_results]
    print(json.dumps({"url": args.url, "urls": page, "count": len(page), "source_type": source_type, "warnings": warnings}, indent=2))


def command_doctor(args: argparse.Namespace, cfg: Config) -> None:
    del args
    cache_dir = cfg.cache_dir
    search_priority = provider_priority(cfg, "SEARCH_PROVIDER_PRIORITY", DEFAULT_SEARCH_PROVIDER_PRIORITY)
    fetch_priority = provider_priority(cfg, "FETCH_PROVIDER_PRIORITY", DEFAULT_FETCH_PROVIDER_PRIORITY)
    map_priority = provider_priority(cfg, "MAP_PROVIDER_PRIORITY", DEFAULT_MAP_PROVIDER_PRIORITY)
    provider_enabled = {
        "grok": "grok" in search_priority,
        "tavily": any("tavily" in priority for priority in (search_priority, fetch_priority, map_priority)),
        "firecrawl": "firecrawl" in fetch_priority,
        "exa": any("exa" in priority for priority in (search_priority, fetch_priority, map_priority)),
        "duckduckgo": "duckduckgo" in search_priority,
        "plain": "plain" in fetch_priority,
    }
    checks: dict[str, Any] = {
        "config_file_checked": [str(path) for path in config_file_candidates()],
        "config_files": config_file_statuses(),
        "active_config_source": cfg.file_values.get(CONFIG_SOURCE_META_KEY, {"source": "injected", "path": ""}),
        "cache_dir": str(cache_dir),
        "cache_dir_exists": cache_dir.exists(),
        "grok_search_upstreams": complete_upstream_count(cfg, "grok_search"),
        "tavily_upstreams": complete_upstream_count(cfg, "tavily"),
        "firecrawl_upstreams": complete_upstream_count(cfg, "firecrawl"),
        "provider_priority": {
            "search": search_priority,
            "fetch": fetch_priority,
            "map": map_priority,
        },
        "search_modes": {mode: search_priority_for_mode(cfg, mode) for mode in SEARCH_MODES},
        "provider_enabled": provider_enabled,
        "has_github_token": bool(cfg.get("GITHUB_TOKEN")),
        "environment": env_presence(),
    }
    probes: list[dict[str, Any]] = []
    grok = random_upstream(cfg, "grok_search")
    if grok and grok.get("grok_search_api_key"):
        try:
            api_url = normalize_v1_base(grok.get("grok_search_url", "https://api.x.ai"))
            probes.append({"name": "ai-provider", "api_url": api_url, "configured": True, "enabled": provider_enabled["grok"]})
        except Exception as exc:  # noqa: BLE001
            probes.append({"name": "ai-provider", "configured": True, "enabled": provider_enabled["grok"], "error": str(exc)})
    tavily = random_upstream(cfg, "tavily")
    if tavily and tavily.get("tavily_api_key"):
        probes.append(
            {
                "name": "tavily",
                "endpoint": tavily.get("tavily_api_url", "https://api.tavily.com"),
                "configured": True,
                "enabled": provider_enabled["tavily"],
            }
        )
    firecrawl = random_upstream(cfg, "firecrawl")
    if firecrawl and firecrawl.get("firecrawl_api_key"):
        probes.append(
            {
                "name": "firecrawl",
                "endpoint": firecrawl.get("firecrawl_api_url", "https://api.firecrawl.dev"),
                "configured": True,
                "enabled": provider_enabled["firecrawl"],
            }
        )
    probes.append(
        {
            "name": "exa-mcp",
            "endpoint": EXA_MCP_URL,
            "configured": True,
            "enabled": provider_enabled["exa"],
            "auth": "free-plan-no-key",
        }
    )
    probes.append(
        {
            "name": "duckduckgo-instant-answer",
            "endpoint": "https://api.duckduckgo.com/",
            "configured": True,
            "enabled": provider_enabled["duckduckgo"],
            "auth": "no-key",
        }
    )
    checks["probes"] = probes
    print(json.dumps(checks, ensure_ascii=False, indent=2))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Direct web research helper")
    sub = parser.add_subparsers(dest="command", required=True)

    def add_search(name: str, help_text: str | None) -> None:
        p = sub.add_parser(name, help=help_text)
        p.add_argument("--query", required=True)
        p.add_argument("--mode", choices=SEARCH_MODES, default="general")
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
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        cfg = Config(lower_keys(load_file_config()))
        args.func(args, cfg)
    except ConfigError as exc:
        eprint(f"Config error: {exc}")
        return 2
    except HttpError as exc:
        eprint(f"HTTP {exc.status}: {exc}")
        return 2
    except KeyboardInterrupt:
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
