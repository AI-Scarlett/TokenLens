import json
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional


HOME = Path.home()
CUSTOM_SCANNERS_PATH = Path(__file__).parent / "custom_scanners.json"

AI_KEYWORDS = {
    "model", "models", "llm", "provider", "providers", "base_url", "baseurl",
    "api_key", "apikey", "api_url", "tokenizer", "max_tokens", "context_window",
    "embedding", "completion", "chat", "openai", "anthropic", "claude", "gpt",
    "gemini", "llama", "mistral", "qwen", "glm", "deepseek", "minimax",
    "codestral", "coding", "agent", "ai", "llm_provider",
}

CONFIG_FILENAMES = {
    "config.json", "config.yaml", "config.toml", "settings.json",
    "models.json", "providers.json", "llm.json", "ai.json",
    "openclaw.json", "mcp.json", "auth.json",
}

IGNORE_DIRS = {
    "node_modules", ".git", ".svn", "__pycache__", ".cache", ".npm",
    ".Trash", "Desktop", "Documents", "Downloads", "Pictures", "Music",
    "Movies", "Public", "Games", ".docker", ".rustup", ".cargo",
    ".gradle", ".m2", ".ivy2", ".sbt", ".venv", "venv", "env",
    ".tox", ".pytest_cache", ".hg", ".idea", ".vscode",
}


def _load_json_safe(path: Path) -> Optional[Dict]:
    if not path.exists():
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _load_toml_safe(path: Path) -> Optional[Dict]:
    if not path.exists():
        return None
    try:
        import tomllib
        with open(path, "rb") as f:
            return tomllib.load(f)
    except ImportError:
        pass
    try:
        import tomli
        with open(path, "rb") as f:
            return tomli.load(f)
    except ImportError:
        pass
    try:
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
        result = {}
        current_section = result
        for line in content.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("[") and line.endswith("]"):
                section_name = line[1:-1].strip()
                parts = section_name.split(".")
                current_section = result
                for part in parts:
                    if part not in current_section:
                        current_section[part] = {}
                    current_section = current_section[part]
            elif "=" in line:
                key, _, value = line.partition("=")
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                current_section[key] = value
        return result
    except Exception:
        return None


def _load_yaml_safe(path: Path) -> Optional[Dict]:
    if not path.exists():
        return None
    try:
        import yaml
        with open(path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f)
    except ImportError:
        return None


def _load_config(path: Path) -> Optional[Dict]:
    suffix = path.suffix.lower()
    if suffix == ".json":
        return _load_json_safe(path)
    elif suffix == ".toml":
        return _load_toml_safe(path)
    elif suffix in (".yaml", ".yml"):
        return _load_yaml_safe(path)
    return None


def _extract_models_from_claude_code() -> List[Dict]:
    models = []
    settings = _load_json_safe(HOME / ".claude" / "settings.json")
    if not settings:
        return models
    model_name = settings.get("model", "")
    if model_name:
        env = settings.get("env", {})
        base_url = env.get("ANTHROPIC_BASE_URL", "")
        models.append({
            "id": model_name, "vendor": "claude-code",
            "vendor_display": "Claude Code", "source": "claude-code",
            "base_url": base_url, "is_default": True,
        })
    env = settings.get("env", {})
    env_model = env.get("ANTHROPIC_MODEL", "")
    if env_model and env_model != model_name:
        base_url = env.get("ANTHROPIC_BASE_URL", "")
        models.append({
            "id": env_model, "vendor": "claude-code",
            "vendor_display": "Claude Code", "source": "claude-code",
            "base_url": base_url, "is_default": False,
        })
    return models


def _extract_models_from_codex() -> List[Dict]:
    models = []
    config = _load_toml_safe(HOME / ".codex" / "config.toml")
    if not config:
        return models
    model_name = config.get("model", "")
    if model_name:
        models.append({
            "id": model_name, "vendor": "openai",
            "vendor_display": "OpenAI Codex", "source": "codex",
            "base_url": "", "is_default": True,
        })
    state = _load_json_safe(HOME / ".codex" / ".codex-global-state.json")
    if state:
        credentials = state.get("credentialPool", {})
        for pool_name, pool_data in credentials.items():
            if isinstance(pool_data, dict):
                pool_model = pool_data.get("model", "")
                if pool_model and pool_model != model_name:
                    models.append({
                        "id": pool_model, "vendor": pool_name,
                        "vendor_display": pool_name, "source": "codex",
                        "base_url": pool_data.get("baseUrl", ""),
                        "is_default": False,
                    })
    return models


