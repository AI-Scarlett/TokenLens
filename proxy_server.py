import asyncio
import json
import logging
import socket
import ssl
import subprocess
import threading
import os
from pathlib import Path
from typing import Any, Dict
from concurrent.futures import ThreadPoolExecutor

from token_counter import TokenCounter

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger('tokenlens-proxy')

counter = TokenCounter()

HOME = Path.home()
CERT_DIR = HOME / '.tokenlens' / 'certs'
CA_KEY = CERT_DIR / 'ca-key.pem'
CA_CERT = CERT_DIR / 'ca-cert.pem'

_known_api_hosts = {
    'api.openai.com': 'openai',
    'api.b.ai': 'openai',
    'code.dme.one': 'openai',
    'api.anthropic.com': 'anthropic',
    'api.minimaxi.com': 'minimax',
    'api.minimax.chat': 'minimax',
    'ark.cn-beijing.volces.com': 'volcengine',
    'open.bigmodel.cn': 'zhipu',
    'dashscope.aliyuncs.com': 'qwen',
    'api.deepseek.com': 'deepseek',
    'api.moonshot.cn': 'moonshot',
    'api.baichuan-ai.com': 'baichuan',
    'api.siliconflow.cn': 'siliconflow',
    'api.lingyiwanwu.com': 'yi',
    'api.01.ai': 'yi',
}

PROXY_CONFIG = {
    'listen_host': '127.0.0.1',
    'listen_port': 8888,
    'timeout': 120,
    'mitm_enabled': True,
}

_executor = ThreadPoolExecutor(max_workers=4)


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


def _identify_vendor(host: str) -> str:
    return _known_api_hosts.get(host, 'unknown')


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


def _record_usage(model: str, usage: Dict, agent: str, vendor: str, api_name: str):
    inp = usage.get('input_tokens', 0)
    out = usage.get('output_tokens', 0)
    if inp == 0 and out == 0:
        return
    if not model:
        model = f'{vendor}-unknown'
    try:
        counter.record(
            api_name=api_name or f'{vendor}-proxy',
            model=model,
            input_tokens=inp,
            output_tokens=out,
            metadata={'vendor': vendor, 'source': 'proxy', 'total_tokens': usage.get('total_tokens', 0)},
            agent=agent or 'unknown',
        )
        logger.info(f'Recorded: agent={agent} model={model} in={inp} out={out} vendor={vendor}')
    except Exception as e:
        logger.error(f'Record error: {e}')


def _parse_usage_from_response(body: bytes, model: str, agent: str, vendor: str, api_name: str, is_streaming: bool):
    if is_streaming:
        accumulated_usage = {}
        text = body.decode('utf-8', errors='replace')
        for line in text.split('\n'):
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
        if accumulated_usage:
            _record_usage(model, accumulated_usage, agent, vendor, api_name)
    else:
        try:
            resp_json = json.loads(body)
            usage = _extract_usage(resp_json)
            if usage:
                _record_usage(model, usage, agent, vendor, api_name)
        except (json.JSONDecodeError, UnicodeDecodeError):
            pass


def ensure_ca_cert() -> bool:
    CERT_DIR.mkdir(parents=True, exist_ok=True)
    if CA_KEY.exists() and CA_CERT.exists():
        return True
    logger.info('Generating TokenLens CA certificate...')
    try:
        subprocess.run([
            'openssl', 'req', '-x509', '-new', '-nodes',
            '-newkey', 'rsa:2048',
            '-keyout', str(CA_KEY),
            '-out', str(CA_CERT),
            '-days', '3650',
            '-subj', '/CN=TokenLens CA/O=TokenLens',
        ], capture_output=True, check=True)
        logger.info(f'CA certificate generated: {CA_CERT}')
        return True
    except Exception as e:
        logger.error(f'Failed to generate CA certificate: {e}')
        return False


def generate_site_cert(hostname: str) -> bool:
    site_key = CERT_DIR / f'{hostname}-key.pem'
    site_cert = CERT_DIR / f'{hostname}-cert.pem'

    if site_cert.exists() and site_key.exists():
        return True

    try:
        csr_path = CERT_DIR / f'{hostname}-csr.pem'
        subprocess.run([
            'openssl', 'req', '-new', '-nodes',
            '-newkey', 'rsa:2048',
            '-keyout', str(site_key),
            '-out', str(csr_path),
            '-subj', f'/CN={hostname}',
        ], capture_output=True, check=True)

        ext_path = CERT_DIR / f'{hostname}-ext.cnf'
        with open(str(ext_path), 'w') as f:
            f.write(f'subjectAltName=DNS:{hostname}\n')
            f.write('basicConstraints=CA:FALSE\n')
            f.write('keyUsage=digitalSignature,keyEncipherment\n')

        subprocess.run([
            'openssl', 'x509', '-req',
            '-in', str(csr_path),
            '-CA', str(CA_CERT),
            '-CAkey', str(CA_KEY),
            '-CAcreateserial',
            '-out', str(site_cert),
            '-days', '365',
            '-extfile', str(ext_path),
        ], capture_output=True, check=True)

        csr_path.unlink(missing_ok=True)
        ext_path.unlink(missing_ok=True)
        return True
    except Exception as e:
        logger.error(f'Failed to generate cert for {hostname}: {e}')
        return False


