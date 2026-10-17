import importlib.util
import io
import json
import re
import socket
import sys
import tomllib
import urllib.error
from types import SimpleNamespace
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "websearch.py"
SPEC = importlib.util.spec_from_file_location("websearch", MODULE_PATH)
web_research = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = web_research
SPEC.loader.exec_module(web_research)


def test_grok_search_config_keys_are_ai_provider_settings():
    cfg = web_research.Config(
        {
            "grok_search_api_key": "sk-test",
            "grok_search_url": "http://example.test",
            "grok_search_model": "grok-test",
        }
    )

    assert cfg.get("GROK_SEARCH_API_KEY") == "sk-test"
    assert cfg.get("GROK_SEARCH_URL") == "http://example.test"
    assert cfg.get("GROK_SEARCH_MODEL") == "grok-test"


def test_config_example_uses_required_search_rs_keys():
    text = (Path(__file__).resolve().parents[1] / "config.example.toml").read_text(encoding="utf-8")

    for key in [
        "FIRECRAWL_API_KEY",
        "FIRECRAWL_API_URL",
        "FETCH_PROVIDER_PRIORITY",
        "GROK_SEARCH_API_KEY",
        "GROK_SEARCH_ALLOW_INTERNAL_FETCH",
        "GROK_SEARCH_MAX_RETRIES",
        "GROK_SEARCH_MODEL",
        "GROK_SEARCH_URL",
        "MAP_PROVIDER_PRIORITY",
        "SEARCH_PROVIDER_PRIORITY",
        "TAVILY_API_KEY",
        "TAVILY_API_URL",
    ]:
        assert re.search(rf"\b{key}\s*=", text)

    for legacy_key in [
        "SEARCH_UPSTREAMS",
        "SEARCH_API_KEY",
        "SEARCH_API_URL",
        "SEARCH_TRANSPORT",
        "SEARCH_TIMEOUT_SECONDS",
        "SEARCH_MAX_RETRIES",
        "SEARCH_FETCH_MAX_CHARS",
        "SEARCH_ALLOW_INTERNAL_FETCH",
        "SEARCH_RESPONSE_MAX_CHARS",
    ]:
        assert re.search(rf"(^|[{{,]\s*){legacy_key}\s*=", text, re.MULTILINE) is None
    assert re.search(r"^GROK_SEARCH_WEB_SEARCH\s*=", text, re.MULTILINE) is None


def test_config_example_groups_provider_keys_together():
    text = (Path(__file__).resolve().parents[1] / "config.example.toml").read_text(encoding="utf-8")
    parsed = tomllib.loads(text)

    assert "[[" not in text
    assert text.index("SEARCH_PROVIDER_PRIORITY") < text.index("GROK_SEARCH_UPSTREAMS")
    assert '# SEARCH_PROVIDER_PRIORITY supports: "grok", "tavily", "exa", "duckduckgo", "parallel" (opt-in).' in text
    assert '# FETCH_PROVIDER_PRIORITY supports: "tavily", "firecrawl", "exa", "plain".' in text
    assert '# MAP_PROVIDER_PRIORITY supports: "tavily", "exa".' in text
    assert len(parsed["FIRECRAWL_UPSTREAMS"]) >= 2
    assert len(parsed["GROK_SEARCH_UPSTREAMS"]) >= 2
    assert len(parsed["TAVILY_UPSTREAMS"]) >= 2
    assert set(parsed["FIRECRAWL_UPSTREAMS"][0]) == {"FIRECRAWL_API_KEY", "FIRECRAWL_API_URL"}
    assert set(parsed["GROK_SEARCH_UPSTREAMS"][0]) == {
        "GROK_SEARCH_API_KEY",
        "GROK_SEARCH_MODEL",
        "GROK_SEARCH_URL",
    }
    assert set(parsed["TAVILY_UPSTREAMS"][0]) == {"TAVILY_API_KEY", "TAVILY_API_URL"}


def test_default_timeout_is_120_seconds():
    assert web_research.Config({}).timeout == 120


def test_default_grok_search_max_retries_is_two():
    assert web_research.Config({}).grok_search_max_retries == 2


def test_default_search_model_uses_documented_xai_slug():
    cfg = web_research.Config({"grok_search_api_key": "sk-test"})

    upstream = web_research.random_upstream(cfg, "grok_search")

    assert upstream is not None
    assert upstream["grok_search_model"] == "grok-4.3"


def test_internal_fetch_is_disabled_by_default():
    assert web_research.Config({}).allow_internal_fetch is False


def test_internal_fetch_can_be_configured_from_file():
    assert web_research.Config({"grok_search_allow_internal_fetch": "true"}).allow_internal_fetch is True


def test_search_max_retries_can_be_configured_from_file():
    assert web_research.Config({"grok_search_max_retries": "2"}).grok_search_max_retries == 2


def test_default_provider_priorities_preserve_existing_order():
    cfg = web_research.Config({})

    assert web_research.provider_priority(cfg, "SEARCH_PROVIDER_PRIORITY", web_research.DEFAULT_SEARCH_PROVIDER_PRIORITY) == [
        "grok",
        "tavily",
        "exa",
        "duckduckgo",
    ]
    assert web_research.provider_priority(cfg, "FETCH_PROVIDER_PRIORITY", web_research.DEFAULT_FETCH_PROVIDER_PRIORITY) == [
        "tavily",
        "firecrawl",
        "exa",
        "plain",
    ]
    assert web_research.provider_priority(cfg, "MAP_PROVIDER_PRIORITY", web_research.DEFAULT_MAP_PROVIDER_PRIORITY) == [
        "tavily",
        "exa",
    ]
    assert web_research.search_priority_for_mode(cfg, "news") == [
        "grok",
        "tavily",
        "exa",
        "duckduckgo",
    ]
    assert web_research.search_priority_for_mode(cfg, "academic") == [
        "grok",
        "tavily",
        "exa",
        "duckduckgo",
    ]


def test_provider_priority_accepts_array_and_disables_missing_defaults():
    cfg = web_research.Config({"search_provider_priority": ["exa", "duckduckgo", "unknown", "tavily"]})

    assert web_research.provider_priority(cfg, "SEARCH_PROVIDER_PRIORITY", web_research.DEFAULT_SEARCH_PROVIDER_PRIORITY) == [
        "exa",
        "duckduckgo",
        "tavily",
    ]


def test_provider_priority_accepts_comma_separated_string():
    cfg = web_research.Config({"fetch_provider_priority": "plain, exa"})

    assert web_research.provider_priority(cfg, "FETCH_PROVIDER_PRIORITY", web_research.DEFAULT_FETCH_PROVIDER_PRIORITY) == [
        "plain",
        "exa",
    ]


def test_search_modes_share_one_provider_priority():
    cfg = web_research.Config({"search_provider_priority": ["duckduckgo", "grok", "exa"]})

    assert web_research.search_priority_for_mode(cfg, "general") == [
        "duckduckgo",
        "grok",
        "exa",
    ]
    assert web_research.search_priority_for_mode(cfg, "news") == ["duckduckgo", "grok", "exa"]
    assert web_research.search_priority_for_mode(cfg, "academic") == ["duckduckgo", "grok", "exa"]


def test_empty_provider_priority_array_disables_all_providers():
    merged = {}
    web_research.merge_missing(merged, {"SEARCH_PROVIDER_PRIORITY": []})
    web_research.merge_missing(merged, {"SEARCH_PROVIDER_PRIORITY": ["grok"]})

    assert merged["search_provider_priority"] == []
    assert web_research.provider_priority(
        web_research.Config(merged),
        "SEARCH_PROVIDER_PRIORITY",
        web_research.DEFAULT_SEARCH_PROVIDER_PRIORITY,
    ) == []


def test_cache_dir_can_be_configured_from_file():
    cfg = web_research.Config({"search_cache_dir": "D:/Temp/custom-cache"})

    assert str(cfg.cache_dir).replace("\\", "/").endswith("D:/Temp/custom-cache")


