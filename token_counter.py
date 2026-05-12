import json
import os
import sqlite3
import unicodedata
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional


_MODELS_FILE = Path(__file__).parent / "models.json"
_CUSTOM_MODELS_FILE = Path(__file__).parent / "custom_models.json"
_CUSTOM_VENDORS_FILE = Path(__file__).parent / "custom_vendors.json"
_ENABLED_FILE = Path(__file__).parent / "enabled_models.json"
_ENABLED_VENDORS_FILE = Path(__file__).parent / "enabled_vendors.json"
_DB_DIR = Path(__file__).parent / "data"
_DB_PATH = _DB_DIR / "token_stats.db"

_TIKTOKEN_CACHE: Dict[str, Any] = {}
_MODELS_CACHE: Optional[Dict[str, Any]] = None
_VENDORS_CACHE: Optional[Dict[str, Any]] = None
_PLANS_CACHE: Optional[Dict[str, Any]] = None
_ENABLED_CACHE: Optional[Dict[str, bool]] = None
_ENABLED_VENDORS_CACHE: Optional[Dict[str, bool]] = None


def _load_json(path: Path, default: Any = None) -> Any:
    if path.exists():
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return default if default is not None else {}


def _save_json(path: Path, data: Any) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _load_models() -> Dict[str, Any]:
    global _MODELS_CACHE
    if _MODELS_CACHE is not None:
        return _MODELS_CACHE
    raw = _load_json(_MODELS_FILE, {})
    built_in = raw.get("models", {})
    custom = _load_json(_CUSTOM_MODELS_FILE, {})
    _MODELS_CACHE = {**built_in, **custom}
    return _MODELS_CACHE


def _load_vendors() -> Dict[str, Any]:
    global _VENDORS_CACHE
    if _VENDORS_CACHE is not None:
        return _VENDORS_CACHE
    raw = _load_json(_MODELS_FILE, {})
    built_in = raw.get("vendors", {})
    custom = _load_json(_CUSTOM_VENDORS_FILE, {})
    _VENDORS_CACHE = {**built_in, **custom}
    return _VENDORS_CACHE


def _load_plans() -> Dict[str, Any]:
    global _PLANS_CACHE
    if _PLANS_CACHE is not None:
        return _PLANS_CACHE
    raw = _load_json(_MODELS_FILE, {})
    _PLANS_CACHE = raw.get("plans", {})
    return _PLANS_CACHE


def _load_enabled() -> Dict[str, bool]:
    global _ENABLED_CACHE
    if _ENABLED_CACHE is not None:
        return _ENABLED_CACHE
    _ENABLED_CACHE = _load_json(_ENABLED_FILE, {})
    return _ENABLED_CACHE


def _save_enabled(data: Dict[str, bool]) -> None:
    global _ENABLED_CACHE
    _save_json(_ENABLED_FILE, data)
    _ENABLED_CACHE = data


def _load_enabled_vendors() -> Dict[str, bool]:
    global _ENABLED_VENDORS_CACHE
    if _ENABLED_VENDORS_CACHE is not None:
        return _ENABLED_VENDORS_CACHE
    _ENABLED_VENDORS_CACHE = _load_json(_ENABLED_VENDORS_FILE, {})
    return _ENABLED_VENDORS_CACHE


def _save_enabled_vendors(data: Dict[str, bool]) -> None:
    global _ENABLED_VENDORS_CACHE
    _save_json(_ENABLED_VENDORS_FILE, data)
    _ENABLED_VENDORS_CACHE = data


def _invalidate_cache() -> None:
    global _MODELS_CACHE, _VENDORS_CACHE, _PLANS_CACHE, _ENABLED_CACHE, _ENABLED_VENDORS_CACHE
    _MODELS_CACHE = None
    _VENDORS_CACHE = None
    _PLANS_CACHE = None
    _ENABLED_CACHE = None
    _ENABLED_VENDORS_CACHE = None


