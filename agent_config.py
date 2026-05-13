import json
import logging
import os
import shutil
import subprocess
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger('tokenlens-agent-config')

HOME = Path.home()


def _detect_vscode_agents() -> List[Dict]:
    results = []
    vscode_variants = [
        ('Trae CN', HOME / 'Library' / 'Application Support' / 'Trae CN' / 'User' / 'settings.json', 'trae'),
        ('Trae', HOME / 'Library' / 'Application Support' / 'Trae' / 'User' / 'settings.json', 'trae'),
        ('Cursor', HOME / 'Library' / 'Application Support' / 'Cursor' / 'User' / 'settings.json', 'cursor'),
        ('Windsurf', HOME / 'Library' / 'Application Support' / 'Windsurf' / 'User' / 'settings.json', 'windsurf'),
        ('VS Code', HOME / 'Library' / 'Application Support' / 'Code' / 'User' / 'settings.json', 'vscode'),
        ('Void', HOME / 'Library' / 'Application Support' / 'Void' / 'User' / 'settings.json', 'void'),
        ('PearAI', HOME / 'Library' / 'Application Support' / 'PearAI' / 'User' / 'settings.json', 'pearai'),
        ('Zed', HOME / '.config' / 'zed' / 'settings.json', 'zed'),
    ]
    for name, path, agent_id in vscode_variants:
        if path.exists():
            results.append({
                'name': name,
                'id': agent_id,
                'type': 'vscode',
                'path': str(path),
                'icon': _agent_icon(agent_id),
                'config_method': 'http.proxy',
            })
    return results


def _detect_standalone_agents() -> List[Dict]:
    results = []
    standalone = [
        ('CodeBuddy', 'codebuddy', HOME / '.codebuddy' / 'models.json', 'codebuddy', 'url_rewrite', '🔗'),
        ('Claude Code', 'claude_code', HOME / '.claude' / 'settings.json', 'claude_code', 'apiBaseUrl', '🤖'),
        ('Continue', 'continue', HOME / '.continue' / 'config.json', 'continue', 'apiBase', '▶️'),
        ('Aider', 'aider', HOME / '.aider.conf.yml', 'aider', 'openai-api-base', '🛠'),
        ('Cline', 'cline', HOME / '.cline' / 'settings.json', 'cline', 'apiBaseUrl', '🧵'),
        ('Amazon Q', 'amazon_q', HOME / '.amazonq' / 'config.json', 'amazon_q', 'apiEndpoint', '🔶'),
        ('Tabnine', 'tabnine', HOME / '.tabnine' / 'config.json', 'tabnine', 'apiEndpoint', '💡'),
        ('GitHub Copilot CLI', 'copilot_cli', HOME / '.config' / 'github-copilot' / 'config.json', 'copilot_cli', 'proxy', '🐙'),
    ]
    for name, agent_id, path, agent_type, config_method, icon in standalone:
        if path.exists():
            results.append({
                'name': name,
                'id': agent_id,
                'type': agent_type,
                'path': str(path),
                'icon': icon,
                'config_method': config_method,
            })
    return results


def _agent_icon(agent_id: str) -> str:
    icons = {
        'trae': '🌀',
        'cursor': '🖱️',
        'windsurf': '🏄',
        'vscode': '💻',
        'void': '⬛',
        'pearai': '🍐',
        'zed': '⚡',
    }
    return icons.get(agent_id, '📦')


def detect_all_agents() -> List[Dict]:
    agents = _detect_vscode_agents() + _detect_standalone_agents()
    for a in agents:
        a['configured'] = _is_agent_configured(a)
    return agents