def test_doctor_counts_only_complete_upstreams(capsys):
    cfg = web_research.Config(
        {
            "grok_search_upstreams": [
                {"grok_search_api_key": "sk-ok", "grok_search_model": "m", "grok_search_url": "https://grok.example"},
                {"grok_search_api_key": "", "grok_search_model": "", "grok_search_url": ""},
            ],
            "tavily_upstreams": [
                {"tavily_api_key": "tvly-ok", "tavily_api_url": "https://tavily.example"},
                {"tavily_api_key": "", "tavily_api_url": ""},
            ],
            "firecrawl_upstreams": [
                {"firecrawl_api_key": "fc-ok", "firecrawl_api_url": "https://firecrawl.example"},
                {"firecrawl_api_key": "", "firecrawl_api_url": ""},
            ],
        }
    )

    web_research.command_doctor(SimpleNamespace(), cfg)

    output = json.loads(capsys.readouterr().out)
    assert output["grok_search_upstreams"] == 1
    assert output["tavily_upstreams"] == 1
    assert output["firecrawl_upstreams"] == 1
    assert output["provider_priority"] == {
        "search": ["grok", "tavily", "exa", "duckduckgo"],
        "fetch": ["tavily", "firecrawl", "exa", "plain"],
        "map": ["tavily", "exa"],
    }
    assert output["search_modes"] == {
        "general": ["grok", "tavily", "exa", "duckduckgo"],
        "news": ["grok", "tavily", "exa", "duckduckgo"],
        "academic": ["grok", "tavily", "exa", "duckduckgo"],
    }
    assert output["provider_enabled"] == {
        "parallel": False,
        "grok": True,
        "tavily": True,
        "firecrawl": True,
        "exa": True,
        "duckduckgo": True,
        "plain": True,
    }


def test_doctor_marks_config_file_existence(monkeypatch, tmp_path, capsys):
    skill_root = tmp_path / "skill"
    userprofile = tmp_path / "userprofile"
    home = tmp_path / "home"
    explicit = tmp_path / "explicit.toml"
    skill_root.mkdir()
    (userprofile / ".config" / "web-search-skill").mkdir(parents=True)
    (home / ".config" / "web-search-skill").mkdir(parents=True)
    user_config = userprofile / ".config" / "web-search-skill" / "config.toml"
    home_config = home / ".config" / "web-search-skill" / "config.toml"
    skill_config = skill_root / "config.toml"
    user_config.write_text("GROK_SEARCH_TIMEOUT_SECONDS = 30\n", encoding="utf-8")
    explicit.write_text("GROK_SEARCH_TIMEOUT_SECONDS = 50\n", encoding="utf-8")

    monkeypatch.setattr(web_research, "skill_dir", lambda: skill_root)
    monkeypatch.setenv("USERPROFILE", str(userprofile))
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("WEB_RESEARCH_CONFIG", str(explicit))

    web_research.command_doctor(SimpleNamespace(), web_research.Config({}))

    output = json.loads(capsys.readouterr().out)
    assert output["config_file_checked"] == [
        str(user_config),
        str(home_config),
        str(skill_config),
        str(explicit),
    ]
    assert output["config_files"] == [
        {"priority": 1, "source": "user", "path": str(user_config), "exists": True},
        {"priority": 2, "source": "user", "path": str(home_config), "exists": False},
        {"priority": 3, "source": "skill-local", "path": str(skill_config), "exists": False},
        {"priority": 4, "source": "fallback", "path": str(explicit), "exists": True},
    ]
    assert isinstance(output["cache_dir_exists"], bool)


def test_config_candidates_are_toml_only(monkeypatch):
    monkeypatch.setenv("WEB_RESEARCH_CONFIG", "D:/Temp/custom.json")

    candidates = web_research.config_file_candidates()

    assert candidates
    assert all(path.suffix == ".toml" for path in candidates)
    assert not any(path.name == "config.json" for path in candidates)


def test_user_config_file_has_highest_priority(monkeypatch, tmp_path):
    skill_root = tmp_path / "skill"
    userprofile = tmp_path / "userprofile"
    home = tmp_path / "home"
    explicit = tmp_path / "explicit.toml"
    skill_root.mkdir()
    (userprofile / ".config" / "web-search-skill").mkdir(parents=True)
    (home / ".config" / "web-search-skill").mkdir(parents=True)

    (skill_root / "config.toml").write_text("GROK_SEARCH_TIMEOUT_SECONDS = 10\n", encoding="utf-8")
    (userprofile / ".config" / "web-search-skill" / "config.toml").write_text(
        "GROK_SEARCH_TIMEOUT_SECONDS = 30\n",
        encoding="utf-8",
    )
    (home / ".config" / "web-search-skill" / "config.toml").write_text(
        "GROK_SEARCH_TIMEOUT_SECONDS = 40\n",
        encoding="utf-8",
    )
    explicit.write_text("GROK_SEARCH_TIMEOUT_SECONDS = 50\n", encoding="utf-8")

    monkeypatch.setattr(web_research, "skill_dir", lambda: skill_root)
    monkeypatch.setenv("USERPROFILE", str(userprofile))
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("WEB_RESEARCH_CONFIG", str(explicit))
    monkeypatch.setenv("GROK_SEARCH_TIMEOUT_SECONDS", "20")

    cfg = web_research.Config(web_research.lower_keys(web_research.load_file_config()))

    assert cfg.timeout == 30
    assert cfg.file_values[web_research.CONFIG_SOURCE_META_KEY]["source"] == "user"
    assert web_research.config_file_candidates()[0] == userprofile / ".config" / "web-search-skill" / "config.toml"


def test_config_sources_are_not_mixed_with_environment_upstream(monkeypatch, tmp_path):
    skill_root = tmp_path / "skill"
    userprofile = tmp_path / "userprofile"
    home = tmp_path / "home"
    skill_root.mkdir()
    (userprofile / ".config" / "web-search-skill").mkdir(parents=True)
    home.mkdir()
    (userprofile / ".config" / "web-search-skill" / "config.toml").write_text(
        'SEARCH_PROVIDER_PRIORITY = ["grok"]\n',
        encoding="utf-8",
    )

    monkeypatch.setattr(web_research, "skill_dir", lambda: skill_root)
    monkeypatch.setenv("USERPROFILE", str(userprofile))
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("GROK_SEARCH_API_KEY", "sk-env")
    monkeypatch.setenv("GROK_SEARCH_MODEL", "env-model")
    monkeypatch.setenv("GROK_SEARCH_URL", "https://env.example")
    monkeypatch.delenv("WEB_RESEARCH_CONFIG", raising=False)

    cfg = web_research.Config(web_research.lower_keys(web_research.load_file_config()))

    assert web_research.provider_priority(cfg, "SEARCH_PROVIDER_PRIORITY", web_research.DEFAULT_SEARCH_PROVIDER_PRIORITY) == [
        "grok"
    ]
    assert web_research.random_upstream(cfg, "grok_search") is None
    assert cfg.file_values[web_research.CONFIG_SOURCE_META_KEY]["source"] == "user"


def test_environment_config_is_used_when_no_file_source_has_values(monkeypatch, tmp_path):
    skill_root = tmp_path / "skill"
    userprofile = tmp_path / "userprofile"
    home = tmp_path / "home"
    skill_root.mkdir()
    (userprofile / ".config" / "web-search-skill").mkdir(parents=True)
    (userprofile / ".config" / "web-search-skill" / "config.toml").write_text(
        'GROK_SEARCH_UPSTREAMS = [{ GROK_SEARCH_API_KEY = "" }]\n',
        encoding="utf-8",
    )
    home.mkdir()

    monkeypatch.setattr(web_research, "skill_dir", lambda: skill_root)
    monkeypatch.setenv("USERPROFILE", str(userprofile))
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("GROK_SEARCH_API_KEY", "sk-env")
    monkeypatch.setenv("GROK_SEARCH_MODEL", "env-model")
    monkeypatch.setenv("GROK_SEARCH_URL", "https://env.example")
    monkeypatch.delenv("WEB_RESEARCH_CONFIG", raising=False)

    cfg = web_research.Config(web_research.lower_keys(web_research.load_file_config()))

    assert web_research.random_upstream(cfg, "grok_search") == {
        "grok_search_api_key": "sk-env",
        "grok_search_model": "env-model",
        "grok_search_url": "https://env.example",
    }
    assert cfg.file_values[web_research.CONFIG_SOURCE_META_KEY]["source"] == "environment"


