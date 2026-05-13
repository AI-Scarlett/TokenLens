import json
import logging
import os
import threading
from flask import Flask, jsonify, request, send_from_directory
from token_counter import TokenCounter
from agent_scanner import scan_agents, scan_available_models, add_custom_scanner, remove_custom_scanner, list_custom_scanners
from usage_monitor import (
    scan_trae_log_history, import_trae_log_history,
    get_monitor_status, start_monitor, stop_monitor,
)
from agent_config import configure_all_agents, unconfigure_all_agents, detect_all_agents, configure_agent_by_name, unconfigure_agent_by_name

app = Flask(__name__, static_folder="static", static_url_path="/static")
counter = TokenCounter()
logger = logging.getLogger(__name__)

_gateway_thread = None
_gateway_status = {"running": False, "port": 0, "host": "", "message": ""}
_monitor_thread = None


@app.route("/")
def index():
    return send_from_directory("static", "index.html")


def _get_time_params():
    date_from = request.args.get("from")
    date_to = request.args.get("to")
    period = request.args.get("period")
    enabled_only = request.args.get("enabled_only", "0") == "1"
    agent_filter = request.args.get("agent") or None
    return date_from, date_to, period, enabled_only, agent_filter


@app.route("/api/summary", methods=["GET"])
def api_summary():
    date_from, date_to, period, enabled_only, agent_filter = _get_time_params()
    with_cost = request.args.get("with_cost", "0") == "1"
    return jsonify(counter.get_summary(
        date_from=date_from, date_to=date_to,
        with_cost=with_cost, enabled_only=enabled_only, period=period,
        agent_filter=agent_filter,
    ))


@app.route("/api/stats", methods=["GET"])
def api_stats():
    group_by = request.args.get("by", "model")
    date_from, date_to, period, enabled_only, agent_filter = _get_time_params()
    model_filter = request.args.get("model")
    api_filter = request.args.get("api")
    return jsonify(counter.get_stats(
        group_by=group_by, date_from=date_from, date_to=date_to,
        model_filter=model_filter, api_filter=api_filter,
        enabled_only=enabled_only, period=period,
        agent_filter=agent_filter,
    ))


@app.route("/api/count", methods=["POST"])
def api_count():
    data = request.get_json()
    text = data.get("text", "")
    model = data.get("model") or counter.get_default_model()
    tokens = counter.count_tokens(text, model=model)
    return jsonify({"model": model, "token_count": tokens, "text_length": len(text)})


@app.route("/api/record", methods=["POST"])
def api_record():
    data = request.get_json()
    result = counter.record_text(
        api_name=data.get("api_name", "unknown"),
        model=data.get("model") or counter.get_default_model(),
        input_text=data.get("input_text", ""),
        output_text=data.get("output_text", ""),
        metadata=data.get("metadata"),
        agent=data.get("agent", ""),
    )
    return jsonify(result)


@app.route("/api/vendors", methods=["GET"])
def api_vendors():
    region = request.args.get("region")
    return jsonify(counter.list_vendors(region=region))


@app.route("/api/vendors", methods=["POST"])
def api_register_vendor():
    data = request.get_json()
    result = counter.register_vendor(
        vendor_id=data.get("id"),
        name=data.get("name"),
        region=data.get("region", "custom"),
        currency=data.get("currency", "USD"),
        base_urls=data.get("base_urls", []),
        api_compatibility=data.get("api_compatibility", "openai"),
        auth_type=data.get("auth_type", "bearer_token"),
        auth_header=data.get("auth_header", "Authorization: Bearer {api_key}"),
        note=data.get("note", ""),
    )
    return jsonify(result)


@app.route("/api/vendors/<vendor_id>", methods=["DELETE"])
def api_unregister_vendor(vendor_id):
    ok = counter.unregister_vendor(vendor_id)
    return jsonify({"success": ok})


@app.route("/api/models", methods=["GET"])
def api_models():
    vendor = request.args.get("vendor")
    region = request.args.get("region")
    return jsonify(counter.list_models(vendor=vendor, region=region))