def _is_model_enabled(model_name: str) -> bool:
    models = _load_models()
    model_config = models.get(model_name, {})
    vendor_id = model_config.get("vendor", "")

    enabled_vendors = _load_enabled_vendors()
    if enabled_vendors and not enabled_vendors.get(vendor_id, False):
        return False

    enabled_map = _load_enabled()
    if not enabled_map:
        return True
    return enabled_map.get(model_name, False)


def _get_enabled_models() -> List[str]:
    models = _load_models()
    enabled_vendors = _load_enabled_vendors()
    enabled_map = _load_enabled()

    result = []
    for name in models:
        vendor_id = models[name].get("vendor", "")
        if enabled_vendors and not enabled_vendors.get(vendor_id, False):
            continue
        if enabled_map and not enabled_map.get(name, False):
            continue
        result.append(name)
    return result


def resolve_time_range(period: Optional[str] = None) -> Dict[str, str]:
    today = date.today()
    if period == "today":
        return {"date_from": today.isoformat(), "date_to": today.isoformat()}
    elif period == "7d":
        return {"date_from": (today - timedelta(days=6)).isoformat(), "date_to": today.isoformat()}
    elif period == "30d":
        return {"date_from": (today - timedelta(days=29)).isoformat(), "date_to": today.isoformat()}
    elif period == "90d":
        return {"date_from": (today - timedelta(days=89)).isoformat(), "date_to": today.isoformat()}
    return {}


