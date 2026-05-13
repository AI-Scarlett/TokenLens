import asyncio
import json
import logging
import ssl
from pathlib import Path
from typing import Any, Dict
from aiohttp import web, ClientSession, ClientTimeout, TCPConnector

from token_counter import TokenCounter

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger('tokenlens-gateway')

counter = TokenCounter()

HOME = Path.home()

GATEWAY_CONFIG = {
    'listen_host': '127.0.0.1',
    'listen_port': 8899,
    'timeout': 120,
}

_vendor_routes = {
    'openai': {
        'base_url': 'https://api.openai.com',
        'paths': ['/v1/chat/completions', '/v1/completions', '/v1/embeddings'],
    },
    'anthropic': {
        'base_url': 'https://api.anthropic.com',
        'paths': ['/v1/messages'],
    },
    'minimax': {
        'base_url': 'https://api.minimaxi.com',
        'paths': ['/v1/chat/completions', '/v1/text/chatcompletion_v2'],
    },
    'volcengine': {
        'base_url': 'https://ark.cn-beijing.volces.com',
        'paths': ['/api/v3/chat/completions'],
    },
    'zhipu': {
        'base_url': 'https://open.bigmodel.cn',
        'paths': ['/api/paas/v4/chat/completions'],
    },
    'qwen': {
        'base_url': 'https://dashscope.aliyuncs.com',
        'paths': ['/compatible-mode/v1/chat/completions'],
    },
    'deepseek': {
        'base_url': 'https://api.deepseek.com',
        'paths': ['/v1/chat/completions'],
    },
    'moonshot': {
        'base_url': 'https://api.moonshot.cn',
        'paths': ['/v1/chat/completions'],
    },
    'baichuan': {
        'base_url': 'https://api.baichuan-ai.com',
        'paths': ['/v1/chat/completions'],
    },
    'siliconflow': {
        'base_url': 'https://api.siliconflow.cn',
        'paths': ['/v1/chat/completions'],
    },
    'yi': {
        'base_url': 'https://api.lingyiwanwu.com',
        'paths': ['/v1/chat/completions'],
    },
}


def _identify_agent(headers: Dict) -> str:
    ua = headers.get('User-Agent', '').lower()
    origin = headers.get('Origin', '').lower()
    referer = headers.get('Referer', '').lower()
    if 'trae' in ua or 'trae' in origin or 'trae' in referer:
        return 'trae'
    if 'codebuddy' in ua or 'codebuddy' in origin or 'codebuddy' in referer:
        return 'codebuddy'
    if 'claude-code' in ua:
        return 'claude-code'
    if 'codex' in ua:
        return 'codex'
    if 'cursor' in ua:
        return 'cursor'
    if 'windsurf' in ua:
        return 'windsurf'
    return ''


def _extract_model(body: Any) -> str:
    if isinstance(body, dict):
        return body.get('model', body.get('modelId', ''))
    return ''


def _extract_usage(response_data: Any) -> Dict:
    if not isinstance(response_data, dict):
        return {}
    u = response_data.get('usage', {})
    if not u:
        return {}
    return {
        'input_tokens': u.get('prompt_tokens', u.get('input_tokens', 0)),
        'output_tokens': u.get('completion_tokens', u.get('output_tokens', 0)),
        'total_tokens': u.get('total_tokens', 0),
    }


def _extract_usage_from_chunk(chunk_data: Any) -> Dict:
    if not isinstance(chunk_data, dict):
        return {}
    u = chunk_data.get('usage', {})
    if u:
        return {
            'input_tokens': u.get('prompt_tokens', u.get('input_tokens', 0)),
            'output_tokens': u.get('completion_tokens', u.get('output_tokens', 0)),
            'total_tokens': u.get('total_tokens', 0),
        }
    msg = chunk_data.get('message', {})
    if isinstance(msg, dict):
        mu = msg.get('usage', {})
        if mu:
            return {
                'input_tokens': mu.get('input_tokens', mu.get('prompt_tokens', 0)),
                'output_tokens': mu.get('output_tokens', mu.get('completion_tokens', 0)),
            }
    return {}


def _record_usage(model: str, usage: Dict, agent: str, vendor: str):
    inp = usage.get('input_tokens', 0)
    out = usage.get('output_tokens', 0)
    if inp == 0 and out == 0:
        return
    if not model:
        model = f'{vendor}-unknown'
    try:
        counter.record(
            api_name=f'{vendor}-gateway',
            model=model,
            input_tokens=inp,
            output_tokens=out,
            metadata={'vendor': vendor, 'source': 'gateway', 'total_tokens': usage.get('total_tokens', 0)},
            agent=agent or 'unknown',
        )
        logger.info(f'Recorded: agent={agent} model={model} in={inp} out={out} vendor={vendor}')
    except Exception as e:
        logger.error(f'Record error: {e}')