@app.route("/api/models", methods=["POST"])
def api_register_model():
    data = request.get_json()
    result = counter.register_model(
        model_name=data.get("name"),
        vendor=data.get("vendor", "custom"),
        tokenizer_type=data.get("tokenizer_type", "estimator"),
        encoding_name=data.get("encoding_name"),
        max_tokens=data.get("max_tokens", 4096),
        cost_per_input_token=data.get("cost_per_input_token", 0),
        cost_per_output_token=data.get("cost_per_output_token", 0),
        cost_currency=data.get("cost_currency", "USD"),
        chars_per_token_en=data.get("chars_per_token_en", 4.0),
        chars_per_token_zh=data.get("chars_per_token_zh", 1.5),
        note=data.get("note"),
    )
    return jsonify(result)


@app.route("/api/models/<model_name>", methods=["DELETE"])
def api_unregister_model(model_name):
    ok = counter.unregister_model(model_name)
    return jsonify({"success": ok})


@app.route("/api/models/<model_name>/toggle", methods=["POST"])
def api_toggle_model(model_name):
    data = request.get_json()
    enabled = data.get("enabled", True)
    ok = counter.set_model_enabled(model_name, enabled)
    return jsonify({"success": ok, "model": model_name, "enabled": enabled})


@app.route("/api/vendors/<vendor_id>/toggle", methods=["POST"])
def api_toggle_vendor(vendor_id):
    data = request.get_json()
    enabled = data.get("enabled", True)
    count = counter.set_vendor_enabled(vendor_id, enabled)
    return jsonify({"success": count > 0, "vendor": vendor_id, "enabled": enabled, "count": count})


@app.route("/api/enabled", methods=["GET"])
def api_enabled_status():
    return jsonify(counter.get_enabled_status())


@app.route("/api/enabled/enable-all", methods=["POST"])
def api_enable_all():
    count = counter.enable_all()
    return jsonify({"enabled": count})


@app.route("/api/enabled/disable-all", methods=["POST"])
def api_disable_all():
    count = counter.disable_all()
    return jsonify({"disabled": count})


@app.route("/api/plans", methods=["GET"])
def api_plans():
    vendor = request.args.get("vendor")
    plan_type = request.args.get("type")
    model = request.args.get("model")
    return jsonify(counter.list_plans(vendor=vendor, plan_type=plan_type, model=model))


@app.route("/api/export", methods=["GET"])
def api_export():
    fmt = request.args.get("format", "json")
    date_from, date_to, period, enabled_only, agent_filter = _get_time_params()
    data = counter.export_data(
        format=fmt, date_from=date_from, date_to=date_to,
        enabled_only=enabled_only, period=period,
        agent_filter=agent_filter,
    )
    if fmt == "csv":
        return data, 200, {"Content-Type": "text/csv; charset=utf-8"}
    return data, 200, {"Content-Type": "application/json; charset=utf-8"}


@app.route("/api/agents/scan", methods=["GET"])
def api_agents_scan():
    agents = scan_agents()
    return jsonify(agents)


@app.route("/api/agents/list", methods=["GET"])
def api_agents_list():
    return jsonify(counter.list_agents())


@app.route("/api/agents/custom", methods=["GET"])
def api_custom_scanners_list():
    return jsonify(list_custom_scanners())


@app.route("/api/agents/custom", methods=["POST"])
def api_custom_scanner_add():
    data = request.get_json()
    scanner = add_custom_scanner(
        scanner_id=data.get("id", ""),
        name=data.get("name", ""),
        description=data.get("description", ""),
        config_path=data.get("config_path", ""),
        model_field=data.get("model_field", "__auto__"),
        vendor_name=data.get("vendor_name", ""),
        icon=data.get("icon", "📦"),
        base_url_field=data.get("base_url_field", ""),
    )
    return jsonify({"success": True, "scanner": scanner})


@app.route("/api/agents/custom/<scanner_id>", methods=["DELETE"])
def api_custom_scanner_delete(scanner_id):
    ok = remove_custom_scanner(scanner_id)
    return jsonify({"success": ok})