def _is_agent_configured(agent: Dict) -> bool:
    path = Path(agent['path'])
    if not path.exists():
        return False
    try:
        atype = agent['type']
        if atype == 'vscode':
            with open(path) as f:
                data = json.load(f)
            return data.get('http.proxy', '').startswith('http://127.0.0.1:88')
        elif atype == 'codebuddy':
            with open(path) as f:
                data = json.load(f)
            models = data.get('models', [])
            return any(m.get('_original_url') for m in models if isinstance(m, dict))
        elif atype == 'claude_code':
            with open(path) as f:
                data = json.load(f)
            return data.get('apiBaseUrl', '').startswith('http://127.0.0.1')
        elif atype == 'continue':
            with open(path) as f:
                data = json.load(f)
            models = data.get('models', [])
            return any(m.get('_original_apiBase') for m in models if isinstance(m, dict))
        elif atype == 'aider':
            with open(path) as f:
                content = f.read()
            return '_original_openai_api_base' in content or 'openai-api-base: http://127.0.0.1:88' in content
        elif atype == 'cline':
            with open(path) as f:
                data = json.load(f)
            return data.get('apiBaseUrl', '').startswith('http://127.0.0.1')
        elif atype in ('amazon_q', 'tabnine', 'copilot_cli'):
            with open(path) as f:
                data = json.load(f)
            return '_tokenlens_configured' in data
    except Exception:
        pass
    return False


def _backup_config(path: Path) -> Optional[Path]:
    backup_path = path.parent / f'{path.name}.tokenlens_backup'
    if not backup_path.exists():
        try:
            shutil.copy2(path, backup_path)
            logger.info(f'Backed up {path} to {backup_path}')
            return backup_path
        except Exception as e:
            logger.error(f'Backup failed for {path}: {e}')
    return None


def configure_vscode_agent(agent: Dict, gateway_host: str, gateway_port: int) -> Dict:
    path = Path(agent['path'])
    try:
        _backup_config(path)
        with open(path, 'r') as f:
            data = json.load(f)
        gateway_url = f'http://{gateway_host}:{gateway_port}'
        data['http.proxy'] = gateway_url
        data['http.proxyStrictSSL'] = False
        data['http.proxySupport'] = 'on'
        with open(path, 'w') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        return {'agent': agent['name'], 'success': True, 'message': f'已配置代理: {gateway_url}'}
    except Exception as e:
        return {'agent': agent['name'], 'success': False, 'message': str(e)}


def unconfigure_vscode_agent(agent: Dict) -> Dict:
    path = Path(agent['path'])
    try:
        with open(path, 'r') as f:
            data = json.load(f)
        for key in ['http.proxy', 'http.proxyStrictSSL', 'http.proxySupport']:
            data.pop(key, None)
        with open(path, 'w') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        return {'agent': agent['name'], 'success': True, 'message': '已恢复原始配置'}
    except Exception as e:
        return {'agent': agent['name'], 'success': False, 'message': str(e)}


def configure_codebuddy(gateway_host: str, gateway_port: int) -> Dict:
    path = HOME / '.codebuddy' / 'models.json'
    if not path.exists():
        return {'agent': 'CodeBuddy', 'success': False, 'message': '配置文件不存在'}

    try:
        _backup_config(path)
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)

        gateway_url = f'http://{gateway_host}:{gateway_port}'
        changed = 0
        known_hosts = [
            'api.openai.com', 'api.anthropic.com', 'api.minimaxi.com',
            'api.minimax.chat', 'ark.cn-beijing.volces.com', 'open.bigmodel.cn',
            'dashscope.aliyuncs.com', 'api.deepseek.com', 'api.moonshot.cn',
            'api.baichuan-ai.com', 'api.siliconflow.cn', 'api.lingyiwanwu.com',
            'api.01.ai', 'code.dme.one', 'api.b.ai',
        ]

        for m in data.get('models', []):
            if not isinstance(m, dict):
                continue
            url = m.get('url', '')
            if not url or '_original_url' in m:
                continue
            for prefix in ['https://', 'http://']:
                if url.startswith(prefix):
                    rest = url[len(prefix):]
                    for host in known_hosts:
                        if rest.startswith(host):
                            path_part = rest[len(host):]
                            if not path_part or path_part == '/':
                                path_part = '/v1/chat/completions'
                            m['url'] = f'{gateway_url}{path_part}'
                            m['_original_url'] = url
                            changed += 1
                            break
                    break

        if changed > 0:
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False)

        return {'agent': 'CodeBuddy', 'success': True, 'message': f'已重定向 {changed} 个模型到网关', 'changed': changed}
    except Exception as e:
        return {'agent': 'CodeBuddy', 'success': False, 'message': str(e)}