def is_ca_trusted() -> bool:
    try:
        result = subprocess.run(
            ['security', 'find-certificate', '-a', '-c', 'TokenLens CA', '-p',
             '/Library/Keychains/System.keychain'],
            capture_output=True, text=True
        )
        return result.returncode == 0 and 'BEGIN CERTIFICATE' in result.stdout
    except:
        return False


def trust_ca() -> Dict:
    if not ensure_ca_cert():
        return {'success': False, 'message': 'CA 证书生成失败'}
    try:
        result = subprocess.run([
            'osascript', '-e',
            f'do shell script "security add-trusted-cert -d -r trustRoot -k /Library/Keychains/System.keychain {str(CA_CERT)}" with administrator privileges'
        ], capture_output=True, text=True, timeout=60)
        if result.returncode == 0:
            return {'success': True, 'message': 'CA 证书已安装到系统信任存储'}
        else:
            return {'success': False, 'message': f'安装失败: {result.stderr.strip() or "用户取消"}'}
    except subprocess.TimeoutExpired:
        return {'success': False, 'message': '安装超时'}
    except Exception as e:
        return {'success': False, 'message': str(e)}


def untrust_ca() -> Dict:
    try:
        subprocess.run([
            'osascript', '-e',
            f'do shell script "security delete-certificate -c \'TokenLens CA\' /Library/Keychains/System.keychain" with administrator privileges'
        ], capture_output=True, text=True, timeout=60)
        return {'success': True, 'message': 'CA 证书已从系统信任存储移除'}
    except Exception as e:
        return {'success': False, 'message': str(e)}


def set_system_proxy(host: str, port: int) -> bool:
    try:
        services = subprocess.check_output(
            ['networksetup', '-listallnetworkservices'], text=True
        ).strip().split('\n')[1:]
        for svc in services:
            svc = svc.strip()
            if not svc:
                continue
            subprocess.run(['networksetup', '-setwebproxy', svc, host, str(port)],
                           capture_output=True)
            subprocess.run(['networksetup', '-setsecurewebproxy', svc, host, str(port)],
                           capture_output=True)
        logger.info(f'System proxy set to {host}:{port}')
        return True
    except Exception as e:
        logger.error(f'Failed to set system proxy: {e}')
        return False


def clear_system_proxy() -> bool:
    try:
        services = subprocess.check_output(
            ['networksetup', '-listallnetworkservices'], text=True
        ).strip().split('\n')[1:]
        for svc in services:
            svc = svc.strip()
            if not svc:
                continue
            subprocess.run(['networksetup', '-setwebproxystate', svc, 'off'],
                           capture_output=True)
            subprocess.run(['networksetup', '-setsecurewebproxystate', svc, 'off'],
                           capture_output=True)
        logger.info('System proxy cleared')
        return True
    except Exception as e:
        logger.error(f'Failed to clear system proxy: {e}')
        return False


def set_trae_proxy(host: str, port: int) -> bool:
    try:
        settings_path = HOME / 'Library' / 'Application Support' / 'Trae CN' / 'User' / 'settings.json'
        if not settings_path.exists():
            settings_path = HOME / 'Library' / 'Application Support' / 'Trae' / 'User' / 'settings.json'
        if not settings_path.exists():
            return False
        with open(settings_path, 'r') as f:
            data = json.load(f)
        data['http.proxy'] = f'http://{host}:{port}'
        data['http.proxyStrictSSL'] = False
        data['http.proxySupport'] = 'on'
        with open(settings_path, 'w') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        logger.info(f'Trae proxy set to {host}:{port}')
        return True
    except Exception as e:
        logger.error(f'Failed to set Trae proxy: {e}')
        return False


def clear_trae_proxy() -> bool:
    try:
        settings_path = HOME / 'Library' / 'Application Support' / 'Trae CN' / 'User' / 'settings.json'
        if not settings_path.exists():
            settings_path = HOME / 'Library' / 'Application Support' / 'Trae' / 'User' / 'settings.json'
        if not settings_path.exists():
            return True
        with open(settings_path, 'r') as f:
            data = json.load(f)
        for key in ['http.proxy', 'http.proxyStrictSSL', 'http.proxySupport']:
            data.pop(key, None)
        with open(settings_path, 'w') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        return True
    except:
        return False