def _extract_models_from_codebuddy() -> List[Dict]:
    models = []
    config = _load_json_safe(HOME / ".codebuddy" / "models.json")
    if not config:
        return models
    model_list = config.get("models", [])
    for m in model_list:
        mid = m.get("id", "")
        if not mid:
            continue
        models.append({
            "id": mid, "vendor": m.get("vendor", "unknown").lower(),
            "vendor_display": m.get("vendor", "Unknown"), "source": "codebuddy",
            "base_url": m.get("url", ""), "is_default": False,
        })
    return models


def _extract_models_from_qwen_code() -> List[Dict]:
    models = []
    settings = _load_json_safe(HOME / ".qwen" / "settings.json")
    if not settings:
        return models
    default_model = settings.get("model", {}).get("name", "")
    providers = settings.get("modelProviders", {})
    for provider_name, model_list in providers.items():
        if not isinstance(model_list, list):
            continue
        for m in model_list:
            mid = m.get("id", "")
            if not mid:
                continue
            models.append({
                "id": mid, "vendor": provider_name,
                "vendor_display": provider_name, "source": "qwen-code",
                "base_url": m.get("baseUrl", ""),
                "is_default": mid == default_model,
            })
    return models


def _extract_models_from_hermes() -> List[Dict]:
    models = []
    config = _load_yaml_safe(HOME / ".hermes" / "config.yaml")
    if not config:
        return models
    model_cfg = config.get("model", {})
    default_model = model_cfg.get("default", "")
    if default_model:
        models.append({
            "id": default_model, "vendor": model_cfg.get("provider", "unknown"),
            "vendor_display": "Hermes", "source": "hermes",
            "base_url": model_cfg.get("base_url", ""), "is_default": True,
        })
    fallbacks = config.get("fallback_providers", [])
    for fb in fallbacks:
        fb_model = fb.get("model", "")
        if fb_model and fb_model != default_model:
            models.append({
                "id": fb_model, "vendor": fb.get("provider", "unknown"),
                "vendor_display": "Hermes Fallback", "source": "hermes",
                "base_url": fb.get("base_url", ""), "is_default": False,
            })
    return models


def _extract_models_from_qclaw() -> List[Dict]:
    models = []
    config = _load_json_safe(HOME / ".qclaw" / "openclaw.json")
    if not config:
        return models
    default_model = config.get("agents", {}).get("defaults", {}).get("model", {}).get("primary", "")
    providers = config.get("models", {}).get("providers", {})
    for provider_name, provider_data in providers.items():
        if not isinstance(provider_data, dict):
            continue
        base_url = provider_data.get("baseUrl", "")
        model_list = provider_data.get("models", [])
        for m in model_list:
            mid = m.get("id", "") if isinstance(m, dict) else str(m)
            if not mid:
                continue
            full_id = f"{provider_name}/{mid}" if "/" not in mid else mid
            is_default = full_id == default_model or mid == default_model
            models.append({
                "id": mid, "vendor": provider_name,
                "vendor_display": provider_name, "source": "qclaw",
                "base_url": base_url, "is_default": is_default,
            })
    return models


def _extract_models_from_kimi() -> List[Dict]:
    models = []
    config = _load_toml_safe(HOME / ".kimi" / "config.toml")
    if not config:
        return models
    model_name = config.get("default_model", "")
    if model_name:
        models.append({
            "id": model_name, "vendor": "kimi",
            "vendor_display": "Kimi Code", "source": "kimi-code",
            "base_url": "", "is_default": True,
        })
    models_section = config.get("models", {})
    if isinstance(models_section, dict):
        for key, val in models_section.items():
            if isinstance(val, str) and val and val != model_name:
                models.append({
                    "id": val, "vendor": "kimi",
                    "vendor_display": "Kimi Code", "source": "kimi-code",
                    "base_url": "", "is_default": False,
                })
    return models