@app.route("/api/agents/models", methods=["GET"])
def api_agents_models():
    models = scan_available_models()
    return jsonify(models)


@app.route("/api/agents/import", methods=["POST"])
def api_agents_import():
    data = request.get_json()
    agent_ids = data.get("agents", [])
    model_ids = data.get("models", [])
    import_all = data.get("all", False)

    available = scan_available_models()
    to_import = []

    if import_all:
        to_import = available
    else:
        id_set = set(model_ids)
        if agent_ids:
            agent_id_set = set(agent_ids)
            for m in available:
                if m.get("source") in agent_id_set:
                    to_import.append(m)
                    id_set.add(m["id"])
        for m in available:
            if m["id"] in id_set and m not in to_import:
                to_import.append(m)

    existing_models = counter.list_models()
    existing_ids = {m["model"] for m in existing_models}
    existing_vendors = {v["id"] for v in counter.list_vendors()}

    imported_vendors = 0
    imported_models = 0
    skipped = 0

    vendor_map = {}
    for m in to_import:
        vid = m.get("vendor", "custom")
        if vid not in existing_vendors and vid not in vendor_map:
            vendor_map[vid] = m.get("vendor_display", vid)

    for vid, vname in vendor_map.items():
        counter.register_vendor(
            vendor_id=vid,
            name=vname,
            region="custom",
            currency="CNY",
            base_urls=[],
            api_compatibility="openai",
            auth_type="bearer_token",
            note=f"Auto-imported from Agent",
        )
        existing_vendors.add(vid)
        imported_vendors += 1

    for m in to_import:
        if m["id"] in existing_ids:
            skipped += 1
            continue
        max_tokens = m.get("max_tokens") or 4096
        counter.register_model(
            model_name=m["id"],
            vendor=m.get("vendor", "custom"),
            tokenizer_type="estimator",
            max_tokens=max_tokens,
            cost_per_input_token=0,
            cost_per_output_token=0,
            cost_currency="CNY",
            note=f"Auto-imported from {m.get('source', 'agent')}",
        )
        existing_ids.add(m["id"])
        imported_models += 1

    return jsonify({
        "success": True,
        "scanned": len(available),
        "selected": len(to_import),
        "imported_vendors": imported_vendors,
        "imported_models": imported_models,
        "skipped_existing": skipped,
    })


@app.route("/api/agents/detect", methods=["GET"])
def api_agents_detect():
    try:
        agents = detect_all_agents()
        return jsonify(agents)
    except Exception as e:
        return jsonify({"error": str(e)})


@app.route("/api/agents/configure", methods=["POST"])
def api_agents_configure():
    data = request.get_json() or {}
    agent_name = data.get("agent", "")
    gateway_host = data.get("host", "127.0.0.1")
    gateway_port = data.get("port", 8899)

    if not agent_name:
        return jsonify({"success": False, "message": "缺少 agent 参数"})

    try:
        result = configure_agent_by_name(agent_name, gateway_host, gateway_port)
        return jsonify(result)
    except Exception as e:
        return jsonify({"agent": agent_name, "success": False, "message": str(e)})


@app.route("/api/agents/unconfigure", methods=["POST"])
def api_agents_unconfigure():
    data = request.get_json() or {}
    agent_name = data.get("agent", "")

    if not agent_name:
        return jsonify({"success": False, "message": "缺少 agent 参数"})

    try:
        result = unconfigure_agent_by_name(agent_name)
        return jsonify(result)
    except Exception as e:
        return jsonify({"agent": agent_name, "success": False, "message": str(e)})


@app.route("/api/gateway/status", methods=["GET"])
def api_gateway_status():
    return jsonify(_gateway_status)