def _do_mitm(client_sock, target_host, target_port, agent, vendor):
    try:
        site_key = CERT_DIR / f'{target_host}-key.pem'
        site_cert = CERT_DIR / f'{target_host}-cert.pem'

        if not generate_site_cert(target_host):
            logger.error(f'MITM: Failed to generate cert for {target_host}')
            client_sock.close()
            return

        ssl_server_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ssl_server_ctx.load_cert_chain(str(site_cert), str(site_key))
        client_ssl = ssl_server_ctx.wrap_socket(client_sock, server_side=True)

        request_data = b''
        client_ssl.settimeout(30)
        try:
            while True:
                chunk = client_ssl.recv(65536)
                if not chunk:
                    break
                request_data += chunk
                if b'\r\n\r\n' in request_data:
                    header_end = request_data.find(b'\r\n\r\n')
                    content_length = 0
                    headers_text = request_data[:header_end].decode('utf-8', errors='replace')
                    for line in headers_text.split('\r\n'):
                        if line.lower().startswith('content-length:'):
                            content_length = int(line.split(':', 1)[1].strip())
                    body_so_far = len(request_data) - header_end - 4
                    if body_so_far >= content_length:
                        break
        except ssl.SSLError:
            client_ssl.close()
            return
        except Exception:
            client_ssl.close()
            return

        if not request_data:
            client_ssl.close()
            return

        api_name = f'{vendor}-proxy'
        model = ''
        is_streaming = False
        header_end = request_data.find(b'\r\n\r\n')
        headers_text = request_data[:header_end].decode('utf-8', errors='replace')
        body_data = request_data[header_end + 4:]

        first_line = headers_text.split('\r\n')[0]
        if 'POST' in first_line and body_data:
            try:
                body_json = json.loads(body_data)
                model = _extract_model(body_json)
                is_streaming = body_json.get('stream', False)
            except (json.JSONDecodeError, UnicodeDecodeError):
                pass

        ssl_client_ctx = ssl.create_default_context()
        remote_sock = ssl_client_ctx.wrap_socket(
            socket.socket(), server_hostname=target_host
        )
        remote_sock.settimeout(PROXY_CONFIG['timeout'])
        remote_sock.connect((target_host, target_port))
        remote_sock.sendall(request_data)

        response_data = b''
        try:
            while True:
                chunk = remote_sock.recv(65536)
                if not chunk:
                    break
                response_data += chunk
                client_ssl.sendall(chunk)
        except Exception:
            pass
        finally:
            try:
                remote_sock.close()
            except:
                pass

        if response_data and model:
            resp_header_end = response_data.find(b'\r\n\r\n')
            resp_body = response_data[resp_header_end + 4:] if resp_header_end >= 0 else response_data
            _parse_usage_from_response(resp_body, model, agent, vendor, api_name, is_streaming)

        try:
            client_ssl.close()
        except:
            pass

    except Exception as e:
        logger.error(f'MITM error for {target_host}: {e}')
        try:
            client_sock.close()
        except:
            pass


async def handle_client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
    try:
        request_line = await asyncio.wait_for(reader.readline(), timeout=30)
        if not request_line:
            writer.close()
            return

        request_line = request_line.decode('utf-8', errors='replace').strip()
        parts = request_line.split(' ', 2)
        if len(parts) < 2:
            writer.close()
            return

        method = parts[0]
        path = parts[1]

        headers = {}
        while True:
            line = await asyncio.wait_for(reader.readline(), timeout=10)
            if not line or line == b'\r\n' or line == b'\n':
                break
            line = line.decode('utf-8', errors='replace').strip()
            if ':' in line:
                key, value = line.split(':', 1)
                headers[key.strip()] = value.strip()

        body = b''
        content_length = int(headers.get('Content-Length', headers.get('content-length', 0)))
        if content_length > 0:
            body = await asyncio.wait_for(reader.readexactly(content_length), timeout=30)

        if method == 'CONNECT':
            host_port = path.split(':')
            target_host = host_port[0]
            target_port = int(host_port[1]) if len(host_port) > 1 else 443
            agent = _identify_agent(headers)
            vendor = _identify_vendor(target_host)
            is_llm_api = target_host in _known_api_hosts

            logger.info(f'CONNECT: {target_host}:{target_port} agent={agent} vendor={vendor} llm={is_llm_api}')

            if is_llm_api and PROXY_CONFIG.get('mitm_enabled') and ensure_ca_cert():
                writer.write(b'HTTP/1.1 200 Connection Established\r\n\r\n')
                await writer.drain()

                raw_sock = writer.transport.get_extra_info('socket')
                if raw_sock:
                    raw_sock.setblocking(False)
                    client_sock = raw_sock.dup()
                    loop = asyncio.get_event_loop()
                    await loop.run_in_executor(
                        _executor,
                        _do_mitm,
                        client_sock, target_host, target_port, agent, vendor
                    )
                else:
                    await _handle_tunnel(reader, writer, target_host, target_port)
            else:
                await _handle_tunnel(reader, writer, target_host, target_port)
        else:
            await _handle_http(reader, writer, method, path, headers, body)
    except Exception as e:
        logger.error(f'Client handler error: {e}')
        try:
            writer.close()
        except:
            pass