def unconfigure_codebuddy() -> Dict:
    path = HOME / '.codebuddy' / 'models.json'
    if not path.exists():
        return {'agent': 'CodeBuddy', 'success': True, 'message': '配置文件不存在'}

    try:
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)

        restored = 0
        for m in data.get('models', []):
            if isinstance(m, dict) and '_original_url' in m:
                m['url'] = m['_original_url']
                del m['_original_url']
                restored += 1

        if restored > 0:
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False)

        return {'agent': 'CodeBuddy', 'success': True, 'message': f'已恢复 {restored} 个模型'}
    except Exception as e:
        return {'agent': 'CodeBuddy', 'success': False, 'message': str(e)}


def configure_claude_code(gateway_host: str, gateway_port: int) -> Dict:
    path = HOME / '.claude' / 'settings.json'
    if not path.exists():
        return {'agent': 'Claude Code', 'success': False, 'message': '配置文件不存在'}

    try:
        _backup_config(path)
        with open(path, 'r') as f:
            data = json.load(f)

        gateway_url = f'http://{gateway_host}:{gateway_port}'
        if 'apiBaseUrl' not in data:
            data['_original_apiBaseUrl'] = data.get('apiBaseUrl', '')
        data['apiBaseUrl'] = gateway_url

        with open(path, 'w') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

        return {'agent': 'Claude Code', 'success': True, 'message': f'已配置 apiBaseUrl: {gateway_url}'}
    except Exception as e:
        return {'agent': 'Claude Code', 'success': False, 'message': str(e)}


def unconfigure_claude_code() -> Dict:
    path = HOME / '.claude' / 'settings.json'
    if not path.exists():
        return {'agent': 'Claude Code', 'success': True, 'message': '配置文件不存在'}

    try:
        with open(path, 'r') as f:
            data = json.load(f)

        if '_original_apiBaseUrl' in data:
            orig = data['_original_apiBaseUrl']
            if orig:
                data['apiBaseUrl'] = orig
            else:
                data.pop('apiBaseUrl', None)
            del data['_original_apiBaseUrl']
        else:
            data.pop('apiBaseUrl', None)

        with open(path, 'w') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

        return {'agent': 'Claude Code', 'success': True, 'message': '已恢复原始配置'}
    except Exception as e:
        return {'agent': 'Claude Code', 'success': False, 'message': str(e)}


def configure_continue(gateway_host: str, gateway_port: int) -> Dict:
    path = HOME / '.continue' / 'config.json'
    if not path.exists():
        return {'agent': 'Continue', 'success': False, 'message': '配置文件不存在'}

    try:
        _backup_config(path)
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)

        gateway_url = f'http://{gateway_host}:{gateway_port}'
        changed = 0
        known_hosts = [
            'api.openai.com', 'api.anthropic.com', 'api.minimaxi.com',
            'ark.cn-beijing.volces.com', 'open.bigmodel.cn',
            'dashscope.aliyuncs.com', 'api.deepseek.com', 'api.moonshot.cn',
            'api.siliconflow.cn', 'api.lingyiwanwu.com',
        ]

        for m in data.get('models', []):
            if not isinstance(m, dict):
                continue
            api_base = m.get('apiBase', '')
            if not api_base or '_original_apiBase' in m:
                continue
            for prefix in ['https://', 'http://']:
                if api_base.startswith(prefix):
                    rest = api_base[len(prefix):]
                    for host in known_hosts:
                        if rest.startswith(host):
                            path_part = rest[len(host):]
                            if not path_part or path_part == '/':
                                path_part = '/v1/chat/completions'
                            m['apiBase'] = f'{gateway_url}{path_part}'
                            m['_original_apiBase'] = api_base
                            changed += 1
                            break
                    break

        if changed > 0:
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False)

        return {'agent': 'Continue', 'success': True, 'message': f'已重定向 {changed} 个模型到网关', 'changed': changed}
    except Exception as e:
        return {'agent': 'Continue', 'success': False, 'message': str(e)}


def unconfigure_continue() -> Dict:
    path = HOME / '.continue' / 'config.json'
    if not path.exists():
        return {'agent': 'Continue', 'success': True, 'message': '配置文件不存在'}

    try:
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)

        restored = 0
        for m in data.get('models', []):
            if isinstance(m, dict) and '_original_apiBase' in m:
                m['apiBase'] = m['_original_apiBase']
                del m['_original_apiBase']
                restored += 1

        if restored > 0:
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False)

        return {'agent': 'Continue', 'success': True, 'message': f'已恢复 {restored} 个模型'}
    except Exception as e:
        return {'agent': 'Continue', 'success': False, 'message': str(e)}