def _extract_models_from_evomorph() -> List[Dict]:
    models = []
    config = _load_json_safe(HOME / ".evomorph" / "config.json")
    if not config:
        return models
    model_name = config.get("model", "")
    if model_name:
        models.append({
            "id": model_name, "vendor": "evomorph",
            "vendor_display": "EvoMorph", "source": "evomorph",
            "base_url": config.get("api_url", ""), "is_default": True,
        })
    return models


def _extract_models_from_cline() -> List[Dict]:
    models = []
    base = HOME / "Library" / "Application Support" / "Code" / "User" / "globalStorage" / "saoudrizwan.claude-dev"
    hicap = _load_json_safe(base / "cache" / "hicap_models.json")
    if hicap and isinstance(hicap, list):
        for m in hicap:
            mid = m.get("id", "") or m.get("name", "")
            if not mid:
                continue
            models.append({
                "id": mid, "vendor": "cline",
                "vendor_display": "Cline", "source": "cline",
                "base_url": "", "is_default": False,
                "max_tokens": m.get("maxTokens") or m.get("contextWindow"),
            })
    return models


BUILTIN_SCANNERS = {
    "claude-code": {
        "name": "Claude Code", "description": "Anthropic Claude Code CLI Agent", "icon": "🤖",
        "extract": _extract_models_from_claude_code,
        "check": lambda: (HOME / ".claude" / "settings.json").exists(),
    },
    "codex": {
        "name": "OpenAI Codex", "description": "OpenAI Codex CLI Agent", "icon": "⚡",
        "extract": _extract_models_from_codex,
        "check": lambda: (HOME / ".codex" / "config.toml").exists(),
    },
    "codebuddy": {
        "name": "CodeBuddy", "description": "CodeBuddy AI Coding Assistant", "icon": "🦾",
        "extract": _extract_models_from_codebuddy,
        "check": lambda: (HOME / ".codebuddy" / "models.json").exists(),
    },
    "qwen-code": {
        "name": "Qwen Code", "description": "通义灵码 Qwen Code Agent", "icon": "🔮",
        "extract": _extract_models_from_qwen_code,
        "check": lambda: (HOME / ".qwen" / "settings.json").exists(),
    },
    "hermes": {
        "name": "Hermes", "description": "Hermes AI Agent (Nous Research)", "icon": "🧙",
        "extract": _extract_models_from_hermes,
        "check": lambda: (HOME / ".hermes" / "config.yaml").exists(),
    },
    "qclaw": {
        "name": "QClaw", "description": "QClaw (开爪) AI Agent", "icon": "🐾",
        "extract": _extract_models_from_qclaw,
        "check": lambda: (HOME / ".qclaw" / "openclaw.json").exists(),
    },
    "kimi-code": {
        "name": "Kimi Code", "description": "Kimi Code Agent (月之暗面)", "icon": "🌙",
        "extract": _extract_models_from_kimi,
        "check": lambda: (HOME / ".kimi" / "config.toml").exists(),
    },
    "evomorph": {
        "name": "EvoMorph", "description": "EvoMorph 易衍 Agent", "icon": "🧬",
        "extract": _extract_models_from_evomorph,
        "check": lambda: (HOME / ".evomorph" / "config.json").exists(),
    },
    "cline": {
        "name": "Cline", "description": "Cline VS Code Extension", "icon": "🔌",
        "extract": _extract_models_from_cline,
        "check": lambda: (HOME / "Library" / "Application Support" / "Code" / "User" / "globalStorage" / "saoudrizwan.claude-dev" / "cache" / "hicap_models.json").exists(),
    },
}


def _is_valid_model_id(mid: str) -> bool:
    if not mid or len(mid) < 3 or len(mid) > 80:
        return False
    if re.match(r'^[0-9a-f]{4,}$', mid):
        return False
    if mid.lower() in ('base', 'main', 'default', 'none', 'null', 'true', 'false',
                        'unknown', 'custom', 'auto', 'test', 'local', 'remote',
                        'openai', 'anthropic', 'google', 'meta', 'mistral',
                        'minimax', 'minimax-cn', 'minimax-portal-auth',
                        'deepseek', 'zhipu', 'baidu', 'alibaba', 'moonshot',
                        'baichuan', 'iflytek', 'sensetime', 'stepfun', 'volcengine',
                        'chatgpt', 'chatgpt.com', 'minimax.io',
                        'bearer_token', 'x-api-key', 'access_token',
                        'openai-compat', 'anthropic-messages', 'google-gemini',
                        'coding', 'chat', 'completion', 'embedding'):
        return False
    if '.' in mid and re.match(r'^[a-z0-9.-]+\.(com|io|cn|org|net|dev)$', mid, re.IGNORECASE):
        return False
    if mid.startswith('http://') or mid.startswith('https://'):
        return False
    if mid.startswith('$') or mid.startswith('{'):
        return False
    if re.match(r'^[0-9a-f]{8}-[0-9a-f]{4}-', mid, re.IGNORECASE):
        return False
    return True


