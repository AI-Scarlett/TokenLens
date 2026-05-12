#!/usr/bin/env python3
import argparse
import json
import sys
from token_counter import TokenCounter


def _resolve_period(args):
    period = getattr(args, 'period', None)
    if period:
        return period
    if getattr(args, 'today', False):
        return "today"
    if getattr(args, 'days7', False):
        return "7d"
    if getattr(args, 'days30', False):
        return "30d"
    if getattr(args, 'days90', False):
        return "90d"
    return None


def cmd_count(args):
    counter = TokenCounter()
    model = args.model or counter.get_default_model()
    tokens = counter.count_tokens(args.text, model=model)
    print(f"Model: {model}")
    print(f"Text: {args.text[:80]}{'...' if len(args.text) > 80 else ''}")
    print(f"Token count: {tokens}")


def cmd_count_messages(args):
    counter = TokenCounter()
    model = args.model or counter.get_default_model()
    try:
        with open(args.file, "r", encoding="utf-8") as f:
            messages = json.load(f)
    except Exception as e:
        print(f"Error reading messages file: {e}", file=sys.stderr)
        sys.exit(1)

    if not isinstance(messages, list):
        print("Messages file must contain a JSON array", file=sys.stderr)
        sys.exit(1)

    tokens = counter.count_messages_tokens(messages, model=model)
    print(f"Model: {model}")
    print(f"Messages: {len(messages)}")
    print(f"Total tokens: {tokens}")


