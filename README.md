# Web Search Skill

Self-contained Codex skill for live web search, URL fetching, source review, site URL discovery, and diagnostics through bundled code.

## Files

- `SKILL.md`: agent-facing workflow and routing rules.
- `scripts/websearch.py`: direct HTTP implementation.
- `references/configuration.md`: environment variables and optional config file.
- `references/tools-and-best-practices.md`: commands, options, and safety rules.
- `agents/openai.yaml`: UI metadata.

## Configuration

For persistent local config, copy `config.example.toml` to the platform user config path, then fill only the keys you need. Skill-local `config.toml` is still supported and ignored by git.

Configuration uses the first source in this order that contains any effective value. Sources are not merged together:

1. `%USERPROFILE%\.config\web-search-skill\config.toml`
2. `$HOME/.config/web-search-skill/config.toml`
3. Environment variables.
4. `config.toml` in this skill directory.
5. TOML file pointed to by `WEB_RESEARCH_CONFIG`

Important details:

- Python 3.11+ is required so TOML config is parsed by the standard `tomllib` parser. `scripts/websearch.py` uses only Python standard library modules; no `pip install` step is required.
- Save `config.toml` as standard UTF-8 without BOM. If a config file starts with a UTF-8 BOM, the script normalizes that file to UTF-8 without BOM before parsing.
- `config.toml` is supported for standard user config and skill-local config. `WEB_RESEARCH_CONFIG` may point to any `.toml` file as a fallback source.
- If a higher-priority config file sets provider priority but does not set upstreams, environment-variable upstreams are not mixed in. Put priority and upstream settings in the same source, or remove the higher-priority file so the next source can take effect.
- Prefer config files for runtime settings. CLI parameters are intended for task inputs and explicit one-off overrides; when a CLI parameter maps to a config value, the CLI value wins for that call.
- Agents should not add optional tuning flags by default, because that can silently override the user's configured behavior. Use config for retry counts, source limits, response budgets, timeouts, provider endpoints, cache paths, and similar settings unless the user explicitly asks for a different value on one command.
- Environment variables cannot express `*_UPSTREAMS` arrays. Use `config.toml` for multiple upstreams.
- Environment variables are useful as their own source for single-upstream keys (`GROK_SEARCH_API_KEY`, `GROK_SEARCH_URL`, `GROK_SEARCH_MODEL`, `TAVILY_API_KEY`, `TAVILY_API_URL`, `FIRECRAWL_API_KEY`, `FIRECRAWL_API_URL`) and scalar settings such as `GITHUB_TOKEN`, `GROK_SEARCH_TIMEOUT_SECONDS`, `GROK_SEARCH_MAX_RETRIES`, `SEARCH_PROVIDER_PRIORITY`, `FETCH_PROVIDER_PRIORITY`, `MAP_PROVIDER_PRIORITY`, `SEARCH_CACHE_DIR`, `GROK_SEARCH_FETCH_MAX_CHARS`, `GROK_SEARCH_ALLOW_INTERNAL_FETCH`, and `GROK_SEARCH_RESPONSE_MAX_CHARS`.
- Exa fallback uses the official remote MCP endpoint free plan without local Exa key config. DuckDuckGo fallback uses keyless DuckDuckGo HTML search, with Instant Answer used only as an error candidate when HTML search fails.
- `GROK_SEARCH_MAX_RETRIES` controls additional Grok `web_search` retries after the first failed attempt and is capped at `5`. Only network failures, timeouts, HTTP `408`, `429`, and `5xx` responses retry; permanent `4xx` responses fall back immediately. Default fallback order is Tavily, Exa, then DuckDuckGo, and provider priorities can be adjusted per command.
- Supported provider priority values are `grok,tavily,exa,duckduckgo,parallel` for `SEARCH_PROVIDER_PRIORITY`, `tavily,firecrawl,exa,plain` for `FETCH_PROVIDER_PRIORITY`, and `tavily,exa` for `MAP_PROVIDER_PRIORITY`; omitted providers are disabled when a priority list is configured, and an empty or all-invalid list disables every provider for that command.
- `web_search --mode` supports `general`, `news`, and `academic`. All modes use `SEARCH_PROVIDER_PRIORITY`; `news` also applies a default 7-day recency filter unless `--recency-days` is passed. `academic` is an explicit routing hint and does not reorder providers.
- `web_search --grok-max-retries` is a per-call override. If omitted, the merged config value is used.
- `WEB_RESEARCH_CONFIG` is a fallback config path in the current implementation. It does not override standard user config files, environment variables, or skill-local config, and non-`.toml` paths are ignored.
- The recommended place for persistent local secrets is the platform-appropriate user config path (`%USERPROFILE%\.config\web-search-skill\config.toml` on Windows, `$HOME/.config/web-search-skill/config.toml` on macOS/Linux).
- `GROK_SEARCH_*` upstreams are called through OpenAI-compatible `/v1/chat/completions`. `doctor` reports the normalized AI `api_url`, not a full request endpoint.
- Empty scalar values are treated as missing; leave optional scalar settings commented out when unused.
- `doctor` also reports `active_config_source`, `config_files` with each config path's priority/source/existence flag, plus `provider_priority` and `provider_enabled` so configured-but-disabled providers are visible.
- `GROK_SEARCH_ALLOW_INTERNAL_FETCH = true` allows `web_fetch` and `web_map` to target private/internal `http(s)` URLs. Default is `false`; configured provider endpoints can use private gateways regardless of this setting. Generic internal `web_fetch` still requires `plain` in `FETCH_PROVIDER_PRIORITY`.