def _normalize_model_id(mid: str) -> str:
    return re.sub(r'[\s_]+', '-', mid.strip().lower())


def _extract_model_ids_from_value(val, depth=0) -> List[str]:
    if depth > 4:
        return []
    results = []
    if isinstance(val, str):
        val = val.strip()
        if not val or len(val) > 200:
            return results
        model_patterns = [
            r'\b(gpt-[\d.]+-\w+|gpt-[\d.]+|gpt4\w*|gpt-4o\w*)\b',
            r'\b(o[134]-\w+-\d+|o[134]-\w+|o[134]\w*)\b',
            r'\b(claude-[\d.]+\w*|claude-\w+-[\d.]+\w*)\b',
            r'\b(gemini-[\d.]*-\w+|gemini[\d.]*-\w+)\b',
            r'\b(llama-[\d.]+\w*|llama[\d]-\w+)\b',
            r'\b(mistral-\w+-\w+|mistral-\w+)\b',
            r'\b(qwen[\d.]*-\w+-\w+|qwen[\d.]*-\w+)\b',
            r'\b(glm-[\d.]+\w*|glm-[\d]\w+)\b',
            r'\b(deepseek-\w+-\w+|deepseek-\w+)\b',
            r'\b(MiniMax-M[\d.]+\w*|minimax-m[\d.]+\w*)\b',
            r'\b(moonshot-v[\d.]+-\w+)\b',
            r'\b(ernie-[\d.]+\w*|ernie-\w+)\b',
            r'\b(spark-\w+-\d+|spark-\w+)\b',
            r'\b(baichuan[\d.]*-\w+)\b',
            r'\b(doubao-\w+-\d+\w*)\b',
            r'\b(sensechat-[\d.]+\w*)\b',
            r'\b(step-[\d]+-\w+)\b',
            r'\b(kimi-k[\d.]+\w*|kimi-k\w+)\b',
            r'\b(codestral-\w+)\b',
            r'\b(command-r\w*-\w+|command-r\w*)\b',
            r'\b(mixtral-\w+-\w+)\b',
            r'\b(pool-\w+-[\w.]+)\b',
            r'\b(gemma-[\d.]*-\w+)\b',
        ]
        for pat in model_patterns:
            matches = re.findall(pat, val, re.IGNORECASE)
            for m in matches:
                if _is_valid_model_id(m):
                    results.append(m)
    elif isinstance(val, dict):
        model_keys = {"model", "default_model", "default", "name", "id"}
        for k, v in val.items():
            if k in model_keys:
                if isinstance(v, str) and v and _is_valid_model_id(v):
                    results.append(v)
            results.extend(_extract_model_ids_from_value(v, depth + 1))
    elif isinstance(val, list):
        for item in val:
            if isinstance(item, dict):
                mid = item.get("id", "") or item.get("name", "") or item.get("model", "")
                if mid and isinstance(mid, str) and _is_valid_model_id(mid):
                    results.append(mid)
            results.extend(_extract_model_ids_from_value(item, depth + 1))
    return results


def _is_ai_config(data: Any, depth=0) -> bool:
    if depth > 3:
        return False
    if isinstance(data, dict):
        keys_lower = {k.lower() for k in data.keys()}
        overlap = keys_lower & AI_KEYWORDS
        if len(overlap) >= 2:
            return True
        for v in data.values():
            if _is_ai_config(v, depth + 1):
                return True
    elif isinstance(data, list):
        for item in data:
            if _is_ai_config(item, depth + 1):
                return True
    return False