def test_home_config_file_is_highest_priority_without_userprofile(monkeypatch, tmp_path):
    skill_root = tmp_path / "skill"
    home = tmp_path / "home"
    explicit = tmp_path / "explicit.toml"
    skill_root.mkdir()
    (home / ".config" / "web-search-skill").mkdir(parents=True)

    (skill_root / "config.toml").write_text("GROK_SEARCH_TIMEOUT_SECONDS = 10\n", encoding="utf-8")
    (home / ".config" / "web-search-skill" / "config.toml").write_text(
        "GROK_SEARCH_TIMEOUT_SECONDS = 40\n",
        encoding="utf-8",
    )
    explicit.write_text("GROK_SEARCH_TIMEOUT_SECONDS = 50\n", encoding="utf-8")

    monkeypatch.setattr(web_research, "skill_dir", lambda: skill_root)
    monkeypatch.delenv("USERPROFILE", raising=False)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("WEB_RESEARCH_CONFIG", str(explicit))
    monkeypatch.setenv("GROK_SEARCH_TIMEOUT_SECONDS", "20")

    cfg = web_research.Config(web_research.lower_keys(web_research.load_file_config()))

    assert cfg.timeout == 40
    assert web_research.config_file_candidates()[0] == home / ".config" / "web-search-skill" / "config.toml"


def test_incomplete_upstream_template_does_not_mask_later_config(monkeypatch):
    merged = {}
    web_research.merge_missing(
        merged,
        {
            "GROK_SEARCH_UPSTREAMS": [
                {"GROK_SEARCH_API_KEY": "", "GROK_SEARCH_MODEL": "", "GROK_SEARCH_URL": ""},
            ]
        },
    )
    web_research.merge_missing(
        merged,
        {
            "GROK_SEARCH_UPSTREAMS": [
                {
                    "GROK_SEARCH_API_KEY": "sk-real",
                    "GROK_SEARCH_MODEL": "model-real",
                    "GROK_SEARCH_URL": "https://real.example",
                }
            ]
        },
    )
    monkeypatch.setattr(web_research.random, "choice", lambda items: items[0])

    assert web_research.random_upstream(web_research.Config(merged), "grok_search") == {
        "grok_search_api_key": "sk-real",
        "grok_search_model": "model-real",
        "grok_search_url": "https://real.example",
    }


def test_array_upstream_requires_explicit_non_empty_required_fields(monkeypatch):
    merged = {}
    web_research.merge_missing(
        merged,
        {
            "GROK_SEARCH_UPSTREAMS": [
                {"GROK_SEARCH_API_KEY": "sk-only"},
            ]
        },
    )
    web_research.merge_missing(
        merged,
        {
            "GROK_SEARCH_UPSTREAMS": [
                {
                    "GROK_SEARCH_API_KEY": "sk-real",
                    "GROK_SEARCH_MODEL": "model-real",
                    "GROK_SEARCH_URL": "https://real.example",
                }
            ]
        },
    )
    monkeypatch.setattr(web_research.random, "choice", lambda items: items[0])

    assert web_research.random_upstream(web_research.Config(merged), "grok_search") == {
        "grok_search_api_key": "sk-real",
        "grok_search_model": "model-real",
        "grok_search_url": "https://real.example",
    }


def test_empty_scalar_config_does_not_mask_later_config():
    merged = {}
    web_research.merge_missing(merged, {"GITHUB_TOKEN": ""})
    web_research.merge_missing(merged, {"GITHUB_TOKEN": "configured"})

    assert web_research.Config(merged).get("GITHUB_TOKEN") == "configured"


def test_read_config_file_requires_real_toml_parser(monkeypatch, tmp_path):
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        'GROK_SEARCH_UPSTREAMS = [{ GROK_SEARCH_API_KEY = "sk", GROK_SEARCH_MODEL = "m", GROK_SEARCH_URL = "https://api.example" }]',
        encoding="utf-8",
    )
    monkeypatch.setattr(web_research, "tomllib", None)

    try:
        web_research.read_config_file(config_path)
    except RuntimeError as exc:
        assert "Python 3.11" in str(exc)
    else:
        raise AssertionError("read_config_file should require a real TOML parser")


def test_read_config_file_normalizes_utf8_bom(tmp_path):
    config_path = tmp_path / "config.toml"
    config_path.write_bytes(b"\xef\xbb\xbfGROK_SEARCH_TIMEOUT_SECONDS = 30\n")

    assert web_research.read_config_file(config_path)["GROK_SEARCH_TIMEOUT_SECONDS"] == 30
    assert not config_path.read_bytes().startswith(b"\xef\xbb\xbf")


def test_main_reports_config_parse_errors_without_traceback(monkeypatch, tmp_path, capsys):
    skill_root = tmp_path / "skill"
    userprofile = tmp_path / "userprofile"
    home = tmp_path / "home"
    config_dir = userprofile / ".config" / "web-search-skill"
    skill_root.mkdir()
    config_dir.mkdir(parents=True)
    home.mkdir()
    (config_dir / "config.toml").write_text("GROK_SEARCH_TIMEOUT_SECONDS =\n", encoding="utf-8")

    monkeypatch.setattr(web_research, "skill_dir", lambda: skill_root)
    monkeypatch.setenv("USERPROFILE", str(userprofile))
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("WEB_RESEARCH_CONFIG", raising=False)

    assert web_research.main(["doctor"]) == 2

    captured = capsys.readouterr()
    assert "Config error:" in captured.err
    assert "config.toml" in captured.err
    assert "Traceback" not in captured.err


def test_doctor_reports_environment_presence_without_values(monkeypatch, capsys):
    monkeypatch.setenv("GROK_SEARCH_API_KEY", "sk-secret")
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)

    web_research.command_doctor(SimpleNamespace(), web_research.Config({}))

    output = json.loads(capsys.readouterr().out)
    assert output["environment"]["GROK_SEARCH_API_KEY"] is True
    assert output["environment"]["TAVILY_API_KEY"] is False
    assert "sk-secret" not in json.dumps(output)


def test_doctor_reports_ai_api_url_not_responses_endpoint(capsys):
    cfg = web_research.Config(
        {
            "grok_search_upstreams": [
                {
                    "grok_search_api_key": "sk-ok",
                    "grok_search_model": "m",
                    "grok_search_url": "http://gateway.example",
                }
            ]
        }
    )

    web_research.command_doctor(SimpleNamespace(), cfg)

    output = json.loads(capsys.readouterr().out)
    ai_probe = next(probe for probe in output["probes"] if probe["name"] == "ai-provider")
    assert ai_probe["api_url"] == "http://gateway.example/v1"
    assert "endpoint" not in ai_probe
    assert "/responses" not in json.dumps(ai_probe)


