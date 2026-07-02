# Web Research Direct Skill

Self-contained Codex skill for live search, URL fetching, source review, site URL discovery, and diagnostics through bundled code.

## Files

- `SKILL.md`：agent-facing workflow and routing rules.
- `scripts/groksearch.py`：direct HTTP implementation.
- `references/configuration.md`：environment variables and optional config file.
- `references/tools-and-best-practices.md`：commands, options, and safety rules.
- `agents/openai.yaml`：UI metadata.

## Configuration

For persistent local config, copy `config.example.toml` to the platform user config path, then fill only the keys you need. Skill-local `config.toml` is still supported and ignored by git.

Configuration is merged per key in this order. Earlier sources win for keys they define:

1. `%USERPROFILE%\.config\grok-search-skill\config.toml`
2. `$HOME/.config/grok-search-skill/config.toml`
3. Environment variables.
4. `config.toml` in this skill directory.
5. TOML file pointed to by `WEB_RESEARCH_CONFIG`

Important details:

- Python 3.11+ is required so TOML config is parsed by the standard `tomllib` parser.
- `config.toml` is supported for standard user config and skill-local config. `WEB_RESEARCH_CONFIG` may point to any `.toml` file as a fallback source.
- Prefer config files for runtime settings. CLI parameters are intended for task inputs and explicit one-off overrides; when a CLI parameter maps to a config value, the CLI value wins for that call.
- Agents should not add optional tuning flags by default, because that can silently override the user's configured behavior. Use config for retry counts, source limits, response budgets, timeouts, provider endpoints, cache paths, and similar settings unless the user explicitly asks for a different value on one command.
- Environment variables cannot express `*_UPSTREAMS` arrays. Use `config.toml` for multiple upstreams.
- Environment variables are still useful for legacy single-upstream keys (`GROK_SEARCH_API_KEY`, `GROK_SEARCH_URL`, `GROK_SEARCH_MODEL`, `TAVILY_API_KEY`, `TAVILY_API_URL`, `FIRECRAWL_API_KEY`, `FIRECRAWL_API_URL`) and scalar settings such as `GITHUB_TOKEN`, `GROK_SEARCH_TIMEOUT_SECONDS`, `GROK_SEARCH_MAX_RETRIES`, `SEARCH_CACHE_DIR`, `GROK_SEARCH_FETCH_MAX_CHARS`, `GROK_SEARCH_ALLOW_INTERNAL_FETCH`, and `GROK_SEARCH_RESPONSE_MAX_CHARS`.
- `GROK_SEARCH_MAX_RETRIES` controls additional Grok `web_search` retries after the first failed attempt. Any Grok error triggers retry; after retries are exhausted, Tavily is used as fallback when configured.
- `web_search --grok-max-retries` is a per-call override. If omitted, the merged config value is used.
- `WEB_RESEARCH_CONFIG` is a fallback config path in the current implementation. It does not override standard user config files, environment variables, or skill-local config.
- The recommended place for persistent local secrets is the platform-appropriate user config path (`%USERPROFILE%\.config\grok-search-skill\config.toml` on Windows, `$HOME/.config/grok-search-skill/config.toml` on macOS/Linux); this survives skill updates better than a skill-local `config.toml`.
- `GROK_SEARCH_*` upstreams are called through OpenAI-compatible `/v1/chat/completions`. `doctor` reports the normalized AI `api_url`, not a full request endpoint.
- `doctor` also reports `config_files` with each config path's priority, source, and existence flag.
- `GROK_SEARCH_ALLOW_INTERNAL_FETCH = true` allows `web_fetch` and `web_map` to target private/internal `http(s)` URLs. Default is `false`; configured provider endpoints can use private gateways regardless of this setting.

Do not commit real keys.

Search sessions are cached under the system temp directory by default. Set `SEARCH_CACHE_DIR` only when you explicitly want a persistent cache.

```powershell
$env:FIRECRAWL_API_KEY = ""
$env:FIRECRAWL_API_URL = "https://api.firecrawl.dev"
$env:GROK_SEARCH_API_KEY = ""
$env:GROK_SEARCH_MODEL = "grok-4.20-fast"
$env:GROK_SEARCH_URL = "https://api.x.ai"
$env:TAVILY_API_KEY = ""
$env:TAVILY_API_URL = "https://api.tavily.com"
```

User or skill-local `config.toml` example:

```toml
# Add more objects as needed. Only objects with all fields filled are used.

FIRECRAWL_UPSTREAMS = [
  { FIRECRAWL_API_KEY = "fc-123456", FIRECRAWL_API_URL = "https://api.firecrawl.dev" },
  { FIRECRAWL_API_KEY = "", FIRECRAWL_API_URL = "" },
]

GROK_SEARCH_UPSTREAMS = [
  { GROK_SEARCH_API_KEY = "sk-123456", GROK_SEARCH_MODEL = "grok-4.20-fast", GROK_SEARCH_URL = "https://api.x.ai" },
  { GROK_SEARCH_API_KEY = "", GROK_SEARCH_MODEL = "", GROK_SEARCH_URL = "" },
]

TAVILY_UPSTREAMS = [
  { TAVILY_API_KEY = "tvly-123456", TAVILY_API_URL = "https://api.tavily.com" },
  { TAVILY_API_KEY = "", TAVILY_API_URL = "" },
]

GITHUB_TOKEN = ""
GROK_SEARCH_MAX_RETRIES = 5
GROK_SEARCH_ALLOW_INTERNAL_FETCH = false
```

User and fallback config file locations:

- `%USERPROFILE%\.config\grok-search-skill\config.toml`
- `$HOME/.config/grok-search-skill/config.toml`
- TOML path from `WEB_RESEARCH_CONFIG`

## Usage

```bash
python scripts/groksearch.py doctor
python scripts/groksearch.py web_search --query "OpenAI Codex current docs" --format concise
python scripts/groksearch.py web_fetch --url "https://example.com"
python scripts/groksearch.py get_sources --session-id "<id>"
python scripts/groksearch.py web_map --url "https://example.com" --max-results 20
```

The script uses only Python standard library modules.