@app.route("/api/gateway/start", methods=["POST"])
def api_gateway_start():
    global _gateway_thread, _gateway_status
    if _gateway_status["running"]:
        return jsonify({"success": False, "message": "Gateway already running"})

    data = request.get_json() or {}
    port = data.get("port", 8899)
    host = data.get("host", "127.0.0.1")
    auto_config = data.get("auto_config", True)

    if auto_config:
        config_results = configure_all_agents(host, port)
        logger.info(f"Agent config results: {config_results}")

    def run_gateway_async():
        global _gateway_status
        try:
            from gateway_server import run_gateway, GATEWAY_CONFIG
            GATEWAY_CONFIG["listen_host"] = host
            GATEWAY_CONFIG["listen_port"] = port
            _gateway_status = {"running": True, "port": port, "host": host, "message": "Running"}
            run_gateway(host=host, port=port)
        except Exception as e:
            _gateway_status = {"running": False, "port": 0, "host": "", "message": str(e)}

    _gateway_thread = threading.Thread(target=run_gateway_async, daemon=True)
    _gateway_thread.start()
    _gateway_status = {"running": True, "port": port, "host": host, "message": "Starting..."}

    config_msg = ""
    if auto_config:
        configured = [r for r in config_results if r.get('success')]
        agent_names = [r['agent'] for r in configured]
        config_msg = f"，已自动配置 {len(configured)} 个 Agent（{', '.join(agent_names)}）"

    return jsonify({
        "success": True,
        "port": port,
        "host": host,
        "message": f"网关已启动{config_msg}，请重启 Agent 使配置生效",
        "gateway_url": f"http://{host}:{port}",
        "config_results": config_results if auto_config else [],
    })


@app.route("/api/gateway/stop", methods=["POST"])
def api_gateway_stop():
    global _gateway_status
    unconfigure_all_agents()
    _gateway_status = {"running": False, "port": 0, "host": "", "message": "Stopped"}
    return jsonify({"success": True, "message": "Gateway stopped, agent configs restored"})


@app.route("/api/monitor/status", methods=["GET"])
def api_monitor_status():
    return jsonify(get_monitor_status())


@app.route("/api/monitor/start", methods=["POST"])
def api_monitor_start():
    global _monitor_thread
    if get_monitor_status()["running"]:
        return jsonify({"success": False, "message": "Monitor already running"})
    _monitor_thread = threading.Thread(target=start_monitor, daemon=True)
    _monitor_thread.start()
    return jsonify({"success": True, "message": "Monitor started"})


@app.route("/api/monitor/stop", methods=["POST"])
def api_monitor_stop():
    stop_monitor()
    return jsonify({"success": True, "message": "Monitor stopped"})


@app.route("/api/usage/trae/scan", methods=["GET"])
def api_usage_trae_scan():
    result = scan_trae_log_history()
    return jsonify(result)


@app.route("/api/usage/trae/import", methods=["POST"])
def api_usage_trae_import():
    result = import_trae_log_history()
    return jsonify(result)


def _get_proxy_instructions(port, host):
    return {
        "trae": {
            "description": "在 Trae 设置中配置 HTTP 代理",
            "steps": [
                f"1. 打开 Trae 设置 (Cmd+,)",
                f"2. 搜索 'proxy' 或 'HTTP 代理'",
                f"3. 设置 HTTP 代理为: http://{host}:{port}",
                f"4. 重启 Trae 使设置生效",
            ],
        },
        "codebuddy": {
            "description": "在 CodeBuddy 配置中设置 API 代理",
            "steps": [
                f"1. 编辑 ~/.codebuddy/models.json",
                f"2. 将每个模型的 url 中的域名替换为: http://{host}:{port}/原始域名",
                f"   例如: https://api.minimaxi.com/v1/chat/completions",
                f"   改为: http://{host}:{port}/api.minimaxi.com/v1/chat/completions",
                f"3. 重启 CodeBuddy 使设置生效",
            ],
        },
        "environment": {
            "description": "通过环境变量设置代理（对所有应用生效）",
            "steps": [
                f"export HTTP_PROXY=http://{host}:{port}",
                f"export HTTPS_PROXY=http://{host}:{port}",
                f"然后启动目标应用",
            ],
        },
    }


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5170))
    debug = os.environ.get("FLASK_DEBUG", "0") == "1"
    app.run(host="0.0.0.0", port=port, debug=debug)