def test_doctor_reports_provider_enabled_from_priority(capsys):
    cfg = web_research.Config(
        {
            "search_provider_priority": [],
            "fetch_provider_priority": ["tavily"],
            "map_provider_priority": ["exa"],
            "grok_search_upstreams": [
                {"grok_search_api_key": "sk-ok", "grok_search_model": "m", "grok_search_url": "https://api.example"}
            ],
        }
    )

    web_research.command_doctor(SimpleNamespace(), cfg)

    output = json.loads(capsys.readouterr().out)
    assert output["provider_priority"] == {"search": [], "fetch": ["tavily"], "map": ["exa"]}
    assert output["provider_enabled"] == {
        "parallel": False,
        "grok": False,
        "tavily": True,
        "firecrawl": False,
        "exa": True,
        "duckduckgo": False,
        "plain": False,
    }
    ai_probe = next(probe for probe in output["probes"] if probe["name"] == "ai-provider")
    exa_probe = next(probe for probe in output["probes"] if probe["name"] == "exa-mcp")
    duckduckgo_probe = next(probe for probe in output["probes"] if probe["name"] == "duckduckgo-html")
    assert ai_probe["configured"] is True
    assert ai_probe["enabled"] is False
    assert exa_probe["enabled"] is True
    assert duckduckgo_probe["configured"] is True
    assert duckduckgo_probe["enabled"] is False


def test_ai_search_uses_openai_chat_completions_by_default(monkeypatch):
    captured = {}

    def fake_request_json(method, endpoint, headers, payload, timeout, **_kwargs):
        captured.update(
            {
                "method": method,
                "endpoint": endpoint,
                "headers": headers,
                "payload": payload,
                "timeout": timeout,
            }
        )
        return {
            "choices": [
                {
                    "message": {
                        "content": "answer https://example.com",
                        "annotations": [{"url": "https://example.com"}],
                    }
                }
            ]
        }

    monkeypatch.setattr(web_research, "request_json", fake_request_json)

    result = web_research.ai_search(
        web_research.Config(
            {
                "grok_search_api_key": "sk-ok",
                "grok_search_model": "m",
                "grok_search_url": "http://gateway.example",
            }
        ),
        "query",
    )

    assert captured["method"] == "POST"
    assert captured["endpoint"] == "http://gateway.example/v1/chat/completions"
    assert captured["payload"]["model"] == "m"
    assert captured["payload"]["messages"] == [{"role": "user", "content": "query"}]
    assert captured["payload"]["stream"] is False
    assert captured["payload"]["tools"] == [{"type": "web_search"}]
    assert result["answer"] == "answer https://example.com"
    assert result["urls"] == ["https://example.com"]


def test_configured_ai_upstream_may_use_private_gateway(monkeypatch):
    captured = {}

    def fake_request_json(method, endpoint, headers, payload, timeout, allow_internal=False):
        captured["allow_internal"] = allow_internal
        return {"choices": [{"message": {"content": "answer"}}]}

    monkeypatch.setattr(web_research, "request_json", fake_request_json)

    web_research.ai_search(
        web_research.Config(
            {
                "grok_search_api_key": "sk-ok",
                "grok_search_model": "m",
                "grok_search_url": "http://10.0.0.5:8080",
            }
        ),
        "query",
    )

    assert captured["allow_internal"] is True


def test_request_text_rejects_non_web_and_internal_urls():
    for url in [
        "file:///C:/Windows/win.ini",
        "http://localhost/",
        "http://127.0.0.1/",
        "http://10.0.0.1/",
        "http://172.16.0.1/",
        "http://192.168.1.1/",
        "http://169.254.169.254/latest/meta-data/",
        "http://[::1]/",
    ]:
        try:
            web_research.request_text("GET", url)
        except web_research.HttpError as exc:
            assert exc.status == 400
        else:
            raise AssertionError(f"{url} should be rejected")


def test_validate_web_url_does_not_resolve_hostname_for_internal_check(monkeypatch):
    def fail_getaddrinfo(*_args, **_kwargs):
        raise AssertionError("validation should not resolve hostnames")

    monkeypatch.setattr(socket, "getaddrinfo", fail_getaddrinfo)

    web_research.validate_web_url("https://example.com")


def test_validate_web_url_allows_hostname_that_resolves_to_proxy_reserved_ip(monkeypatch):
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [(None, None, None, None, ("198.18.0.1", 0))],
    )

    web_research.validate_web_url("https://example.com")


def test_request_text_rejects_invalid_port_without_traceback():
    try:
        web_research.request_text("GET", "https://example.com:99999/")
    except web_research.HttpError as exc:
        assert exc.status == 400
        assert "port" in str(exc)
    else:
        raise AssertionError("invalid URL port should be rejected")


def test_request_text_normalizes_transport_failures(monkeypatch):
    monkeypatch.setattr(
        web_research.urllib.request,
        "build_opener",
        lambda *_args, **_kwargs: SimpleNamespace(
            open=lambda *_args, **_kwargs: (_ for _ in ()).throw(urllib.error.URLError("dns failure"))
        ),
    )

    try:
        web_research.request_text("GET", "https://example.com")
    except web_research.HttpError as exc:
        assert exc.status == 0
        assert "network failure" in str(exc)
    else:
        raise AssertionError("transport failure should be normalized")


def test_request_text_leaves_dns_resolution_to_urlopen(monkeypatch):
    calls = []
    captured = {}
    private = (None, None, None, None, ("198.18.0.1", 443))

    def fake_getaddrinfo(*args, **_kwargs):
        calls.append(args)
        return [private]

    class FakeHeaders:
        @staticmethod
        def get_content_charset():
            return "utf-8"

    class FakeResponse:
        headers = FakeHeaders()

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        @staticmethod
        def read():
            return b"ok"

    def fake_open(_req, timeout):
        del timeout
        captured["resolved"] = socket.getaddrinfo("proxy.example", 443)
        return FakeResponse()

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
    monkeypatch.setattr(web_research.urllib.request, "build_opener", lambda *_args, **_kwargs: SimpleNamespace(open=fake_open))

    assert web_research.request_text("GET", "https://proxy.example/resource") == "ok"
    assert captured["resolved"] == [private]
    assert calls == [("proxy.example", 443)]


def test_request_text_falls_back_to_utf8_for_unknown_charset(monkeypatch):
    class FakeHeaders:
        @staticmethod
        def get_content_charset():
            return "x-not-a-real-charset"

    class FakeResponse:
        headers = FakeHeaders()

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        @staticmethod
        def read():
            return "ok".encode("utf-8")

    monkeypatch.setattr(
        web_research.urllib.request,
        "build_opener",
        lambda *_args, **_kwargs: SimpleNamespace(open=lambda *_args, **_kwargs: FakeResponse()),
    )

    assert web_research.request_text("GET", "https://example.com") == "ok"


def test_request_text_rejects_response_body_over_memory_limit(monkeypatch):
    class FakeHeaders:
        @staticmethod
        def get_content_charset():
            return "utf-8"

        @staticmethod
        def get(_name, default=None):
            return default

    class FakeResponse:
        headers = FakeHeaders()

        def __init__(self):
            self.calls = 0

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self, _size=-1):
            self.calls += 1
            return b"abcdef" if self.calls == 1 else b""

    monkeypatch.setattr(web_research, "HTTP_RESPONSE_MAX_BYTES", 5)
    monkeypatch.setattr(
        web_research.urllib.request,
        "build_opener",
        lambda *_args, **_kwargs: SimpleNamespace(open=lambda *_args, **_kwargs: FakeResponse()),
    )

    try:
        web_research.request_text("GET", "https://example.com")
    except web_research.HttpError as exc:
        assert exc.status == 413
        assert "exceeds" in str(exc)
    else:
        raise AssertionError("oversized response should be rejected")