def _scan_directory_for_ai_configs(dir_path: Path, max_depth=2, current_depth=0) -> List[Dict]:
    results = []
    if current_depth > max_depth or not dir_path.exists():
        return results
    try:
        entries = list(dir_path.iterdir())
    except PermissionError:
        return results

    for entry in entries:
        if entry.name.startswith(".") and current_depth > 0:
            continue
        if entry.name in IGNORE_DIRS:
            continue
        if entry.is_dir():
            results.extend(_scan_directory_for_ai_configs(entry, max_depth, current_depth + 1))
        elif entry.is_file() and entry.name in CONFIG_FILENAMES:
            data = _load_config(entry)
            if data and _is_ai_config(data):
                model_ids = _extract_model_ids_from_value(data)
                model_ids = list(dict.fromkeys(model_ids))
                if model_ids:
                    dir_name = entry.parent.name
                    results.append({
                        "dir_name": dir_name,
                        "config_path": str(entry),
                        "models": model_ids,
                    })
    return results


def _extract_models_from_generic() -> List[Dict]:
    all_models = []

    builtin_config_paths = set()
    for s in BUILTIN_SCANNERS.values():
        try:
            if s["check"]():
                builtin_config_paths.add(str(HOME))
        except Exception:
            pass

    builtin_model_ids = set()
    for s in BUILTIN_SCANNERS.values():
        try:
            if s["check"]():
                for m in s["extract"]():
                    builtin_model_ids.add(_normalize_model_id(m["id"]))
        except Exception:
            pass

    known_agent_dirs = {
        ".claude", ".codex", ".codebuddy", ".qwen", ".hermes",
        ".qclaw", ".kimi", ".evomorph", ".lmstudio",
    }

    scan_dirs = []
    if HOME.exists():
        for entry in HOME.iterdir():
            if entry.name.startswith(".") and entry.is_dir():
                if entry.name in IGNORE_DIRS or entry.name in known_agent_dirs:
                    continue
                scan_dirs.append(entry)
        mac_support = HOME / "Library" / "Application Support"
        if mac_support.exists():
            for entry in mac_support.iterdir():
                if entry.is_dir() and entry.name not in IGNORE_DIRS:
                    if entry.name.lower() not in ("claude", "codebuddy", "cursor"):
                        scan_dirs.append(entry)
        linux_config = HOME / ".config"
        if linux_config.exists():
            for entry in linux_config.iterdir():
                if entry.is_dir() and entry.name not in IGNORE_DIRS:
                    scan_dirs.append(entry)

    discovered = []
    for d in scan_dirs:
        found = _scan_directory_for_ai_configs(d, max_depth=1)
        discovered.extend(found)

    seen_normalized = set(builtin_model_ids)
    for disc in discovered:
        dir_name = disc["dir_name"]
        for mid in disc["models"]:
            norm = _normalize_model_id(mid)
            if norm in seen_normalized:
                continue
            seen_normalized.add(norm)
            all_models.append({
                "id": mid,
                "vendor": dir_name,
                "vendor_display": dir_name,
                "source": "generic",
                "base_url": "",
                "is_default": False,
                "config_path": disc["config_path"],
            })
    return all_models


def _load_custom_scanners() -> Dict:
    if CUSTOM_SCANNERS_PATH.exists():
        data = _load_json_safe(CUSTOM_SCANNERS_PATH)
        if data and isinstance(data, dict):
            return data
    return {}


def _save_custom_scanners(scanners: Dict):
    with open(CUSTOM_SCANNERS_PATH, "w", encoding="utf-8") as f:
        json.dump(scanners, f, indent=2, ensure_ascii=False)