def configure_aider(gateway_host: str, gateway_port: int) -> Dict:
    path = HOME / '.aider.conf.yml'
    if not path.exists():
        return {'agent': 'Aider', 'success': False, 'message': '配置文件不存在'}

    try:
        _backup_config(path)
        with open(path, 'r') as f:
            content = f.read()

        gateway_url = f'http://{gateway_host}:{gateway_port}/v1'

        if 'openai-api-base:' in content:
            import re
            pattern = r'^(openai-api-base:\s*).+$'
            match = re.search(pattern, content, re.MULTILINE)
            if match:
                old_line = match.group(0)
                old_value = match.group(1).rstrip()
                old_url = old_line.split(':', 1)[1].strip()
                if old_url and not old_url.startswith('http://127.0.0.1:88'):
                    content = content.replace(old_line, f'openai-api-base: {gateway_url}')
                    content += f'\n# _original_openai_api_base: {old_url}'
        else:
            content += f'\nopenai-api-base: {gateway_url}'

        with open(path, 'w') as f:
            f.write(content)

        return {'agent': 'Aider', 'success': True, 'message': f'已配置 openai-api-base: {gateway_url}'}
    except Exception as e:
        return {'agent': 'Aider', 'success': False, 'message': str(e)}


def unconfigure_aider() -> Dict:
    path = HOME / '.aider.conf.yml'
    if not path.exists():
        return {'agent': 'Aider', 'success': True, 'message': '配置文件不存在'}

    try:
        with open(path, 'r') as f:
            content = f.read()

        import re
        orig_match = re.search(r'^#\s*_original_openai_api_base:\s*(.+)$', content, re.MULTILINE)
        if orig_match:
            original_url = orig_match.group(1).strip()
            content = re.sub(r'^openai-api-base:\s*.+$', f'openai-api-base: {original_url}', content, flags=re.MULTILINE)
            content = re.sub(r'^#\s*_original_openai_api_base:\s*.+\n?', '', content, flags=re.MULTILINE)
        else:
            content = re.sub(r'^openai-api-base:\s*http://127\.0\.0\.1:88[0-9]+/v1\s*\n?', '', content, flags=re.MULTILINE)

        with open(path, 'w') as f:
            f.write(content)

        return {'agent': 'Aider', 'success': True, 'message': '已恢复原始配置'}
    except Exception as e:
        return {'agent': 'Aider', 'success': False, 'message': str(e)}


def configure_generic_json(agent: Dict, gateway_host: str, gateway_port: int,
                           url_field: str = 'apiEndpoint') -> Dict:
    path = Path(agent['path'])
    if not path.exists():
        return {'agent': agent['name'], 'success': False, 'message': '配置文件不存在'}

    try:
        _backup_config(path)
        with open(path, 'r') as f:
            data = json.load(f)

        gateway_url = f'http://{gateway_host}:{gateway_port}'
        if url_field in data and not str(data[url_field]).startswith('http://127.0.0.1:88'):
            data[f'_original_{url_field}'] = data[url_field]
            data[url_field] = gateway_url
        data['_tokenlens_configured'] = True

        with open(path, 'w') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

        return {'agent': agent['name'], 'success': True, 'message': f'已配置 {url_field}: {gateway_url}'}
    except Exception as e:
        return {'agent': agent['name'], 'success': False, 'message': str(e)}


def unconfigure_generic_json(agent: Dict, url_field: str = 'apiEndpoint') -> Dict:
    path = Path(agent['path'])
    if not path.exists():
        return {'agent': agent['name'], 'success': True, 'message': '配置文件不存在'}

    try:
        with open(path, 'r') as f:
            data = json.load(f)

        orig_key = f'_original_{url_field}'
        if orig_key in data:
            data[url_field] = data[orig_key]
            del data[orig_key]
        data.pop('_tokenlens_configured', None)

        with open(path, 'w') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

        return {'agent': agent['name'], 'success': True, 'message': '已恢复原始配置'}
    except Exception as e:
        return {'agent': agent['name'], 'success': False, 'message': str(e)}