def _get_db() -> sqlite3.Connection:
    _DB_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(_DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS token_usage (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            api_name TEXT NOT NULL,
            model TEXT NOT NULL,
            input_tokens INTEGER NOT NULL,
            output_tokens INTEGER NOT NULL,
            total_tokens INTEGER NOT NULL,
            metadata TEXT,
            agent TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_model ON token_usage(model)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_api ON token_usage(api_name)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_created ON token_usage(created_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_agent ON token_usage(agent)")
    try:
        conn.execute("ALTER TABLE token_usage ADD COLUMN agent TEXT DEFAULT ''")
    except Exception:
        pass
    conn.commit()
    return conn


def _get_tiktoken_encoder(encoding_name: str):
    if encoding_name in _TIKTOKEN_CACHE:
        return _TIKTOKEN_CACHE[encoding_name]
    try:
        import tiktoken
        enc = tiktoken.get_encoding(encoding_name)
        _TIKTOKEN_CACHE[encoding_name] = enc
        return enc
    except ImportError:
        raise RuntimeError(
            "tiktoken is required. Install it with: pip install tiktoken"
        )


def _estimate_tokens(text: str, chars_per_token_en: float = 4.0, chars_per_token_zh: float = 1.5) -> int:
    en_chars = 0
    zh_chars = 0
    for ch in text:
        try:
            if unicodedata.category(ch).startswith("Lo") or "\u4e00" <= ch <= "\u9fff":
                zh_chars += 1
            else:
                en_chars += 1
        except Exception:
            en_chars += 1
    estimated = en_chars / chars_per_token_en + zh_chars / chars_per_token_zh
    return max(1, int(estimated))


def _resolve_model_config(model: str) -> Dict[str, Any]:
    models = _load_models()
    if model in models:
        return models[model]
    for key in models:
        if model.startswith(key) or key.startswith(model):
            return models[key]
    return {
        "vendor": "custom",
        "tokenizer_type": "estimator",
        "tokenizer_config": {"chars_per_token_en": 4.0, "chars_per_token_zh": 1.5},
        "max_tokens": 4096,
        "note": f"Model '{model}' not found in registry; using estimator fallback",
    }


class TokenCounter:
    def __init__(self, db_path: Optional[str] = None):
        global _DB_PATH
        if db_path:
            _DB_PATH = Path(db_path)
        self._models = _load_models()

    def get_default_model(self) -> str:
        models = _load_models()
        if models:
            return next(iter(models))
        return "default"

    def count_tokens(self, text: str, model: Optional[str] = None) -> int:
        if not text:
            return 0
        if model is None:
            model = self.get_default_model()
        config = _resolve_model_config(model)
        tokenizer_type = config.get("tokenizer_type", "estimator")
        tokenizer_config = config.get("tokenizer_config", {})

        if tokenizer_type == "tiktoken":
            encoding_name = tokenizer_config.get("encoding_name", "cl100k_base")
            encoder = _get_tiktoken_encoder(encoding_name)
            return len(encoder.encode(text))
        elif tokenizer_type == "estimator":
            return _estimate_tokens(
                text,
                chars_per_token_en=tokenizer_config.get("chars_per_token_en", 4.0),
                chars_per_token_zh=tokenizer_config.get("chars_per_token_zh", 1.5),
            )
        else:
            return _estimate_tokens(text)

    def count_messages_tokens(self, messages: List[Dict[str, str]], model: Optional[str] = None) -> int:
        if model is None:
            model = self.get_default_model()
        total = 0
        for msg in messages:
            total += 4
            for key, value in msg.items():
                total += self.count_tokens(value, model=model)
                total += 1
            total += 2
        return total

    def record(
        self,
        api_name: str,
        model: str,
        input_tokens: int,
        output_tokens: int,
        metadata: Optional[Dict[str, Any]] = None,
        agent: str = "",
    ) -> int:
        conn = _get_db()
        cursor = conn.execute(
            """INSERT INTO token_usage (api_name, model, input_tokens, output_tokens, total_tokens, metadata, agent)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                api_name,
                model,
                input_tokens,
                output_tokens,
                input_tokens + output_tokens,
                json.dumps(metadata, ensure_ascii=False) if metadata else None,
                agent,
            ),
        )
        conn.commit()
        row_id = cursor.lastrowid
        conn.close()
        return row_id

    def record_text(
        self,
        api_name: str,
        model: str,
        input_text: str,
        output_text: str,
        metadata: Optional[Dict[str, Any]] = None,
        agent: str = "",
    ) -> Dict[str, Any]:
        input_tokens = self.count_tokens(input_text, model=model)
        output_tokens = self.count_tokens(output_text, model=model)
        row_id = self.record(
            api_name=api_name,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            metadata=metadata,
            agent=agent,
        )
        return {
            "id": row_id,
            "api_name": api_name,
            "model": model,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens,
            "agent": agent,
        }

    def _build_where(
        self,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        model_filter: Optional[str] = None,
        api_filter: Optional[str] = None,
        enabled_only: bool = False,
        period: Optional[str] = None,
        agent_filter: Optional[str] = None,
    ) -> tuple:
        if period:
            tr = resolve_time_range(period)
            if tr:
                if not date_from:
                    date_from = tr["date_from"]
                if not date_to:
                    date_to = tr["date_to"]

        where_clauses = []
        params: list = []
        if date_from:
            where_clauses.append("DATE(created_at) >= ?")
            params.append(date_from)
        if date_to:
            where_clauses.append("DATE(created_at) <= ?")
            params.append(date_to)
        if model_filter:
            where_clauses.append("model LIKE ?")
            params.append(f"%{model_filter}%")
        if api_filter:
            where_clauses.append("api_name LIKE ?")
            params.append(f"%{api_filter}%")
        if agent_filter:
            where_clauses.append("agent = ?")
            params.append(agent_filter)
        if enabled_only:
            enabled_list = _get_enabled_models()
            if enabled_list:
                placeholders = ",".join("?" for _ in enabled_list)
                where_clauses.append(f"model IN ({placeholders})")
                params.extend(enabled_list)

        where_sql = ""
        if where_clauses:
            where_sql = "WHERE " + " AND ".join(where_clauses)
        return where_sql, params

    def get_stats(
        self,
        group_by: str = "model",
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        model_filter: Optional[str] = None,
        api_filter: Optional[str] = None,
        enabled_only: bool = False,
        period: Optional[str] = None,
        agent_filter: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        conn = _get_db()

        valid_groups = {"model", "api", "date", "model_api", "vendor", "agent"}
        if group_by not in valid_groups:
            group_by = "model"

        if group_by == "vendor":
            group_expr = "model"
        elif group_by == "agent":
            group_expr = "CASE WHEN agent = '' THEN '(未指定)' ELSE agent END"
        else:
            group_expr = {
                "model": "model",
                "api": "api_name",
                "date": "DATE(created_at)",
                "model_api": "model || ' | ' || api_name",
            }[group_by]

        where_sql, params = self._build_where(
            date_from=date_from, date_to=date_to,
            model_filter=model_filter, api_filter=api_filter,
            enabled_only=enabled_only, period=period,
            agent_filter=agent_filter,
        )

        query = f"""
            SELECT
                {group_expr} AS group_key,
                COUNT(*) AS call_count,
                SUM(input_tokens) AS total_input_tokens,
                SUM(output_tokens) AS total_output_tokens,
                SUM(total_tokens) AS total_tokens,
                AVG(input_tokens) AS avg_input_tokens,
                AVG(output_tokens) AS avg_output_tokens,
                MIN(created_at) AS first_call,
                MAX(created_at) AS last_call
            FROM token_usage
            {where_sql}
            GROUP BY {group_expr}
            ORDER BY total_tokens DESC
        """

        rows = conn.execute(query, params).fetchall()
        conn.close()

        results = []
        models = _load_models()
        for row in rows:
            entry = {
                "group_key": row["group_key"],
                "call_count": row["call_count"],
                "total_input_tokens": row["total_input_tokens"],
                "total_output_tokens": row["total_output_tokens"],
                "total_tokens": row["total_tokens"],
                "avg_input_tokens": round(row["avg_input_tokens"], 1),
                "avg_output_tokens": round(row["avg_output_tokens"], 1),
                "first_call": row["first_call"],
                "last_call": row["last_call"],
            }
            if group_by == "vendor":
                model_name = row["group_key"]
                vendor = models.get(model_name, {}).get("vendor", "unknown")
                entry["vendor"] = vendor
            results.append(entry)

        if group_by == "vendor":
            vendor_agg: Dict[str, Dict[str, Any]] = {}
            for entry in results:
                vendor = entry.get("vendor", "unknown")
                if vendor not in vendor_agg:
                    vendor_agg[vendor] = {
                        "group_key": vendor,
                        "call_count": 0,
                        "total_input_tokens": 0,
                        "total_output_tokens": 0,
                        "total_tokens": 0,
                    }
                vendor_agg[vendor]["call_count"] += entry["call_count"]
                vendor_agg[vendor]["total_input_tokens"] += entry["total_input_tokens"]
                vendor_agg[vendor]["total_output_tokens"] += entry["total_output_tokens"]
                vendor_agg[vendor]["total_tokens"] += entry["total_tokens"]
            results = sorted(vendor_agg.values(), key=lambda x: x["total_tokens"], reverse=True)

        return results

    def get_summary(
        self,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        with_cost: bool = False,
        enabled_only: bool = False,
        period: Optional[str] = None,
        agent_filter: Optional[str] = None,
    ) -> Dict[str, Any]:
        conn = _get_db()

        where_sql, params = self._build_where(
            date_from=date_from, date_to=date_to,
            enabled_only=enabled_only, period=period,
            agent_filter=agent_filter,
        )

        row = conn.execute(
            f"""
            SELECT
                COUNT(*) AS total_calls,
                SUM(input_tokens) AS total_input_tokens,
                SUM(output_tokens) AS total_output_tokens,
                SUM(total_tokens) AS total_tokens,
                COUNT(DISTINCT model) AS unique_models,
                COUNT(DISTINCT api_name) AS unique_apis,
                MIN(created_at) AS first_call,
                MAX(created_at) AS last_call
            FROM token_usage
            {where_sql}
            """,
            params,
        ).fetchone()

        conn.close()

        result = {
            "total_calls": row["total_calls"] or 0,
            "total_input_tokens": row["total_input_tokens"] or 0,
            "total_output_tokens": row["total_output_tokens"] or 0,
            "total_tokens": row["total_tokens"] or 0,
            "unique_models": row["unique_models"] or 0,
            "unique_apis": row["unique_apis"] or 0,
            "first_call": row["first_call"],
            "last_call": row["last_call"],
        }

        if with_cost:
            cost_detail = self._estimate_total_cost_detail(
                date_from=date_from, date_to=date_to,
                enabled_only=enabled_only, period=period,
            )
            result["estimated_cost"] = cost_detail

        return result

    def _estimate_total_cost_detail(
        self,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        enabled_only: bool = False,
        period: Optional[str] = None,
    ) -> Dict[str, Any]:
        conn = _get_db()

        where_sql, params = self._build_where(
            date_from=date_from, date_to=date_to,
            enabled_only=enabled_only, period=period,
        )

        rows = conn.execute(
            f"SELECT model, SUM(input_tokens) AS input_tokens, SUM(output_tokens) AS output_tokens FROM token_usage {where_sql} GROUP BY model",
            params,
        ).fetchall()
        conn.close()

        by_currency: Dict[str, float] = {}
        by_model: List[Dict[str, Any]] = []
        models = _load_models()

        for row in rows:
            model = row["model"]
            config = models.get(model, _resolve_model_config(model))
            currency = config.get("cost_currency", "USD")
            input_cost = config.get("cost_per_input_token", 0) * (row["input_tokens"] or 0)
            output_cost = config.get("cost_per_output_token", 0) * (row["output_tokens"] or 0)
            total_cost = input_cost + output_cost

            by_currency[currency] = by_currency.get(currency, 0.0) + total_cost
            by_model.append({
                "model": model,
                "vendor": config.get("vendor", "unknown"),
                "input_cost": round(input_cost, 8),
                "output_cost": round(output_cost, 8),
                "total_cost": round(total_cost, 8),
                "currency": currency,
            })

        return {
            "by_currency": {k: round(v, 6) for k, v in by_currency.items()},
            "by_model": by_model,
        }

    def export_data(
        self,
        format: str = "json",
        output: Optional[str] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        enabled_only: bool = False,
        period: Optional[str] = None,
        agent_filter: Optional[str] = None,
    ) -> str:
        conn = _get_db()

        where_sql, params = self._build_where(
            date_from=date_from, date_to=date_to,
            enabled_only=enabled_only, period=period,
            agent_filter=agent_filter,
        )

        rows = conn.execute(
            f"SELECT * FROM token_usage {where_sql} ORDER BY created_at DESC",
            params,
        ).fetchall()
        conn.close()

        records = []
        for row in rows:
            records.append({
                "id": row["id"],
                "api_name": row["api_name"],
                "model": row["model"],
                "input_tokens": row["input_tokens"],
                "output_tokens": row["output_tokens"],
                "total_tokens": row["total_tokens"],
                "metadata": row["metadata"],
                "created_at": row["created_at"],
            })

        if format == "csv":
            lines = ["id,api_name,model,input_tokens,output_tokens,total_tokens,metadata,created_at"]
            for r in records:
                meta = r["metadata"] or ""
                meta = meta.replace('"', '""')
                lines.append(
                    f'{r["id"]},{r["api_name"]},{r["model"]},{r["input_tokens"]},'
                    f'{r["output_tokens"]},{r["total_tokens"]},"{meta}",{r["created_at"]}'
                )
            data = "\n".join(lines)
        else:
            data = json.dumps(records, ensure_ascii=False, indent=2)

        if output:
            with open(output, "w", encoding="utf-8") as f:
                f.write(data)

        return data

    def list_agents(self) -> List[str]:
        conn = _get_db()
        rows = conn.execute(
            "SELECT DISTINCT agent FROM token_usage WHERE agent != '' ORDER BY agent"
        ).fetchall()
        conn.close()
        return [r["agent"] for r in rows]

    def list_models(
        self,
        vendor: Optional[str] = None,
        region: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        models = _load_models()
        vendors = _load_vendors()
        enabled_map = _load_enabled()
        results = []
        for name, config in models.items():
            model_vendor = config.get("vendor", "unknown")
            vendor_info = vendors.get(model_vendor, {})
            if vendor and model_vendor != vendor:
                continue
            if region and vendor_info.get("region") != region:
                continue
            is_enabled = enabled_map.get(name, False) if enabled_map else True
            results.append({
                "model": name,
                "vendor": model_vendor,
                "vendor_name": vendor_info.get("name", model_vendor),
                "region": vendor_info.get("region", "unknown"),
                "tokenizer_type": config.get("tokenizer_type", "unknown"),
                "max_tokens": config.get("max_tokens", 0),
                "has_cost_info": "cost_per_input_token" in config,
                "cost_currency": config.get("cost_currency", ""),
                "is_custom": name in _load_json(_CUSTOM_MODELS_FILE, {}),
                "enabled": is_enabled,
            })
        return results

    def list_vendors(self, region: Optional[str] = None) -> List[Dict[str, Any]]:
        vendors = _load_vendors()
        results = []
        custom_vendors = _load_json(_CUSTOM_VENDORS_FILE, {})
        enabled_vendors = _load_enabled_vendors()
        models = _load_models()
        for key, info in vendors.items():
            if region and info.get("region") != region:
                continue
            model_count = sum(1 for m in models.values() if m.get("vendor") == key)
            is_enabled = enabled_vendors.get(key, True) if enabled_vendors else True
            results.append({
                "id": key,
                "name": info.get("name", key),
                "region": info.get("region", "unknown"),
                "currency": info.get("currency", "USD"),
                "base_urls": info.get("base_urls", []),
                "api_compatibility": info.get("api_compatibility", "unknown"),
                "auth_type": info.get("auth_type", "unknown"),
                "auth_header": info.get("auth_header", ""),
                "note": info.get("note", ""),
                "model_count": model_count,
                "enabled": is_enabled,
                "is_custom": key in custom_vendors,
            })
        return results

    def list_plans(
        self,
        vendor: Optional[str] = None,
        plan_type: Optional[str] = None,
        model: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        plans = _load_plans()
        results = []
        for key, info in plans.items():
            if vendor and info.get("vendor") != vendor:
                continue
            if plan_type and info.get("type") != plan_type:
                continue
            if model and model not in info.get("applicable_models", []):
                continue
            results.append({
                "id": key,
                "vendor": info.get("vendor", ""),
                "name": info.get("name", key),
                "type": info.get("type", ""),
                "price": info.get("price", 0),
                "currency": info.get("currency", "USD"),
                "included_tokens": info.get("included_tokens", 0),
                "applicable_models": info.get("applicable_models", []),
                "description": info.get("description", ""),
                "discount_rate": info.get("discount_rate"),
                "billing_unit": info.get("billing_unit"),
                "credits_per_month": info.get("credits_per_month"),
                "prompts_per_5h": info.get("prompts_per_5h"),
                "requests_per_5h": info.get("requests_per_5h"),
                "calls_per_month": info.get("calls_per_month"),
            })
        return results

    def set_model_enabled(self, model_name: str, enabled: bool) -> bool:
        models = _load_models()
        if model_name not in models:
            return False
        enabled_map = _load_enabled()
        enabled_map[model_name] = enabled
        _save_enabled(enabled_map)
        return True

    def set_vendor_enabled(self, vendor_id: str, enabled: bool) -> int:
        enabled_vendors = _load_enabled_vendors()
        enabled_vendors[vendor_id] = enabled
        _save_enabled_vendors(enabled_vendors)

        models = _load_models()
        enabled_map = _load_enabled()
        count = 0
        for name, config in models.items():
            if config.get("vendor") == vendor_id:
                enabled_map[name] = enabled
                count += 1
        if count > 0:
            _save_enabled(enabled_map)
        return count

    def enable_all(self) -> int:
        models = _load_models()
        vendors = _load_vendors()
        enabled_map = {name: True for name in models}
        _save_enabled(enabled_map)
        enabled_vendors = {vid: True for vid in vendors}
        _save_enabled_vendors(enabled_vendors)
        return len(models)

    def disable_all(self) -> int:
        models = _load_models()
        vendors = _load_vendors()
        enabled_map = {name: False for name in models}
        _save_enabled(enabled_map)
        enabled_vendors = {vid: False for vid in vendors}
        _save_enabled_vendors(enabled_vendors)
        return len(models)

    def get_enabled_status(self) -> Dict[str, Any]:
        models = _load_models()
        vendors = _load_vendors()
        enabled_map = _load_enabled()
        enabled_vendors = _load_enabled_vendors()
        if not enabled_map and not enabled_vendors:
            return {
                "initialized": False,
                "total_models": len(models),
                "enabled_count": len(models),
                "disabled_count": 0,
                "total_vendors": len(vendors),
                "enabled_vendor_count": len(vendors),
                "disabled_vendor_count": 0,
                "models": {name: True for name in models},
                "vendors": {vid: True for vid in vendors},
            }
        enabled_count = sum(1 for v in enabled_map.values() if v) if enabled_map else len(models)
        enabled_vendor_count = sum(1 for v in enabled_vendors.values() if v) if enabled_vendors else len(vendors)
        return {
            "initialized": True,
            "total_models": len(models),
            "enabled_count": enabled_count,
            "disabled_count": len(models) - enabled_count,
            "total_vendors": len(vendors),
            "enabled_vendor_count": enabled_vendor_count,
            "disabled_vendor_count": len(vendors) - enabled_vendor_count,
            "models": enabled_map or {name: True for name in models},
            "vendors": enabled_vendors or {vid: True for vid in vendors},
        }

    def register_vendor(
        self,
        vendor_id: str,
        name: str,
        region: str = "custom",
        currency: str = "USD",
        base_urls: Optional[List[str]] = None,
        api_compatibility: str = "openai",
        auth_type: str = "bearer_token",
        auth_header: str = "Authorization: Bearer {api_key}",
        note: str = "",
    ) -> Dict[str, Any]:
        custom_vendors = _load_json(_CUSTOM_VENDORS_FILE, {})
        entry = {
            "name": name,
            "region": region,
            "currency": currency,
            "base_urls": base_urls or [],
            "api_compatibility": api_compatibility,
            "auth_type": auth_type,
            "auth_header": auth_header,
        }
        if note:
            entry["note"] = note
        custom_vendors[vendor_id] = entry
        _save_json(_CUSTOM_VENDORS_FILE, custom_vendors)
        _invalidate_cache()
        return {"id": vendor_id, **entry}

    def register_model(
        self,
        model_name: str,
        vendor: str = "custom",
        tokenizer_type: str = "estimator",
        encoding_name: Optional[str] = None,
        max_tokens: int = 4096,
        cost_per_input_token: float = 0,
        cost_per_output_token: float = 0,
        cost_currency: str = "USD",
        chars_per_token_en: float = 4.0,
        chars_per_token_zh: float = 1.5,
        note: Optional[str] = None,
    ) -> Dict[str, Any]:
        custom_models = _load_json(_CUSTOM_MODELS_FILE, {})

        if tokenizer_type == "tiktoken" and encoding_name:
            tokenizer_config = {"encoding_name": encoding_name}
        else:
            tokenizer_config = {
                "chars_per_token_en": chars_per_token_en,
                "chars_per_token_zh": chars_per_token_zh,
            }

        entry = {
            "vendor": vendor,
            "tokenizer_type": tokenizer_type,
            "tokenizer_config": tokenizer_config,
            "max_tokens": max_tokens,
            "cost_per_input_token": cost_per_input_token,
            "cost_per_output_token": cost_per_output_token,
            "cost_currency": cost_currency,
        }
        if note:
            entry["note"] = note

        custom_models[model_name] = entry
        _save_json(_CUSTOM_MODELS_FILE, custom_models)
        _invalidate_cache()
        return {"model": model_name, **entry}

    def unregister_model(self, model_name: str) -> bool:
        custom_models = _load_json(_CUSTOM_MODELS_FILE, {})
        if model_name in custom_models:
            del custom_models[model_name]
            _save_json(_CUSTOM_MODELS_FILE, custom_models)
            enabled_map = _load_enabled()
            if model_name in enabled_map:
                del enabled_map[model_name]
                _save_enabled(enabled_map)
            _invalidate_cache()
            return True
        return False

    def unregister_vendor(self, vendor_id: str) -> bool:
        custom_vendors = _load_json(_CUSTOM_VENDORS_FILE, {})
        if vendor_id in custom_vendors:
            del custom_vendors[vendor_id]
            _save_json(_CUSTOM_VENDORS_FILE, custom_vendors)
            _invalidate_cache()
            return True
        return False
