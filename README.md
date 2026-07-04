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

- Python 3.11+ is required so TOML config is parsed by the standard `tomllib` parser.
- `config.toml` is supported for standard user config and skill-local config. `WEB_RESEARCH_CONFIG` may point to any `.toml` file as a fallback source.
- If a higher-priority config file sets provider priority but does not set upstreams, environment-variable upstreams are not mixed in. Put priority and upstream settings in the same source, or remove the higher-priority file so the next source can take effect.
- Prefer config files for runtime settings. CLI parameters are intended for task inputs and explicit one-off overrides; when a CLI parameter maps to a config value, the CLI value wins for that call.
- Agents should not add optional tuning flags by default, because that can silently override the user's configured behavior. Use config for retry counts, source limits, response budgets, timeouts, provider endpoints, cache paths, and similar settings unless the user explicitly asks for a different value on one command.
- Environment variables cannot express `*_UPSTREAMS` arrays. Use `config.toml` for multiple upstreams.
- Environment variables are useful as their own source for single-upstream keys (`GROK_SEARCH_API_KEY`, `GROK_SEARCH_URL`, `GROK_SEARCH_MODEL`, `TAVILY_API_KEY`, `TAVILY_API_URL`, `FIRECRAWL_API_KEY`, `FIRECRAWL_API_URL`) and scalar settings such as `GITHUB_TOKEN`, `GROK_SEARCH_TIMEOUT_SECONDS`, `GROK_SEARCH_MAX_RETRIES`, `SEARCH_PROVIDER_PRIORITY`, `FETCH_PROVIDER_PRIORITY`, `MAP_PROVIDER_PRIORITY`, `SEARCH_CACHE_DIR`, `GROK_SEARCH_FETCH_MAX_CHARS`, `GROK_SEARCH_ALLOW_INTERNAL_FETCH`, and `GROK_SEARCH_RESPONSE_MAX_CHARS`.
- Exa fallback uses the official remote MCP endpoint free plan without local Exa key config.
- `GROK_SEARCH_MAX_RETRIES` controls additional Grok `web_search` retries after the first failed attempt. Any Grok error triggers retry; default fallback order is Tavily then Exa, and provider priorities can be adjusted per command.
- Provider priority values are `grok,tavily,exa` for `SEARCH_PROVIDER_PRIORITY`, `tavily,firecrawl,exa,plain` for `FETCH_PROVIDER_PRIORITY`, and `tavily,exa` for `MAP_PROVIDER_PRIORITY`; omitted providers are disabled when a priority list is configured, and an empty or all-invalid list disables every provider for that command.
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
# SEARCH_PROVIDER_PRIORITY supports: "grok", "tavily", "exa".
# FETCH_PROVIDER_PRIORITY supports: "tavily", "firecrawl", "exa", "plain".
# MAP_PROVIDER_PRIORITY supports: "tavily", "exa".
SEARCH_PROVIDER_PRIORITY = ["grok", "tavily", "exa"]
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

GROK_SEARCH_MAX_RETRIES = 5
GROK_SEARCH_ALLOW_INTERNAL_FETCH = false

# GITHUB_TOKEN = "ghp_..."
```

User and fallback config file locations:

- `%USERPROFILE%\.config\web-search-skill\config.toml`
- `$HOME/.config/web-search-skill/config.toml`
- TOML path from `WEB_RESEARCH_CONFIG` (must end with `.toml`)

## Usage

```bash
python scripts/websearch.py doctor
python scripts/websearch.py web_search --query "OpenAI Codex current docs" --format concise
python scripts/websearch.py web_fetch --url "https://example.com"
python scripts/websearch.py get_sources --session-id "<id>"
python scripts/websearch.py web_map --url "https://example.com" --max-results 20
```

The script uses only Python standard library modules.