def configure_agent_by_name(agent_name: str, gateway_host: str, gateway_port: int) -> Dict:
    all_agents = detect_all_agents()
    target = None
    for a in all_agents:
        if a['name'] == agent_name or a['id'] == agent_name:
            target = a
            break

    if not target:
        return {'agent': agent_name, 'success': False, 'message': f'未找到 Agent: {agent_name}'}

    atype = target['type']
    if atype == 'vscode':
        return configure_vscode_agent(target, gateway_host, gateway_port)
    elif atype == 'codebuddy':
        return configure_codebuddy(gateway_host, gateway_port)
    elif atype == 'claude_code':
        return configure_claude_code(gateway_host, gateway_port)
    elif atype == 'continue':
        return configure_continue(gateway_host, gateway_port)
    elif atype == 'aider':
        return configure_aider(gateway_host, gateway_port)
    elif atype == 'cline':
        return configure_generic_json(target, gateway_host, gateway_port, 'apiBaseUrl')
    elif atype in ('amazon_q', 'tabnine', 'copilot_cli'):
        return configure_generic_json(target, gateway_host, gateway_port, target.get('config_method', 'apiEndpoint'))
    else:
        return configure_generic_json(target, gateway_host, gateway_port, 'apiEndpoint')


def unconfigure_agent_by_name(agent_name: str) -> Dict:
    all_agents = detect_all_agents()
    target = None
    for a in all_agents:
        if a['name'] == agent_name or a['id'] == agent_name:
            target = a
            break

    if not target:
        return {'agent': agent_name, 'success': False, 'message': f'未找到 Agent: {agent_name}'}

    atype = target['type']
    if atype == 'vscode':
        return unconfigure_vscode_agent(target)
    elif atype == 'codebuddy':
        return unconfigure_codebuddy()
    elif atype == 'claude_code':
        return unconfigure_claude_code()
    elif atype == 'continue':
        return unconfigure_continue()
    elif atype == 'aider':
        return unconfigure_aider()
    elif atype == 'cline':
        return unconfigure_generic_json(target, 'apiBaseUrl')
    elif atype in ('amazon_q', 'tabnine', 'copilot_cli'):
        return unconfigure_generic_json(target, target.get('config_method', 'apiEndpoint'))
    else:
        return unconfigure_generic_json(target, 'apiEndpoint')


def configure_all_agents(gateway_host: str, gateway_port: int) -> List[Dict]:
    results = []

    vscode_agents = _detect_vscode_agents()
    for agent in vscode_agents:
        results.append(configure_vscode_agent(agent, gateway_host, gateway_port))

    standalone_configs = [
        (HOME / '.codebuddy' / 'models.json', configure_codebuddy),
        (HOME / '.claude' / 'settings.json', configure_claude_code),
        (HOME / '.continue' / 'config.json', configure_continue),
        (HOME / '.aider.conf.yml', configure_aider),
    ]
    for path, config_fn in standalone_configs:
        if path.exists():
            results.append(config_fn(gateway_host, gateway_port))

    generic_agents = _detect_standalone_agents()
    for agent in generic_agents:
        if agent['type'] in ('cline', 'amazon_q', 'tabnine', 'copilot_cli'):
            results.append(configure_generic_json(
                agent, gateway_host, gateway_port,
                agent.get('config_method', 'apiEndpoint')
            ))

    return results


def unconfigure_all_agents() -> List[Dict]:
    results = []

    vscode_agents = _detect_vscode_agents()
    for agent in vscode_agents:
        results.append(unconfigure_vscode_agent(agent))

    standalone_unconfigs = [
        (HOME / '.codebuddy' / 'models.json', unconfigure_codebuddy),
        (HOME / '.claude' / 'settings.json', unconfigure_claude_code),
        (HOME / '.continue' / 'config.json', unconfigure_continue),
        (HOME / '.aider.conf.yml', unconfigure_aider),
    ]
    for path, unconfig_fn in standalone_unconfigs:
        if path.exists():
            results.append(unconfig_fn())

    generic_agents = _detect_standalone_agents()
    for agent in generic_agents:
        if agent['type'] in ('cline', 'amazon_q', 'tabnine', 'copilot_cli'):
            results.append(unconfigure_generic_json(
                agent, agent.get('config_method', 'apiEndpoint')
            ))

    return results