def _extract_models_from_custom(scanner: Dict) -> List[Dict]:
    models = []
    config_path = Path(scanner.get("config_path", "")).expanduser()
    model_field = scanner.get("model_field", "model")
    vendor_name = scanner.get("vendor_name", scanner.get("id", "custom"))
    base_url_field = scanner.get("base_url_field", "")

    data = _load_config(config_path)
    if not data:
        return models

    if model_field == "__auto__":
        model_ids = _extract_model_ids_from_value(data)
        model_ids = list(dict.fromkeys(model_ids))
        for mid in model_ids:
            models.append({
                "id": mid, "vendor": vendor_name,
                "vendor_display": scanner.get("name", vendor_name),
                "source": scanner.get("id", "custom"),
                "base_url": "", "is_default": False,
            })
    else:
        parts = model_field.split(".")
        val = data
        for p in parts:
            if isinstance(val, dict):
                val = val.get(p)
            elif isinstance(val, list) and p.isdigit():
                idx = int(p)
                val = val[idx] if idx < len(val) else None
            else:
                val = None
                break

        if isinstance(val, str) and val:
            models.append({
                "id": val, "vendor": vendor_name,
                "vendor_display": scanner.get("name", vendor_name),
                "source": scanner.get("id", "custom"),
                "base_url": "", "is_default": True,
            })
        elif isinstance(val, list):
            for item in val:
                if isinstance(item, str) and item:
                    models.append({
                        "id": item, "vendor": vendor_name,
                        "vendor_display": scanner.get("name", vendor_name),
                        "source": scanner.get("id", "custom"),
                        "base_url": "", "is_default": False,
                    })
                elif isinstance(item, dict):
                    mid = item.get("id", "") or item.get("name", "") or item.get("model", "")
                    if mid:
                        bu = ""
                        if base_url_field:
                            bu = item.get(base_url_field, "")
                        models.append({
                            "id": mid, "vendor": vendor_name,
                            "vendor_display": scanner.get("name", vendor_name),
                            "source": scanner.get("id", "custom"),
                            "base_url": bu, "is_default": False,
                        })
    return models


def get_all_scanners() -> Dict:
    scanners = dict(BUILTIN_SCANNERS)
    custom = _load_custom_scanners()
    for sid, sdef in custom.items():
        scanner_copy = dict(sdef)
        sdef_ref = sdef
        scanner_copy["extract"] = lambda s=sdef_ref: _extract_models_from_custom(s)
        config_path = Path(sdef.get("config_path", "")).expanduser()
        scanner_copy["check"] = lambda p=config_path: p.exists()
        scanner_copy["is_custom"] = True
        scanners[sid] = scanner_copy
    return scanners


def scan_agents() -> List[Dict[str, Any]]:
    results = []
    scanners = get_all_scanners()

    for agent_id, agent_info in scanners.items():
        installed = agent_info["check"]()
        models = agent_info["extract"]() if installed else []
        results.append({
            "id": agent_id,
            "name": agent_info["name"],
            "description": agent_info["description"],
            "icon": agent_info.get("icon", "📦"),
            "installed": installed,
            "model_count": len(models),
            "models": models,
            "is_custom": agent_info.get("is_custom", False),
        })

    generic_models = _extract_models_from_generic()
    if generic_models:
        known_model_ids = set()
        for r in results:
            for m in r.get("models", []):
                known_model_ids.add(m["id"].lower())

        new_models = [m for m in generic_models if m["id"].lower() not in known_model_ids]
        if new_models:
            results.append({
                "id": "generic",
                "name": "其他已发现Agent",
                "description": "自动扫描发现的未识别Agent配置",
                "icon": "🔍",
                "installed": True,
                "model_count": len(new_models),
                "models": new_models,
                "is_custom": False,
            })

    return results


def scan_available_models() -> List[Dict[str, Any]]:
    all_models = []
    seen = set()
    for agent_info in get_all_scanners().values():
        if not agent_info["check"]():
            continue
        for m in agent_info["extract"]():
            key = m["id"]
            if key not in seen:
                seen.add(key)
                all_models.append(m)
    return all_models


def add_custom_scanner(scanner_id: str, name: str, description: str,
                       config_path: str, model_field: str = "__auto__",
                       vendor_name: str = "", icon: str = "📦",
                       base_url_field: str = "") -> Dict:
    scanners = _load_custom_scanners()
    scanners[scanner_id] = {
        "id": scanner_id,
        "name": name,
        "description": description,
        "config_path": config_path,
        "model_field": model_field,
        "vendor_name": vendor_name or scanner_id,
        "icon": icon,
        "base_url_field": base_url_field,
    }
    _save_custom_scanners(scanners)
    return scanners[scanner_id]


def remove_custom_scanner(scanner_id: str) -> bool:
    scanners = _load_custom_scanners()
    if scanner_id in scanners:
        del scanners[scanner_id]
        _save_custom_scanners(scanners)
        return True
    return False


def list_custom_scanners() -> List[Dict]:
    scanners = _load_custom_scanners()
    result = []
    for sid, sdef in scanners.items():
        entry = dict(sdef)
        entry["id"] = sid
        config_path = Path(sdef.get("config_path", "")).expanduser()
        entry["installed"] = config_path.exists()
        result.append(entry)
    return result