Do not commit real keys.

Search sessions are cached under the system temp directory by default. Set `SEARCH_CACHE_DIR` only when you explicitly want a persistent cache.

```powershell
$env:FIRECRAWL_API_KEY = ""
$env:FIRECRAWL_API_URL = "https://api.firecrawl.dev"
$env:GROK_SEARCH_API_KEY = ""
$env:GROK_SEARCH_MODEL = "grok-4.3"
$env:GROK_SEARCH_URL = "https://api.x.ai"
$env:TAVILY_API_KEY = ""
$env:TAVILY_API_URL = "https://api.tavily.com"
```

User or skill-local `config.toml` example:

```toml
# Provider priorities are evaluated in order. Providers omitted from a priority list are disabled.
# SEARCH_PROVIDER_PRIORITY supports: "grok", "tavily", "exa", "duckduckgo", "parallel" (opt-in).
# FETCH_PROVIDER_PRIORITY supports: "tavily", "firecrawl", "exa", "plain".
# MAP_PROVIDER_PRIORITY supports: "tavily", "exa".
SEARCH_PROVIDER_PRIORITY = ["grok", "tavily", "exa", "duckduckgo"]
FETCH_PROVIDER_PRIORITY = ["tavily", "firecrawl", "exa", "plain"]
MAP_PROVIDER_PRIORITY = ["tavily", "exa"]

# Add more objects as needed. Only objects with all fields filled are used.
GROK_SEARCH_UPSTREAMS = [
  { GROK_SEARCH_API_KEY = "sk-123456", GROK_SEARCH_MODEL = "grok-4.3", GROK_SEARCH_URL = "https://api.x.ai" },
  { GROK_SEARCH_API_KEY = "", GROK_SEARCH_MODEL = "", GROK_SEARCH_URL = "" },
]

TAVILY_UPSTREAMS = [
  { TAVILY_API_KEY = "tvly-123456", TAVILY_API_URL = "https://api.tavily.com" },
  { TAVILY_API_KEY = "", TAVILY_API_URL = "" },
]

FIRECRAWL_UPSTREAMS = [
  { FIRECRAWL_API_KEY = "fc-123456", FIRECRAWL_API_URL = "https://api.firecrawl.dev" },
  { FIRECRAWL_API_KEY = "", FIRECRAWL_API_URL = "" },
]

# Exa fallback uses the official free-plan MCP endpoint without local key config.
# DuckDuckGo fallback uses keyless DuckDuckGo HTML search.

GROK_SEARCH_MAX_RETRIES = 2
GROK_SEARCH_ALLOW_INTERNAL_FETCH = false

# GITHUB_TOKEN = "ghp_..."
```

User and fallback config file locations:

- `%USERPROFILE%\.config\web-search-skill\config.toml`
- `$HOME/.config/web-search-skill/config.toml`
- TOML path from `WEB_RESEARCH_CONFIG` (must end with `.toml`)

## Optional Parallel search

[Parallel Search MCP](https://docs.parallel.ai/integrations/mcp/search-mcp) provides free web search in Fast mode without an account or API key, subject to free-tier rate limits. It uses the same bundled Python script and needs no extra packages.

To try it first, add `"parallel"` to `SEARCH_PROVIDER_PRIORITY` in your active config source while keeping any fallbacks you want:

```toml
SEARCH_PROVIDER_PRIORITY = ["parallel", "grok", "tavily", "exa", "duckduckgo"]
```

Then run from the skill directory root:

```bash
python scripts/websearch.py doctor
python scripts/websearch.py web_search --query "Python asyncio documentation" --format detailed
python scripts/websearch.py get_sources --session-id "<returned session_id>"
```

The default provider order stays unchanged. Parallel returns source URLs and excerpts, which are saved for `get_sources`. Domain filters use search operators and local result filtering. Positive `--recency-days` values, including the default 7-day window in news mode, skip Parallel with a warning and continue to the next configured provider. If no provider can return sources, the command reports an empty result and warnings. This integration supports search only; `FETCH_PROVIDER_PRIORITY` and `MAP_PROVIDER_PRIORITY` do not accept `parallel`.

## Usage

```bash
python scripts/websearch.py doctor
python scripts/websearch.py web_search --query "OpenAI Codex current docs" --format concise
python scripts/websearch.py web_search --query "AI safety policy" --mode news --format concise
python scripts/websearch.py web_fetch --url "https://example.com"
python scripts/websearch.py get_sources --session-id "<id>"
python scripts/websearch.py web_map --url "https://example.com" --max-results 20
```

`web_fetch` is for textual pages and documents. The skill workflow routes PDF, Office, EPUB, OpenDocument, and archive URLs to host-provided system download and document-reading tools instead, and does not use `web_fetch` as a binary downloader. Direct CLI callers remain responsible for following this rule.

The script uses only Python standard library modules, so running it does not require installing extra packages.
