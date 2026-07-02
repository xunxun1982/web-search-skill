# Configuration

The bundled script merges configuration per key by priority. Earlier sources win for keys they define:

1. `%USERPROFILE%\.config\grok-search-skill\config.toml`
2. `$HOME/.config/grok-search-skill/config.toml`
3. Environment variables
4. Skill directory `config.toml`
5. TOML file pointed to by `WEB_RESEARCH_CONFIG`

Notes:

- If two sources define the same key, the earlier source keeps its value and later sources are ignored for that key.
- Environment variables cannot express the `*_UPSTREAMS` arrays. Use `config.toml` for multiple upstream objects.
- Environment variables can still provide legacy single-upstream keys and scalar settings. Empty scalar values are treated as missing.
- `WEB_RESEARCH_CONFIG` is currently a lowest-priority fallback path, not a high-priority override. Its path must end with `.toml` or it is ignored.

## Config File

User config files are preferred so settings and secrets survive skill updates. Copy `config.example.toml` to the platform user config path or skill-local `config.toml`, then fill only the keys you need. Keep real key files untracked.

User and fallback locations:

- Windows: `%USERPROFILE%\.config\grok-search-skill\config.toml`
- macOS / Linux: `$HOME/.config/grok-search-skill/config.toml`
- Extra fallback TOML path: `WEB_RESEARCH_CONFIG` (must end with `.toml`)

TOML example:

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

GROK_SEARCH_TIMEOUT_SECONDS = 120
GROK_SEARCH_MAX_RETRIES = 5
GROK_SEARCH_FETCH_MAX_CHARS = 0
GROK_SEARCH_ALLOW_INTERNAL_FETCH = false
GROK_SEARCH_RESPONSE_MAX_CHARS = 60000

# Optional non-empty scalar. Leave commented out when unused.
# GITHUB_TOKEN = "ghp_..."
```

## Environment Variables

Environment variables are intentionally limited to scalar values. They cannot define `GROK_SEARCH_UPSTREAMS`, `TAVILY_UPSTREAMS`, or `FIRECRAWL_UPSTREAMS`. For multiple upstreams, use `config.toml`.

| Variable | Purpose |
|---|---|
| `GROK_SEARCH_API_KEY` | Legacy single Grok-compatible upstream key. Used only when `GROK_SEARCH_UPSTREAMS` is not configured by an earlier file source. |
| `GROK_SEARCH_URL` | Legacy single Grok-compatible upstream URL. Default: `https://api.x.ai`. |
| `GROK_SEARCH_MODEL` | Legacy single Grok-compatible upstream model. Default: `grok-4.20-fast`. |
| `TAVILY_API_KEY` | Legacy single Tavily upstream key. Used only when `TAVILY_UPSTREAMS` is not configured by an earlier file source. |
| `TAVILY_API_URL` | Legacy single Tavily upstream URL. Default: `https://api.tavily.com`. |
| `FIRECRAWL_API_KEY` | Legacy single Firecrawl upstream key. Used only when `FIRECRAWL_UPSTREAMS` is not configured by an earlier file source. |
| `FIRECRAWL_API_URL` | Legacy single Firecrawl upstream URL. Default: `https://api.firecrawl.dev`. |
| `GITHUB_TOKEN` | Optional token for higher GitHub issue/PR fetch limits and private repositories. |
| `GROK_SEARCH_TIMEOUT_SECONDS` | HTTP timeout. Default: `120`. |
| `GROK_SEARCH_MAX_RETRIES` | Additional Grok search retries after the first failed attempt. Any Grok error triggers retry until this count is exhausted, then `web_search` falls back to Tavily when configured. Default: `5`. |
| `SEARCH_CACHE_DIR` | Optional search session cache directory. Default uses the system temp directory. |
| `GROK_SEARCH_FETCH_MAX_CHARS` | Default `fetch` character cap. |
| `GROK_SEARCH_ALLOW_INTERNAL_FETCH` | Allows `web_fetch` and `web_map` to target private/internal `http(s)` URLs. Default: `false`. Provider endpoints are explicit config and can use private gateways independently. |
| `GROK_SEARCH_RESPONSE_MAX_CHARS` | Default `search` response budget. |

## Provider Selection

- With `GROK_SEARCH_UPSTREAMS`, `search` randomly selects one Grok/OpenAI-compatible upstream object for the AI answer and calls `/v1/chat/completions`.
- `web_search --grok-max-retries` overrides `GROK_SEARCH_MAX_RETRIES` for that call. When the flag is omitted, the merged config value is used.
- With `TAVILY_UPSTREAMS`, timed-out search fallback, generic fetch, and map randomly select one Tavily upstream object.
- With `FIRECRAWL_UPSTREAMS`, generic `fetch` fallback randomly selects one Firecrawl upstream object.
- Legacy single-value `GROK_SEARCH_*`, `TAVILY_*`, and `FIRECRAWL_*` keys still work as fallback when no upstream table is configured.
- Empty or partially filled upstream objects are ignored.
- With no provider keys, specialized public fetchers still work for GitHub, StackExchange, arXiv, and Wikipedia URLs.

## Safety

- Keep real secrets outside the repository.
- `doctor` redacts configuration and reports config paths checked, each config path's `exists` flag, cache directory existence, environment variable presence, normalized AI `api_url`, provider endpoints, and upstream counts.
- Do not paste complete local config or shell environment into chat output.
