# Configuration

The bundled script uses the first source in this priority order that contains any effective value. Sources are not merged together:

1. `%USERPROFILE%\.config\web-search-skill\config.toml`
2. `$HOME/.config/web-search-skill/config.toml`
3. Environment variables
4. Skill directory `config.toml`
5. TOML file pointed to by `WEB_RESEARCH_CONFIG`

Notes:

- If a higher-priority source contains any effective value, later sources are ignored entirely.
- If a higher-priority config file sets provider priority but omits upstreams, environment-variable upstreams are not mixed in. Put priority and upstream settings in the same source, or remove the higher-priority file so the next source can take effect.
- Environment variables cannot express the `*_UPSTREAMS` arrays. Use `config.toml` for multiple upstream objects.
- Environment variables can provide single-upstream scalar keys and scalar runtime settings as their own source. Empty scalar values are treated as missing.
- `WEB_RESEARCH_CONFIG` is currently a lowest-priority fallback path, not a high-priority override. Its path must end with `.toml` or it is ignored.

## Config File

User config files are preferred so settings and secrets survive skill updates. Copy `config.example.toml` to the platform user config path or skill-local `config.toml`, then fill only the keys you need. Keep real key files untracked.

User and fallback locations:

- Windows: `%USERPROFILE%\.config\web-search-skill\config.toml`
- macOS / Linux: `$HOME/.config/web-search-skill/config.toml`
- Extra fallback TOML path: `WEB_RESEARCH_CONFIG` (must end with `.toml`)

TOML example:

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

GROK_SEARCH_TIMEOUT_SECONDS = 120
GROK_SEARCH_MAX_RETRIES = 5
GROK_SEARCH_FETCH_MAX_CHARS = 0
GROK_SEARCH_ALLOW_INTERNAL_FETCH = false
GROK_SEARCH_RESPONSE_MAX_CHARS = 60000

# Optional non-empty scalar. Leave commented out when unused.
# GITHUB_TOKEN = "ghp_..."
```

## Environment Variables

Environment variables are intentionally limited to scalar values. They cannot define `GROK_SEARCH_UPSTREAMS`, `TAVILY_UPSTREAMS`, or `FIRECRAWL_UPSTREAMS`. For multiple upstreams, use `config.toml`. Environment variables are used only when no higher-priority config file contains effective settings.

| Variable | Purpose |
|---|---|
| `GROK_SEARCH_API_KEY` | Single Grok/OpenAI-compatible upstream key. Used only when `GROK_SEARCH_UPSTREAMS` is not configured by an earlier file source. |
| `GROK_SEARCH_URL` | Single Grok/OpenAI-compatible upstream URL. Default: `https://api.x.ai`. |
| `GROK_SEARCH_MODEL` | Single Grok/OpenAI-compatible upstream model. Default: `grok-4.3`. |
| `TAVILY_API_KEY` | Single Tavily upstream key. Used only when `TAVILY_UPSTREAMS` is not configured by an earlier file source. |
| `TAVILY_API_URL` | Single Tavily upstream URL. Default: `https://api.tavily.com`. |
| `FIRECRAWL_API_KEY` | Single Firecrawl upstream key. Used only when `FIRECRAWL_UPSTREAMS` is not configured by an earlier file source. |
| `FIRECRAWL_API_URL` | Single Firecrawl upstream URL. Default: `https://api.firecrawl.dev`. |
| `GITHUB_TOKEN` | Optional token for higher GitHub issue/PR fetch limits and private repositories. |
| `GROK_SEARCH_TIMEOUT_SECONDS` | HTTP timeout. Default: `120`. |
| `GROK_SEARCH_MAX_RETRIES` | Additional Grok search retries after the first failed attempt. Any Grok error triggers retry until this count is exhausted, then `web_search` falls back to later enabled providers. Default: `5`. |
| `SEARCH_PROVIDER_PRIORITY` | `web_search` provider priority. Supported values: `grok`, `tavily`, `exa`. Config files may use a TOML array; environment variables may use comma-separated values. Default: `grok,tavily,exa`. Omitted providers are disabled when this is configured. |
| `FETCH_PROVIDER_PRIORITY` | Generic `web_fetch` provider priority after specialized fetchers. Supported values: `tavily`, `firecrawl`, `exa`, `plain`. Config files may use a TOML array; environment variables may use comma-separated values. Default: `tavily,firecrawl,exa,plain`. Omitted providers are disabled when this is configured. |
| `MAP_PROVIDER_PRIORITY` | `web_map` provider priority. Supported values: `tavily`, `exa`. Config files may use a TOML array; environment variables may use comma-separated values. Default: `tavily,exa`. Omitted providers are disabled when this is configured. |
| `SEARCH_CACHE_DIR` | Optional search session cache directory. Default uses the system temp directory. |
| `GROK_SEARCH_FETCH_MAX_CHARS` | Default `fetch` character cap. |
| `GROK_SEARCH_ALLOW_INTERNAL_FETCH` | Allows `web_fetch` and `web_map` to target private/internal `http(s)` URLs. Default: `false`. Provider endpoints are explicit config and can use private gateways independently. For generic internal `web_fetch`, plain HTTP still requires `plain` to be enabled in `FETCH_PROVIDER_PRIORITY`. |
| `GROK_SEARCH_RESPONSE_MAX_CHARS` | Default `search` response budget. |

## Provider Selection

- Default provider priorities are `grok,tavily,exa` for `web_search`, `tavily,firecrawl,exa,plain` for generic `web_fetch`, and `tavily,exa` for `web_map`.
- Priority config reorders providers and disables omitted providers, so `["exa", "tavily"]` makes Exa first and disables Grok for that command. An empty or all-invalid priority list disables every provider for that command.
- With `GROK_SEARCH_UPSTREAMS`, `web_search` randomly selects one Grok/OpenAI-compatible upstream object for the AI answer and calls `/v1/chat/completions`.
- `web_search --grok-max-retries` overrides `GROK_SEARCH_MAX_RETRIES` for that call. When the flag is omitted, the merged config value is used.
- With `TAVILY_UPSTREAMS`, search fallback, generic fetch, and map randomly select one Tavily upstream object.
- With `FIRECRAWL_UPSTREAMS`, generic `fetch` fallback randomly selects one Firecrawl upstream object.
- Exa uses the official remote MCP endpoint free plan without local key config. In the default priorities, `web_search` uses Exa after Tavily fallback fails, generic `web_fetch` uses Exa after Tavily and Firecrawl fail, and `web_map` uses Exa after Tavily fails or returns no URLs.
- Single-value `GROK_SEARCH_*`, `TAVILY_*`, and `FIRECRAWL_*` keys work as fallback when no upstream table is configured.
- Empty or partially filled upstream objects are ignored. Upstream array objects must explicitly provide every required field; default endpoint/model values are only used by single-value fallback config.
- With no provider keys, specialized public fetchers still work for GitHub, StackExchange, arXiv, and Wikipedia URLs.

## Safety

- Keep real secrets outside the repository.
- `doctor` redacts configuration and reports the active config source, config paths checked, each config path's `exists` flag, cache directory existence, environment variable presence, provider priority, provider enabled state, normalized AI `api_url`, provider endpoints, and upstream counts.
- Do not paste complete local config or shell environment into chat output.