async def _handle_tunnel(reader, writer, target_host, target_port):
    try:
        remote_reader, remote_writer = await asyncio.wait_for(
            asyncio.open_connection(target_host, target_port),
            timeout=10
        )
    except Exception as e:
        logger.error(f'Tunnel: Failed to connect to {target_host}:{target_port}: {e}')
        writer.write(b'HTTP/1.1 502 Bad Gateway\r\n\r\n')
        await writer.drain()
        writer.close()
        return

    writer.write(b'HTTP/1.1 200 Connection Established\r\n\r\n')
    await writer.drain()

    async def pipe(r, w):
        try:
            while True:
                data = await r.read(65536)
                if not data:
                    break
                w.write(data)
                await w.drain()
        except Exception:
            pass
        finally:
            try:
                w.close()
            except:
                pass

    await asyncio.gather(pipe(reader, remote_writer), pipe(remote_reader, writer))


async def _handle_http(reader, writer, method, path, headers, body):
    host_header = headers.get('Host', headers.get('host', ''))
    if path.startswith('/'):
        target_url = f'https://{host_header}{path}' if host_header else f'https://{path.lstrip("/")}'
    else:
        target_url = path

    from urllib.parse import urlparse
    parsed = urlparse(target_url)
    host = parsed.hostname or ''
    port = parsed.port or 443
    path_part = parsed.path or '/'
    if parsed.query:
        path_part += f'?{parsed.query}'

    vendor = _identify_vendor(host)
    agent = _identify_agent(headers)
    api_name = f'{vendor}-proxy'

    model = ''
    is_streaming = False
    if body and method in ('POST', 'PUT', 'PATCH'):
        try:
            body_json = json.loads(body)
            model = _extract_model(body_json)
            is_streaming = body_json.get('stream', False)
        except (json.JSONDecodeError, UnicodeDecodeError):
            pass

    fwd_headers = {k: v for k, v in headers.items()
                   if k.lower() not in ('host', 'connection', 'transfer-encoding',
                                         'content-length', 'proxy-connection',
                                         'proxy-authorization')}

    try:
        ssl_ctx = ssl.create_default_context()
        remote_reader, remote_writer = await asyncio.wait_for(
            asyncio.open_connection(host, port, ssl=ssl_ctx),
            timeout=10
        )
    except Exception as e:
        logger.error(f'HTTP proxy: Failed to connect to {host}:{port}: {e}')
        writer.write(b'HTTP/1.1 502 Bad Gateway\r\n\r\n')
        await writer.drain()
        writer.close()
        return

    request_line = f'{method} {path_part} HTTP/1.1\r\n'
    request_line += f'Host: {host}\r\n'
    for k, v in fwd_headers.items():
        request_line += f'{k}: {v}\r\n'
    if body:
        request_line += f'Content-Length: {len(body)}\r\n'
    request_line += 'Connection: close\r\n\r\n'

    remote_writer.write(request_line.encode() + (body or b''))
    await remote_writer.drain()

    response_data = b''
    try:
        while True:
            chunk = await asyncio.wait_for(remote_reader.read(65536), timeout=PROXY_CONFIG['timeout'])
            if not chunk:
                break
            response_data += chunk
            writer.write(chunk)
            await writer.drain()
    except:
        pass
    finally:
        try:
            remote_writer.close()
        except:
            pass
        try:
            writer.close()
        except:
            pass

    if response_data and model:
        header_end = response_data.find(b'\r\n\r\n')
        resp_body = response_data[header_end + 4:] if header_end >= 0 else response_data
        _parse_usage_from_response(resp_body, model, agent, vendor, api_name, is_streaming)


async def start_proxy_server(host: str, port: int):
    server = await asyncio.start_server(handle_client, host, port)
    logger.info(f'TokenLens Proxy listening on {host}:{port}')
    logger.info(f'MITM enabled: {PROXY_CONFIG.get("mitm_enabled")}')
    logger.info(f'Known LLM API hosts: {list(_known_api_hosts.keys())}')
    async with server:
        await server.serve_forever()


def run_proxy(host: str = '127.0.0.1', port: int = 8888):
    PROXY_CONFIG['listen_host'] = host
    PROXY_CONFIG['listen_port'] = port
    ensure_ca_cert()
    asyncio.run(start_proxy_server(host, port))


if __name__ == '__main__':
    import sys
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8888
    run_proxy(port=port)