async def handle_gateway(request: web.Request) -> web.Response:
    if request.method == 'OPTIONS':
        return web.Response(status=204, headers={
            'Access-Control-Allow-Origin': '*',
            'Access-Control-Allow-Methods': '*',
            'Access-Control-Allow-Headers': '*',
            'Access-Control-Max-Age': '86400',
        })

    path = request.path
    vendor = ''
    target_base = ''

    for v, route in _vendor_routes.items():
        if path in route['paths'] or any(path.startswith(p) for p in route['paths']):
            vendor = v
            target_base = route['base_url']
            break

    if not vendor:
        for v, route in _vendor_routes.items():
            if any(p in path for p in route['paths']):
                vendor = v
                target_base = route['base_url']
                break

    if not vendor:
        return web.Response(status=404, text=f'Unknown API path: {path}')

    target_url = f'{target_base}{path}'

    agent = _identify_agent(dict(request.headers))
    api_name = f'{vendor}-gateway'

    body = None
    model = ''
    is_streaming = False
    if request.method in ('POST', 'PUT', 'PATCH'):
        try:
            body = await request.read()
            try:
                body_json = json.loads(body)
                model = _extract_model(body_json)
                is_streaming = body_json.get('stream', False)
            except (json.JSONDecodeError, UnicodeDecodeError):
                pass
        except Exception:
            body = None

    fwd_headers = {}
    for k, v in request.headers.items():
        kl = k.lower()
        if kl in ('host', 'connection', 'transfer-encoding', 'content-length'):
            continue
        fwd_headers[k] = v

    timeout = ClientTimeout(total=GATEWAY_CONFIG['timeout'])
    try:
        ssl_ctx = ssl.create_default_context()
        async with ClientSession(timeout=timeout, connector=TCPConnector(ssl=ssl_ctx)) as session:
            if is_streaming:
                return await _handle_streaming(
                    request, session, target_url, fwd_headers, body,
                    vendor, agent, api_name, model
                )

            async with session.request(
                method=request.method, url=target_url,
                headers=fwd_headers, data=body, allow_redirects=True,
            ) as resp:
                resp_body = await resp.read()
                resp_headers = {}
                for k, v in resp.headers.items():
                    if k.lower() not in ('transfer-encoding', 'connection', 'content-encoding'):
                        resp_headers[k] = v

                try:
                    resp_json = json.loads(resp_body)
                    usage = _extract_usage(resp_json)
                    if usage:
                        _record_usage(model, usage, agent, vendor)
                except (json.JSONDecodeError, UnicodeDecodeError):
                    pass

                return web.Response(status=resp.status, body=resp_body, headers=resp_headers)
    except Exception as e:
        logger.error(f'Gateway error for {target_url}: {e}')
        return web.Response(status=502, text=f'Gateway error: {str(e)}')


async def _handle_streaming(
    request, session, target_url, headers, body,
    vendor, agent, api_name, model
) -> web.Response:
    response = web.StreamResponse(status=200, headers={
        'Content-Type': 'text/event-stream',
        'Cache-Control': 'no-cache',
        'Connection': 'keep-alive',
        'Access-Control-Allow-Origin': '*',
    })
    await response.prepare(request)

    accumulated_usage = {}
    try:
        async with session.request(
            method=request.method, url=target_url,
            headers=headers, data=body, allow_redirects=True,
        ) as resp:
            async for chunk in resp.content.iter_any():
                await response.write(chunk)
                try:
                    chunk_text = chunk.decode('utf-8', errors='replace')
                    for line in chunk_text.split('\n'):
                        line = line.strip()
                        if line.startswith('data: '):
                            data_str = line[6:]
                            if data_str == '[DONE]':
                                continue
                            try:
                                chunk_json = json.loads(data_str)
                                chunk_usage = _extract_usage_from_chunk(chunk_json)
                                for k, v in chunk_usage.items():
                                    if v:
                                        accumulated_usage[k] = accumulated_usage.get(k, 0) + v
                            except (json.JSONDecodeError, UnicodeDecodeError):
                                pass
                except Exception:
                    pass
    except Exception as e:
        logger.error(f'Streaming error: {e}')
    finally:
        if accumulated_usage:
            _record_usage(model, accumulated_usage, agent, vendor)
        await response.write_eof()

    return response


def create_app() -> web.Application:
    app = web.Application()
    app.router.add_route('*', '/{path:.*}', handle_gateway)
    return app


async def start_gateway_server(host: str, port: int):
    app = create_app()
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host, port)
    await site.start()
    logger.info(f'TokenLens Gateway listening on http://{host}:{port}')
    logger.info(f'Supported vendors: {list(_vendor_routes.keys())}')

    while True:
        await asyncio.sleep(3600)


def run_gateway(host: str = '127.0.0.1', port: int = 8899):
    GATEWAY_CONFIG['listen_host'] = host
    GATEWAY_CONFIG['listen_port'] = port
    asyncio.run(start_gateway_server(host, port))


def get_gateway_urls(host: str = '127.0.0.1', port: int = 8899) -> Dict[str, str]:
    return {
        vendor: f'http://{host}:{port}'
        for vendor in _vendor_routes
    }


if __name__ == '__main__':
    import sys
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8899
    run_gateway(port=port)