def cmd_record(args):
    counter = TokenCounter()
    metadata = {}
    if args.metadata:
        try:
            metadata = json.loads(args.metadata)
        except json.JSONDecodeError:
            print("Invalid metadata JSON", file=sys.stderr)
            sys.exit(1)

    result = counter.record_text(
        api_name=args.api,
        model=args.model,
        input_text=args.input_text,
        output_text=args.output_text,
        metadata=metadata,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


def cmd_stats(args):
    counter = TokenCounter()
    results = counter.get_stats(
        group_by=args.by,
        date_from=args.from_date,
        date_to=args.to_date,
        model_filter=args.model,
        api_filter=args.api,
        enabled_only=args.enabled_only,
        period=_resolve_period(args),
    )
    if not results:
        print("No data found.")
        return
    print(json.dumps(results, ensure_ascii=False, indent=2))


def cmd_summary(args):
    counter = TokenCounter()
    result = counter.get_summary(
        date_from=args.from_date,
        date_to=args.to_date,
        with_cost=args.with_cost,
        enabled_only=args.enabled_only,
        period=_resolve_period(args),
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


def cmd_export(args):
    counter = TokenCounter()
    data = counter.export_data(
        format=args.format,
        output=args.output,
        date_from=args.from_date,
        date_to=args.to_date,
        enabled_only=args.enabled_only,
        period=_resolve_period(args),
    )
    if not args.output:
        print(data)
    else:
        print(f"Exported to {args.output}")


def cmd_models(args):
    counter = TokenCounter()
    models = counter.list_models(vendor=args.vendor, region=args.region)
    for m in models:
        cost_info = "✓" if m["has_cost_info"] else "✗"
        custom_tag = " [custom]" if m["is_custom"] else ""
        enabled_tag = "🟢" if m["enabled"] else "⚫"
        print(
            f"  {enabled_tag} {m['model']:25s}  vendor={m['vendor_name']:12s}  "
            f"region={m['region']:14s}  tokenizer={m['tokenizer_type']:12s}  "
            f"max={m['max_tokens']:>8d}  cost={cost_info}  cur={m['cost_currency']}{custom_tag}"
        )


def cmd_vendors(args):
    counter = TokenCounter()
    vendors = counter.list_vendors(region=args.region)
    for v in vendors:
        enabled_tag = "🟢" if v.get("enabled", True) else "⚫"
        urls = " | ".join(v.get("base_urls", [])) or "无"
        print(f"  {enabled_tag} {v['id']:15s}  name={v['name']:12s}  region={v['region']:14s}  currency={v['currency']}  models={v.get('model_count',0)}")
        print(f"  {'':19s}  base_url={urls}")
        print(f"  {'':19s}  api={v.get('api_compatibility','?'):10s}  auth={v.get('auth_type','?'):14s}  header={v.get('auth_header','?')}")
        if v.get("note"):
            print(f"  {'':19s}  note: {v['note']}")
        print()


def cmd_plans(args):
    counter = TokenCounter()
    plans = counter.list_plans(vendor=args.vendor, plan_type=args.type, model=args.model)
    if not plans:
        print("No plans found.")
        return
    for p in plans:
        discount = f"  discount={p['discount_rate']}" if p.get("discount_rate") else ""
        quota = ""
        if p.get("credits_per_month"):
            quota = f"  credits={p['credits_per_month']}/月"
        elif p.get("prompts_per_5h"):
            quota = f"  prompts={p['prompts_per_5h']}/5h"
        elif p.get("requests_per_5h"):
            quota = f"  requests={p['requests_per_5h']}/5h"
        elif p.get("calls_per_month"):
            quota = f"  calls={p['calls_per_month']}/月"
        elif p.get("included_tokens"):
            quota = f"  tokens={p['included_tokens']:>12d}"
        print(
            f"  {p['id']:40s}  vendor={p['vendor']:10s}  type={p['type']:14s}  "
            f"price={p['price']} {p['currency']}{quota}{discount}"
        )
        print(f"  {'':40s}  {p['name']}")
        print(f"  {'':40s}  models: {', '.join(p['applicable_models'])}")
        print(f"  {'':40s}  {p['description']}")
        print()


def cmd_enable(args):
    counter = TokenCounter()
    if args.model:
        ok = counter.set_model_enabled(args.model, True)
        print(f"Model '{args.model}' enabled." if ok else f"Model '{args.model}' not found.")
    elif args.vendor:
        count = counter.set_vendor_enabled(args.vendor, True)
        print(f"Enabled {count} models for vendor '{args.vendor}'.")
    elif args.all:
        count = counter.enable_all()
        print(f"Enabled all {count} models.")
    else:
        print("Please specify --model, --vendor, or --all")


def cmd_disable(args):
    counter = TokenCounter()
    if args.model:
        ok = counter.set_model_enabled(args.model, False)
        print(f"Model '{args.model}' disabled." if ok else f"Model '{args.model}' not found.")
    elif args.vendor:
        count = counter.set_vendor_enabled(args.vendor, False)
        print(f"Disabled {count} models for vendor '{args.vendor}'.")
    elif args.all:
        count = counter.disable_all()
        print(f"Disabled all {count} models.")
    else:
        print("Please specify --model, --vendor, or --all")


def cmd_enabled(args):
    counter = TokenCounter()
    status = counter.get_enabled_status()
    print(f"Initialized: {status['initialized']}")
    print(f"Total models: {status['total_models']}")
    print(f"Enabled: {status['enabled_count']}")
    print(f"Disabled: {status['disabled_count']}")
    if args.verbose:
        for name, enabled in sorted(status["models"].items()):
            tag = "🟢" if enabled else "⚫"
            print(f"  {tag} {name}")


def _add_time_args(parser):
    parser.add_argument("--from", dest="from_date", help="起始日期 (YYYY-MM-DD)")
    parser.add_argument("--to", dest="to_date", help="结束日期 (YYYY-MM-DD)")
    parser.add_argument("--today", action="store_true", help="今日数据")
    parser.add_argument("--7d", dest="days7", action="store_true", help="最近7天")
    parser.add_argument("--30d", dest="days30", action="store_true", help="最近30天")
    parser.add_argument("--90d", dest="days90", action="store_true", help="最近90天")
    parser.add_argument("--enabled-only", action="store_true", help="仅统计已启用的模型")


def cmd_register_vendor(args):
    counter = TokenCounter()
    base_urls = args.base_url.split(",") if args.base_url else []
    result = counter.register_vendor(
        vendor_id=args.id,
        name=args.name,
        region=args.region,
        currency=args.currency,
        base_urls=base_urls,
        api_compatibility=args.api_compat,
        auth_type=args.auth_type,
        auth_header=args.auth_header,
        note=args.note or "",
    )
    print(f"Vendor registered: {json.dumps(result, ensure_ascii=False, indent=2)}")


def cmd_register_model(args):
    counter = TokenCounter()
    result = counter.register_model(
        model_name=args.name,
        vendor=args.vendor,
        tokenizer_type=args.tokenizer,
        encoding_name=args.encoding,
        max_tokens=args.max_tokens,
        cost_per_input_token=args.input_cost,
        cost_per_output_token=args.output_cost,
        cost_currency=args.currency,
        chars_per_token_en=args.chars_en,
        chars_per_token_zh=args.chars_zh,
        note=args.note,
    )
    print(f"Model registered: {json.dumps(result, ensure_ascii=False, indent=2)}")


def cmd_unregister_model(args):
    counter = TokenCounter()
    if counter.unregister_model(args.name):
        print(f"Model '{args.name}' unregistered.")
    else:
        print(f"Model '{args.name}' not found in custom models.")


def cmd_unregister_vendor(args):
    counter = TokenCounter()
    if counter.unregister_vendor(args.id):
        print(f"Vendor '{args.id}' unregistered.")
    else:
        print(f"Vendor '{args.id}' not found in custom vendors.")


def main():
    parser = argparse.ArgumentParser(
        description="Token Counter - 本地Token消耗统计工具 (无需API调用)"
    )
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # count
    p_count = subparsers.add_parser("count", help="计算文本的Token数")
    p_count.add_argument("--text", "-t", required=True, help="要计算的文本")
    p_count.add_argument("--model", "-m", default=None, help="模型名称 (默认: 注册表中的第一个模型)")

    # count-messages
    p_msg = subparsers.add_parser("count-messages", help="计算消息列表的Token数")
    p_msg.add_argument("--file", "-f", required=True, help="消息列表JSON文件路径")
    p_msg.add_argument("--model", "-m", default=None, help="模型名称 (默认: 注册表中的第一个模型)")

    # record
    p_record = subparsers.add_parser("record", help="记录一次API调用的Token消耗")
    p_record.add_argument("--api", "-a", required=True, help="API接口名称")
    p_record.add_argument("--model", "-m", required=True, help="模型名称")
    p_record.add_argument("--input-text", "-i", required=True, help="输入文本")
    p_record.add_argument("--output-text", "-o", required=True, help="输出文本")
    p_record.add_argument("--metadata", "-M", help="附加元数据 (JSON格式)")

    # stats
    p_stats = subparsers.add_parser("stats", help="查询统计报告")
    p_stats.add_argument("--by", "-b", default="model", choices=["model", "api", "date", "model_api", "vendor"], help="分组方式")
    p_stats.add_argument("--model", "-m", help="按模型名过滤")
    p_stats.add_argument("--api", "-a", help="按API名过滤")
    _add_time_args(p_stats)

    # summary
    p_summary = subparsers.add_parser("summary", help="查看总体统计")
    p_summary.add_argument("--with-cost", action="store_true", help="包含费用估算")
    _add_time_args(p_summary)

    # export
    p_export = subparsers.add_parser("export", help="导出统计数据")
    p_export.add_argument("--format", "-f", default="json", choices=["json", "csv"], help="导出格式")
    p_export.add_argument("--output", "-o", help="输出文件路径 (不指定则输出到stdout)")
    _add_time_args(p_export)

    # models
    p_models = subparsers.add_parser("models", help="列出所有支持的模型 (含启用状态)")
    p_models.add_argument("--vendor", "-v", help="按厂商过滤")
    p_models.add_argument("--region", "-r", choices=["china", "international"], help="按区域过滤")

    # vendors
    p_vendors = subparsers.add_parser("vendors", help="列出所有厂商")
    p_vendors.add_argument("--region", "-r", choices=["china", "international"], help="按区域过滤")

    # plans
    p_plans = subparsers.add_parser("plans", help="查看套餐/定价计划")
    p_plans.add_argument("--vendor", "-v", help="按厂商过滤")
    p_plans.add_argument("--type", "-t", choices=["token_pack", "token_plan", "coding_plan", "discount_plan", "free_tier"], help="按套餐类型过滤")
    p_plans.add_argument("--model", "-m", help="按适用模型过滤")

    # enable
    p_enable = subparsers.add_parser("enable", help="启用模型/厂商")
    p_enable.add_argument("--model", "-m", help="启用指定模型")
    p_enable.add_argument("--vendor", "-v", help="启用指定厂商的所有模型")
    p_enable.add_argument("--all", action="store_true", help="启用所有模型")

    # disable
    p_disable = subparsers.add_parser("disable", help="禁用模型/厂商")
    p_disable.add_argument("--model", "-m", help="禁用指定模型")
    p_disable.add_argument("--vendor", "-v", help="禁用指定厂商的所有模型")
    p_disable.add_argument("--all", action="store_true", help="禁用所有模型")

    # enabled (status)
    p_enabled = subparsers.add_parser("enabled", help="查看启用状态")
    p_enabled.add_argument("--verbose", "-V", action="store_true", help="显示每个模型的启用状态")

    # register-vendor
    p_regv = subparsers.add_parser("register-vendor", help="注册自定义厂商")
    p_regv.add_argument("--id", required=True, help="厂商ID (英文标识符)")
    p_regv.add_argument("--name", required=True, help="厂商显示名称")
    p_regv.add_argument("--region", default="custom", choices=["china", "international", "custom"], help="区域")
    p_regv.add_argument("--currency", default="USD", help="货币 (默认: USD)")
    p_regv.add_argument("--base-url", help="API Base URL (多个用逗号分隔)")
    p_regv.add_argument("--api-compat", default="openai", choices=["openai", "anthropic", "google", "cohere", "baidu", "spark", "custom"], help="API兼容格式 (默认: openai)")
    p_regv.add_argument("--auth-type", default="bearer_token", choices=["bearer_token", "x_api_key", "access_token", "query_param", "spark_auth", "none"], help="认证方式 (默认: bearer_token)")
    p_regv.add_argument("--auth-header", default="Authorization: Bearer {api_key}", help="认证Header模板")
    p_regv.add_argument("--note", help="备注")

    # register-model
    p_regm = subparsers.add_parser("register-model", help="注册自定义模型")
    p_regm.add_argument("--name", required=True, help="模型名称")
    p_regm.add_argument("--vendor", default="custom", help="所属厂商ID (默认: custom)")
    p_regm.add_argument("--tokenizer", default="estimator", choices=["tiktoken", "estimator"], help="Tokenizer类型")
    p_regm.add_argument("--encoding", help="tiktoken编码名 (如 cl100k_base, o200k_base)")
    p_regm.add_argument("--max-tokens", type=int, default=4096, help="最大上下文长度")
    p_regm.add_argument("--input-cost", type=float, default=0, help="输入Token单价")
    p_regm.add_argument("--output-cost", type=float, default=0, help="输出Token单价")
    p_regm.add_argument("--currency", default="USD", help="货币 (默认: USD)")
    p_regm.add_argument("--chars-en", type=float, default=4.0, help="英文字符/token比 (estimator模式)")
    p_regm.add_argument("--chars-zh", type=float, default=1.5, help="中文字符/token比 (estimator模式)")
    p_regm.add_argument("--note", help="备注")

    # unregister-model
    p_unregm = subparsers.add_parser("unregister-model", help="移除自定义模型")
    p_unregm.add_argument("--name", required=True, help="模型名称")

    # unregister-vendor
    p_unregv = subparsers.add_parser("unregister-vendor", help="移除自定义厂商")
    p_unregv.add_argument("--id", required=True, help="厂商ID")

    args = parser.parse_args()

    commands = {
        "count": cmd_count,
        "count-messages": cmd_count_messages,
        "record": cmd_record,
        "stats": cmd_stats,
        "summary": cmd_summary,
        "export": cmd_export,
        "models": cmd_models,
        "vendors": cmd_vendors,
        "plans": cmd_plans,
        "enable": cmd_enable,
        "disable": cmd_disable,
        "enabled": cmd_enabled,
        "register-vendor": cmd_register_vendor,
        "register-model": cmd_register_model,
        "unregister-model": cmd_unregister_model,
        "unregister-vendor": cmd_unregister_vendor,
    }

    if args.command in commands:
        commands[args.command](args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