def test_exa_mcp_post_sends_protocol_version_header(monkeypatch):
    captured = {}

    class FakeResponse:
        class Headers:
            @staticmethod
            def get(_name):
                return "session-1"

        headers = Headers()

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        @staticmethod
        def read():
            return b"{}"

    def fake_open(req, timeout):
        captured["timeout"] = timeout
        captured["headers"] = dict(req.headers)
        return FakeResponse()

    def fake_validate(_url, *, allow_internal, timeout):
        captured["validate_allow_internal"] = allow_internal
        captured["validate_timeout"] = timeout

    def fake_build_opener(handler):
        captured["redirect_allow_internal"] = handler.allow_internal
        return SimpleNamespace(open=fake_open)

    monkeypatch.setattr(web_research, "validate_web_url", fake_validate)
    monkeypatch.setattr(web_research.urllib.request, "build_opener", fake_build_opener)

    web_research.exa_mcp_post("https://mcp.exa.ai/mcp", {"jsonrpc": "2.0"}, web_research.Config({}))

    assert captured["validate_allow_internal"] is False
    assert captured["redirect_allow_internal"] is False
    assert captured["headers"]["Mcp-protocol-version"] == web_research.MCP_PROTOCOL_VERSION


def test_web_fetch_rejects_internal_url_before_provider_extract(monkeypatch):
    calls = []

    def record_extract(*_args, **_kwargs):
        calls.append("extract")
        return None

    monkeypatch.setattr(web_research, "tavily_extract", record_extract)
    monkeypatch.setattr(web_research, "firecrawl_extract", record_extract)

    try:
        web_research.command_fetch(SimpleNamespace(url="http://127.0.0.1/", max_chars=None), web_research.Config({}))
    except web_research.HttpError as exc:
        assert exc.status == 400
    else:
        raise AssertionError("internal web_fetch URL should be rejected")
    assert calls == []


def test_web_fetch_allows_internal_url_when_explicitly_configured(monkeypatch, capsys):
    calls = []

    def fail_extract(*_args, **_kwargs):
        raise AssertionError("external extract should not receive internal URLs")

    def fake_plain_fetch(url, cfg, *, max_chars=None, allow_internal=False):
        del cfg, max_chars
        calls.append((url, allow_internal))
        return "internal docs", False

    monkeypatch.setattr(web_research, "tavily_extract", fail_extract)
    monkeypatch.setattr(web_research, "firecrawl_extract", fail_extract)
    monkeypatch.setattr(web_research, "plain_fetch", fake_plain_fetch)

    web_research.command_fetch(
        SimpleNamespace(url="http://127.0.0.1/docs", max_chars=None),
        web_research.Config({"grok_search_allow_internal_fetch": "true"}),
    )

    output = json.loads(capsys.readouterr().out)
    assert output["content"] == "internal docs"
    assert output["source_type"] == "plain-http"
    assert calls == [("http://127.0.0.1/docs", True)]


def test_internal_fetch_respects_plain_provider_priority(monkeypatch, capsys):
    for name in ["github_fetch", "stackexchange_fetch", "arxiv_fetch", "wikipedia_fetch"]:
        monkeypatch.setattr(web_research, name, lambda *_args, **_kwargs: None)
    monkeypatch.setattr(web_research, "plain_fetch", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("plain should not be called")))

    web_research.command_fetch(
        SimpleNamespace(url="http://127.0.0.1/docs", max_chars=None),
        web_research.Config({"grok_search_allow_internal_fetch": "true", "fetch_provider_priority": ["tavily"]}),
    )

    output = json.loads(capsys.readouterr().out)
    assert output["content"] == ""
    assert output["source_type"] == "none"
    assert output["warnings"] == ["No enabled fetch provider returned content."]


def test_find_urls_strips_trailing_markdown_emphasis():
    assert web_research.find_urls({"text": "**https://openai.com/**"}) == ["https://openai.com/"]


def test_find_urls_stops_before_markdown_citation():
    assert web_research.find_urls({"text": "**https://chatgpt.com/**.[[2]](https://chatgpt.com/overview/)"}) == [
        "https://chatgpt.com/",
        "https://chatgpt.com/overview/",
    ]


def test_cli_exposes_search_rs_tool_names():
    parser = web_research.build_parser()

    assert parser.parse_args(["web_search", "--query", "q"]).func is web_research.command_search
    assert parser.parse_args(["web_search", "--query", "q", "--grok-max-retries", "2"]).grok_max_retries == 2
    assert parser.parse_args(["get_sources", "--session-id", "abc123abc123abcd"]).func is web_research.command_sources
    assert parser.parse_args(["web_fetch", "--url", "https://example.com"]).func is web_research.command_fetch
    assert parser.parse_args(["web_map", "--url", "https://example.com"]).func is web_research.command_map
    assert parser.parse_args(["doctor"]).func is web_research.command_doctor


def test_random_upstream_keeps_provider_fields_together(monkeypatch):
    cfg = web_research.Config(
        {
            "grok_search_upstreams": [
                {
                    "grok_search_api_key": "sk-a",
                    "grok_search_model": "model-a",
                    "grok_search_url": "https://a.example",
                },
                {
                    "grok_search_api_key": "sk-b",
                    "grok_search_model": "model-b",
                    "grok_search_url": "https://b.example",
                },
            ]
        }
    )
    monkeypatch.setattr(web_research.random, "choice", lambda items: items[1])

    upstream = web_research.random_upstream(cfg, "grok_search")

    assert upstream == {
        "grok_search_api_key": "sk-b",
        "grok_search_model": "model-b",
        "grok_search_url": "https://b.example",
    }


def test_random_upstream_ignores_incomplete_entries(monkeypatch):
    cfg = web_research.Config(
        {
            "grok_search_upstreams": [
                {
                    "grok_search_api_key": "",
                    "grok_search_model": "model-empty-key",
                    "grok_search_url": "https://empty-key.example",
                },
                {
                    "grok_search_api_key": "sk-missing-model",
                    "grok_search_model": "",
                    "grok_search_url": "https://missing-model.example",
                },
                {
                    "grok_search_api_key": "sk-valid",
                    "grok_search_model": "model-valid",
                    "grok_search_url": "https://valid.example",
                },
            ]
        }
    )
    monkeypatch.setattr(web_research.random, "choice", lambda items: items[0])

    assert web_research.random_upstream(cfg, "grok_search") == {
        "grok_search_api_key": "sk-valid",
        "grok_search_model": "model-valid",
        "grok_search_url": "https://valid.example",
    }


def test_random_upstream_returns_none_when_only_empty_entries():
    cfg = web_research.Config(
        {
            "tavily_upstreams": [
                {"tavily_api_key": "", "tavily_api_url": "https://api.tavily.com"},
                {"tavily_api_key": "", "tavily_api_url": ""},
            ]
        }
    )

    assert web_research.random_upstream(cfg, "tavily") is None


def test_exa_upstreams_are_ignored_because_mcp_is_keyless():
    cfg = web_research.Config({"exa_upstreams": [{"exa_api_key": "secret", "exa_api_url": "https://api.exa.ai"}]})

    assert web_research.random_upstream(cfg, "exa") is None
    assert web_research.complete_upstream_count(cfg, "exa") == 0


def test_single_value_config_remains_fallback_upstream():
    cfg = web_research.Config(
        {
            "tavily_api_key": "tvly",
            "tavily_api_url": "https://api.tavily.com",
        }
    )

    assert web_research.random_upstream(cfg, "tavily") == {
        "tavily_api_key": "tvly",
        "tavily_api_url": "https://api.tavily.com",
    }


def test_exa_search_uses_mcp_free_plan_without_key(monkeypatch):
    calls = []

    def fake_mcp_call(endpoint, tool_name, arguments, cfg):
        calls.append((endpoint, tool_name, arguments, cfg.timeout))
        return "Title: Example\nURL: https://example.com\nHighlights:\nhello"

    monkeypatch.setattr(web_research, "exa_mcp_tool_call", fake_mcp_call)

    result = web_research.exa_search(
        web_research.Config({}),
        "query",
        max_sources=2,
        detailed=False,
        include_domains=[],
        exclude_domains=[],
        recency_days=None,
    )

    assert calls == [
        (web_research.EXA_MCP_URL, "web_search_exa", {"query": "query", "numResults": 2}, 120)
    ]
    assert result["answer"].startswith("Title: Example")
    assert result["sources"] == [
        {"title": "Example", "url": "https://example.com", "content": "Title: Example\nURL: https://example.com\nHighlights:\nhello", "score": None, "published_date": None, "provider": "exa"}
    ]


def test_exa_search_uses_advanced_mcp_when_filters_are_needed(monkeypatch):
    captured = {}

    def fake_mcp_call(endpoint, tool_name, arguments, cfg):
        captured.update({"endpoint": endpoint, "tool_name": tool_name, "arguments": arguments})
        return json.dumps({"results": [{"title": "Example", "url": "https://example.com", "text": "hello"}]})

    monkeypatch.setattr(web_research, "exa_mcp_tool_call", fake_mcp_call)

    result = web_research.exa_search(
        web_research.Config({}),
        "query",
        max_sources=1,
        detailed=False,
        include_domains=["example.com"],
        exclude_domains=[],
        recency_days=None,
    )

    assert captured == {
        "endpoint": web_research.EXA_MCP_URL,
        "tool_name": "web_search_advanced_exa",
        "arguments": {"query": "query", "numResults": 1, "type": "auto", "enableHighlights": True, "includeDomains": ["example.com"]},
    }
    assert result["sources"][0]["url"] == "https://example.com"
    assert result["answer"] == "Title: Example\nURL: https://example.com\nhello"


def test_exa_extract_uses_mcp_web_fetch(monkeypatch):
    captured = {}

    def fake_mcp_call(endpoint, tool_name, arguments, cfg):
        captured.update({"endpoint": endpoint, "tool_name": tool_name, "arguments": arguments})
        return "# Example\nURL: https://example.com\n\nbody"

    monkeypatch.setattr(web_research, "exa_mcp_tool_call", fake_mcp_call)

    content = web_research.exa_extract(
        "https://example.com",
        web_research.Config({}),
    )

    assert content == "# Example\nURL: https://example.com\n\nbody"
    assert captured == {
        "endpoint": web_research.EXA_MCP_URL,
        "tool_name": "web_fetch_exa",
        "arguments": {"urls": ["https://example.com"]},
    }


def test_exa_map_uses_mcp_search_urls(monkeypatch):
    def fake_mcp_call(_endpoint, tool_name, arguments, _cfg):
        assert tool_name == "web_search_advanced_exa"
        assert arguments["numResults"] == 2
        assert arguments["includeDomains"] == ["example.com"]
        return "\n".join(
            [
                "Title: A",
                "URL: https://example.com/a",
                "",
                "---",
                "",
                "Title: B",
                "URL: https://example.com/b",
            ]
        )

    monkeypatch.setattr(web_research, "exa_mcp_tool_call", fake_mcp_call)

    assert web_research.exa_map("https://example.com/docs", web_research.Config({}), 2) == [
        "https://example.com/a",
        "https://example.com/b",
    ]


def test_exa_plaintext_url_fallback_does_not_duplicate_full_content():
    text = "See https://example.com/a and https://example.com/b for details"

    sources = web_research.exa_sources_from_mcp_text(text)

    assert sources == [
        {"title": "https://example.com/a", "url": "https://example.com/a", "content": "", "score": None, "published_date": None, "provider": "exa"},
        {"title": "https://example.com/b", "url": "https://example.com/b", "content": "", "score": None, "published_date": None, "provider": "exa"},
    ]


def test_command_map_reports_provider_failure_reasons(monkeypatch):
    monkeypatch.setattr(web_research, "tavily_map", lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("tavily down")))
    monkeypatch.setattr(web_research, "exa_map", lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("exa down")))

    try:
        web_research.command_map(SimpleNamespace(url="https://example.com", max_results=3), web_research.Config({}))
    except SystemExit as exc:
        message = str(exc)
    else:
        raise AssertionError("map failures should raise SystemExit")

    assert "Map failed:" in message
    assert "Tavily map failed: tavily down" in message
    assert "Exa map failed: exa down" in message


def test_command_map_reports_unconfigured_tavily_before_exa_failure(monkeypatch):
    monkeypatch.setattr(web_research, "tavily_map", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(web_research, "exa_map", lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("exa down")))

    try:
        web_research.command_map(SimpleNamespace(url="https://example.com", max_results=3), web_research.Config({}))
    except SystemExit as exc:
        message = str(exc)
    else:
        raise AssertionError("map failures should raise SystemExit")

    assert "Map failed:" in message
    assert "Tavily map not configured." in message
    assert "Exa map failed: exa down" in message


def test_fetch_priority_can_try_exa_before_tavily_and_firecrawl(monkeypatch, capsys):
    for name in ["github_fetch", "stackexchange_fetch", "arxiv_fetch", "wikipedia_fetch"]:
        monkeypatch.setattr(web_research, name, lambda *_args, **_kwargs: None)
    monkeypatch.setattr(web_research, "tavily_extract", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("tavily should not be called")))
    monkeypatch.setattr(web_research, "firecrawl_extract", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("firecrawl should not be called")))
    monkeypatch.setattr(web_research, "exa_extract", lambda *_args, **_kwargs: "exa content")

    web_research.command_fetch(
        SimpleNamespace(url="https://example.com", max_chars=0),
        web_research.Config({"fetch_provider_priority": ["exa", "tavily", "firecrawl", "plain"]}),
    )

    output = json.loads(capsys.readouterr().out)
    assert output["content"] == "exa content"
    assert output["source_type"] == "exa"


def test_fetch_priority_without_plain_disables_plain_fallback(monkeypatch, capsys):
    for name in ["github_fetch", "stackexchange_fetch", "arxiv_fetch", "wikipedia_fetch"]:
        monkeypatch.setattr(web_research, name, lambda *_args, **_kwargs: None)
    monkeypatch.setattr(web_research, "tavily_extract", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(web_research, "plain_fetch", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("plain should not be called")))

    web_research.command_fetch(
        SimpleNamespace(url="https://example.com", max_chars=0),
        web_research.Config({"fetch_provider_priority": ["tavily"]}),
    )

    output = json.loads(capsys.readouterr().out)
    assert output["content"] == ""
    assert output["source_type"] == "none"
    assert output["warnings"] == ["No enabled fetch provider returned content."]


def test_map_priority_can_try_exa_before_tavily(monkeypatch, capsys):
    monkeypatch.setattr(web_research, "tavily_map", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("tavily should not be called")))
    monkeypatch.setattr(web_research, "exa_map", lambda *_args, **_kwargs: ["https://example.com/a"])

    web_research.command_map(
        SimpleNamespace(url="https://example.com", max_results=3),
        web_research.Config({"map_provider_priority": ["exa", "tavily"]}),
    )

    output = json.loads(capsys.readouterr().out)
    assert output["urls"] == ["https://example.com/a"]
    assert output["source_type"] == "exa"


def test_clean_found_url_removes_escaped_mcp_markup():
    assert web_research.clean_found_url("https://mcp.exa.ai/mcp\\") == "https://mcp.exa.ai/mcp"
    assert web_research.clean_found_url("https://example.com/path`") == "https://example.com/path"


def test_clean_found_url_preserves_balanced_trailing_parenthesis():
    assert (
        web_research.clean_found_url("https://en.wikipedia.org/wiki/Python_(programming_language)")
        == "https://en.wikipedia.org/wiki/Python_(programming_language)"
    )


def test_find_urls_preserves_balanced_parentheses_in_url():
    assert web_research.find_urls({"text": "[[1]](https://en.wikipedia.org/wiki/Python_(programming_language))"}) == [
        "https://en.wikipedia.org/wiki/Python_(programming_language)"
    ]


def search_args():
    return SimpleNamespace(
        query="query",
        mode="general",
        format="concise",
        max_chars=None,
        max_sources=3,
        include_domain=[],
        exclude_domain=[],
        recency_days=None,
        grok_max_retries=None,
    )


def test_search_empty_priority_reports_disabled_without_provider_calls(monkeypatch, capsys):
    monkeypatch.setattr(web_research, "save_session", lambda _cfg, _payload: "abc123abc123abcd")
    monkeypatch.setattr(web_research, "ai_search", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("grok should not be called")))
    monkeypatch.setattr(web_research, "tavily_search", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("tavily should not be called")))
    monkeypatch.setattr(web_research, "exa_search", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("exa should not be called")))

    web_research.command_search(
        search_args(),
        web_research.Config({"search_provider_priority": []}),
    )

    output = json.loads(capsys.readouterr().out)
    assert output["answer"] == "No answer text returned."
    assert output["sources_count"] == 0
    assert output["warnings"] == ["No search provider is enabled."]


def test_cli_exposes_search_mode_parameter():
    parser = web_research.build_parser()

    assert parser.parse_args(["web_search", "--query", "q"]).mode == "general"
    assert parser.parse_args(["web_search", "--query", "q", "--mode", "news"]).mode == "news"
    assert parser.parse_args(["web_search", "--query", "q", "--mode", "academic"]).mode == "academic"


def test_news_mode_defaults_to_recent_results_for_non_ai_providers(monkeypatch, capsys):
    calls = {}
    args = search_args()
    args.mode = "news"
    monkeypatch.setattr(web_research, "save_session", lambda _cfg, _payload: "abc123abc123abcd")
    monkeypatch.setattr(web_research, "ai_search", lambda *_args, **_kwargs: None)

    def fake_tavily(_cfg, _query, **kwargs):
        calls.update(kwargs)
        return {"answer": "news answer", "sources": [{"title": "source", "url": "https://example.com", "provider": "tavily"}]}

    monkeypatch.setattr(web_research, "tavily_search", fake_tavily)

    web_research.command_search(args, web_research.Config({"search_provider_priority": ["grok", "tavily"]}))

    output = json.loads(capsys.readouterr().out)
    assert output["mode"] == "news"
    assert calls["search_mode"] == "news"
    assert calls["recency_days"] == 7


def test_academic_mode_keeps_tavily_before_exa_when_priority_is_default(monkeypatch, capsys):
    args = search_args()
    args.mode = "academic"
    monkeypatch.setattr(web_research, "save_session", lambda _cfg, _payload: "abc123abc123abcd")
    monkeypatch.setattr(web_research, "ai_search", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        web_research,
        "tavily_search",
        lambda *_args, **_kwargs: {
            "answer": "tavily academic",
            "sources": [{"title": "paper", "url": "https://example.com/paper", "provider": "tavily"}],
        },
    )
    monkeypatch.setattr(
        web_research,
        "exa_search",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("exa should not run before tavily in academic mode")),
    )

    web_research.command_search(args, web_research.Config({}))

    output = json.loads(capsys.readouterr().out)
    assert output["mode"] == "academic"
    assert output["answer"] == "tavily academic"


def test_non_general_modes_try_ai_before_non_ai_providers(monkeypatch, capsys):
    monkeypatch.setattr(web_research, "save_session", lambda _cfg, _payload: "abc123abc123abcd")
    monkeypatch.setattr(
        web_research,
        "ai_search",
        lambda _cfg, query, **_kwargs: {"answer": f"ai answer for {query}", "urls": ["https://example.com"]},
    )
    monkeypatch.setattr(
        web_research,
        "tavily_search",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("tavily should not run before AI")),
    )

    for mode in ("news", "academic"):
        args = search_args()
        args.mode = mode
        args.query = mode

        web_research.command_search(args, web_research.Config({}))

        output = json.loads(capsys.readouterr().out)
        assert output["mode"] == mode
        assert output["answer"] == f"ai answer for {mode}"


def test_search_does_not_call_tavily_when_ai_succeeds(monkeypatch, capsys):
    monkeypatch.setattr(web_research, "save_session", lambda _cfg, _payload: "abc123abc123abcd")
    monkeypatch.setattr(
        web_research,
        "ai_search",
        lambda _cfg, _query, **_kwargs: {"answer": "ai answer", "urls": ["https://example.com"]},
    )

    def fail_tavily(*_args, **_kwargs):
        raise AssertionError("tavily should not be called when AI succeeds")

    monkeypatch.setattr(web_research, "tavily_search", fail_tavily)

    web_research.command_search(search_args(), web_research.Config({}))

    output = json.loads(capsys.readouterr().out)
    assert output["answer"] == "ai answer"
    assert output["sources_count"] == 1
    assert output["warnings"] == []


def test_search_calls_tavily_when_ai_times_out(monkeypatch, capsys):
    monkeypatch.setattr(web_research, "save_session", lambda _cfg, _payload: "abc123abc123abcd")
    monkeypatch.setattr(
        web_research,
        "ai_search",
        lambda _cfg, _query, **_kwargs: (_ for _ in ()).throw(TimeoutError("timed out")),
    )
    monkeypatch.setattr(
        web_research,
        "tavily_search",
        lambda *_args, **_kwargs: {
            "answer": "tavily answer",
            "sources": [{"title": "source", "url": "https://example.com", "provider": "tavily"}],
        },
    )

    web_research.command_search(search_args(), web_research.Config({}))

    output = json.loads(capsys.readouterr().out)
    assert output["answer"] == "tavily answer"
    assert output["sources_count"] == 1


def test_search_calls_tavily_when_ai_is_unconfigured(monkeypatch, capsys):
    monkeypatch.setattr(web_research, "save_session", lambda _cfg, _payload: "abc123abc123abcd")
    monkeypatch.setattr(web_research, "ai_search", lambda _cfg, _query, **_kwargs: None)
    monkeypatch.setattr(
        web_research,
        "tavily_search",
        lambda *_args, **_kwargs: {
            "answer": "tavily answer",
            "sources": [{"title": "source", "url": "https://example.com", "provider": "tavily"}],
        },
    )

    web_research.command_search(search_args(), web_research.Config({}))

    output = json.loads(capsys.readouterr().out)
    assert output["answer"] == "tavily answer"
    assert output["sources_count"] == 1
    assert output["warnings"] == ["AI provider not configured; using search fallbacks."]


def test_search_priority_can_try_exa_before_tavily_and_grok(monkeypatch, capsys):
    monkeypatch.setattr(web_research, "save_session", lambda _cfg, _payload: "abc123abc123abcd")
    monkeypatch.setattr(web_research, "ai_search", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("grok should not be called")))
    monkeypatch.setattr(web_research, "tavily_search", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("tavily should not be called")))
    monkeypatch.setattr(
        web_research,
        "exa_search",
        lambda *_args, **_kwargs: {
            "answer": "exa answer",
            "sources": [{"title": "source", "url": "https://example.com", "provider": "exa"}],
        },
    )

    web_research.command_search(
        search_args(),
        web_research.Config({"search_provider_priority": ["exa", "tavily", "grok"]}),
    )

    output = json.loads(capsys.readouterr().out)
    assert output["answer"] == "exa answer"
    assert output["sources_count"] == 1


def test_search_priority_without_grok_or_tavily_disables_them(monkeypatch, capsys):
    monkeypatch.setattr(web_research, "save_session", lambda _cfg, _payload: "abc123abc123abcd")
    monkeypatch.setattr(web_research, "ai_search", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("grok should not be called")))
    monkeypatch.setattr(web_research, "tavily_search", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("tavily should not be called")))
    monkeypatch.setattr(web_research, "exa_search", lambda *_args, **_kwargs: {"answer": "", "sources": []})

    web_research.command_search(
        search_args(),
        web_research.Config({"search_provider_priority": ["exa"]}),
    )

    output = json.loads(capsys.readouterr().out)
    assert output["answer"] == "No answer text returned."
    assert output["sources_count"] == 0
    assert output["warnings"] == ["Exa returned no usable results."]


def test_search_calls_duckduckgo_after_exa_returns_no_results(monkeypatch, capsys):
    monkeypatch.setattr(web_research, "save_session", lambda _cfg, _payload: "abc123abc123abcd")
    monkeypatch.setattr(web_research, "ai_search", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("grok should not be called")))
    monkeypatch.setattr(web_research, "tavily_search", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("tavily should not be called")))
    monkeypatch.setattr(web_research, "exa_search", lambda *_args, **_kwargs: {"answer": "", "sources": [], "provider": "exa"})
    monkeypatch.setattr(
        web_research,
        "duckduckgo_search",
        lambda *_args, **_kwargs: {
            "answer": "duck answer",
            "sources": [{"title": "Duck", "url": "https://example.com/duck", "provider": "duckduckgo"}],
            "provider": "duckduckgo",
        },
    )

    web_research.command_search(
        search_args(),
        web_research.Config({"search_provider_priority": ["exa", "duckduckgo", "unknown"]}),
    )

    output = json.loads(capsys.readouterr().out)
    assert output["answer"] == "duck answer"
    assert output["sources_count"] == 1
    assert output["sources"][0]["provider"] == "duckduckgo"
    assert output["warnings"] == ["Exa returned no usable results; using DuckDuckGo fallback."]


def test_search_calls_tavily_when_urlopen_wraps_timeout(monkeypatch, capsys):
    monkeypatch.setattr(web_research, "save_session", lambda _cfg, _payload: "abc123abc123abcd")
    monkeypatch.setattr(
        web_research,
        "ai_search",
        lambda _cfg, _query: (_ for _ in ()).throw(urllib.error.URLError(TimeoutError("timed out"))),
    )
    monkeypatch.setattr(
        web_research,
        "tavily_search",
        lambda *_args, **_kwargs: {
            "answer": "tavily answer",
            "sources": [{"title": "source", "url": "https://example.com", "provider": "tavily"}],
        },
    )

    web_research.command_search(search_args(), web_research.Config({}))

    output = json.loads(capsys.readouterr().out)
    assert output["answer"] == "tavily answer"
    assert output["sources_count"] == 1


def test_search_retries_ai_errors_before_tavily_fallback(monkeypatch, capsys):
    calls = []
    args = search_args()
    args.grok_max_retries = 2
    monkeypatch.setattr(web_research, "save_session", lambda _cfg, _payload: "abc123abc123abcd")

    def fail_ai(_cfg, _query, **_kwargs):
        calls.append("ai")
        raise web_research.HttpError(429, "rate limited")

    monkeypatch.setattr(web_research, "ai_search", fail_ai)
    monkeypatch.setattr(
        web_research,
        "tavily_search",
        lambda *_args, **_kwargs: {
            "answer": "tavily answer",
            "sources": [{"title": "source", "url": "https://example.com", "provider": "tavily"}],
        },
    )

    web_research.command_search(args, web_research.Config({}))

    output = json.loads(capsys.readouterr().out)
    assert len(calls) == 3
    assert output["answer"] == "tavily answer"
    assert output["sources_count"] == 1
    assert output["warnings"] == [
        "AI provider attempt 1/3 failed: rate limited",
        "AI provider attempt 2/3 failed: rate limited",
        "AI provider attempt 3/3 failed: rate limited",
        "Grok unavailable; using Tavily fallback.",
    ]


def test_tavily_extract_blank_content_is_no_content(monkeypatch):
    monkeypatch.setattr(web_research, "random_upstream", lambda _cfg, _provider: {"tavily_api_key": "tvly-test"})
    monkeypatch.setattr(web_research, "request_json", lambda *_args, **_kwargs: {"results": [{"raw_content": "   "}]})

    assert web_research.tavily_extract("https://example.com", web_research.Config({})) is None


def test_firecrawl_extract_blank_content_is_no_content(monkeypatch):
    monkeypatch.setattr(web_research, "random_upstream", lambda _cfg, _provider: {"firecrawl_api_key": "fc-test"})
    monkeypatch.setattr(web_research, "request_json", lambda *_args, **_kwargs: {"data": {"markdown": "\n\t"}})

    assert web_research.firecrawl_extract("https://example.com", web_research.Config({})) is None


def test_search_uses_config_retries_when_cli_arg_is_absent(monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(web_research, "save_session", lambda _cfg, _payload: "abc123abc123abcd")
    monkeypatch.setattr(
        web_research,
        "ai_search",
        lambda _cfg, _query, **_kwargs: calls.append("ai") or (_ for _ in ()).throw(TimeoutError("timed out")),
    )
    monkeypatch.setattr(
        web_research,
        "tavily_search",
        lambda *_args, **_kwargs: {
            "answer": "tavily answer",
            "sources": [{"title": "source", "url": "https://example.com", "provider": "tavily"}],
        },
    )

    web_research.command_search(search_args(), web_research.Config({"grok_search_max_retries": "2"}))

    output = json.loads(capsys.readouterr().out)
    assert len(calls) == 3
    assert output["answer"] == "tavily answer"


def test_search_cli_retries_override_config_retries(monkeypatch, capsys):
    calls = []
    args = search_args()
    args.grok_max_retries = 1
    monkeypatch.setattr(web_research, "save_session", lambda _cfg, _payload: "abc123abc123abcd")
    monkeypatch.setattr(
        web_research,
        "ai_search",
        lambda _cfg, _query, **_kwargs: calls.append("ai") or (_ for _ in ()).throw(TimeoutError("timed out")),
    )
    monkeypatch.setattr(
        web_research,
        "tavily_search",
        lambda *_args, **_kwargs: {
            "answer": "tavily answer",
            "sources": [{"title": "source", "url": "https://example.com", "provider": "tavily"}],
        },
    )

    web_research.command_search(args, web_research.Config({"grok_search_max_retries": "5"}))

    output = json.loads(capsys.readouterr().out)
    assert len(calls) == 2
    assert output["answer"] == "tavily answer"


def test_search_stops_retrying_after_ai_success(monkeypatch, capsys):
    calls = []
    args = search_args()
    args.grok_max_retries = 3
    monkeypatch.setattr(web_research, "save_session", lambda _cfg, _payload: "abc123abc123abcd")

    def flaky_ai(_cfg, _query, **_kwargs):
        calls.append("ai")
        if len(calls) == 1:
            raise TimeoutError("timed out")
        return {"answer": "ai answer", "urls": ["https://example.com"]}

    monkeypatch.setattr(web_research, "ai_search", flaky_ai)

    def fail_tavily(*_args, **_kwargs):
        raise AssertionError("tavily should not be called when AI retry succeeds")

    monkeypatch.setattr(web_research, "tavily_search", fail_tavily)

    web_research.command_search(args, web_research.Config({}))

    output = json.loads(capsys.readouterr().out)
    assert len(calls) == 2
    assert output["answer"] == "ai answer"
    assert output["sources_count"] == 1
    assert len(output["warnings"]) == 1


def test_main_writes_utf8_output_when_stdout_defaults_to_gbk(monkeypatch, tmp_path):
    stdout_bytes = io.BytesIO()
    stderr_bytes = io.BytesIO()
    monkeypatch.setattr(web_research.sys, "stdout", io.TextIOWrapper(stdout_bytes, encoding="gbk"))
    monkeypatch.setattr(web_research.sys, "stderr", io.TextIOWrapper(stderr_bytes, encoding="gbk"))
    monkeypatch.setattr(web_research, "load_file_config", lambda: {"search_cache_dir": str(tmp_path)})
    monkeypatch.setattr(web_research, "save_session", lambda _cfg, _payload: "abc123abc123abcd")
    monkeypatch.setattr(
        web_research,
        "ai_search",
        lambda _cfg, _query, **_kwargs: {"answer": "emoji 馃槀 answer", "urls": ["https://example.com"]},
    )

    assert web_research.main(["web_search", "--query", "q"]) == 0

    web_research.sys.stdout.flush()
    output = stdout_bytes.getvalue().decode("utf-8")
    assert "emoji 馃槀 answer" in output


