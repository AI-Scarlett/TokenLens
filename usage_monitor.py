import json
import logging
import os
import re
import threading
import time
from pathlib import Path
from typing import Dict, List, Optional
from datetime import datetime

from token_counter import TokenCounter

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger('tokenlens-monitor')

counter = TokenCounter()

HOME = Path.home()
TRAE_LOG_DIR = HOME / 'Library' / 'Application Support' / 'Trae CN' / 'logs'

_monitor_status = {
    'running': False,
    'trae_watching': False,
    'last_check': '',
    'events_captured': 0,
    'message': '',
}

_stop_event = threading.Event()

_processed_positions = {}


def _parse_trae_logs() -> List[Dict]:
    events = []
    if not TRAE_LOG_DIR.exists():
        return events

    for log_folder in sorted(TRAE_LOG_DIR.iterdir()):
        if not log_folder.is_dir():
            continue
        for log_file in log_folder.rglob('renderer*.log'):
            file_key = str(log_file)
            try:
                file_size = log_file.stat().st_size
                last_pos = _processed_positions.get(file_key, 0)
                if last_pos >= file_size:
                    continue

                current_model = ''
                current_session = ''

                with open(log_file, 'r', errors='replace') as f:
                    f.seek(last_pos)
                    for line in f:
                        line = line.strip()

                        model_match = re.search(r'"chat_model"\s*:\s*"([^"]+)"', line)
                        if model_match:
                            current_model = model_match.group(1)

                        session_match = re.search(r'"session_id"\s*:\s*"([^"]+)"', line)
                        if session_match:
                            current_session = session_match.group(1)

                        if 'report first token usage' in line.lower():
                            ts_match = re.match(r'(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})', line)
                            timestamp = ts_match.group(1) if ts_match else ''
                            events.append({
                                'type': 'token_usage',
                                'timestamp': timestamp,
                                'model': current_model,
                                'session_id': current_session,
                                'source': str(log_file),
                            })

                    _processed_positions[file_key] = f.tell()
            except Exception:
                pass

    return events


def _record_trae_log_event(event: Dict):
    model = event.get('model', '')
    if not model:
        model = 'unknown'
    timestamp = event.get('timestamp', '')
    session_id = event.get('session_id', '')

    try:
        counter.record(
            api_name='trae-log',
            model=model,
            input_tokens=0,
            output_tokens=0,
            metadata={
                'source': 'trae-log-monitor',
                'timestamp': timestamp,
                'session_id': session_id,
                'event_type': 'api_call',
            },
            agent='trae',
        )
        logger.info(f'Trae log event: model={model} session={session_id[:12]}')
    except Exception as e:
        logger.error(f'Record error: {e}')


def scan_trae_log_history() -> Dict:
    global _processed_positions
    _processed_positions = {}
    result = {'events': 0, 'models': {}, 'sessions': set()}

    events = _parse_trae_logs()
    result['events'] = len(events)

    for e in events:
        model = e.get('model', 'unknown')
        result['models'][model] = result['models'].get(model, 0) + 1
        if e.get('session_id'):
            result['sessions'].add(e['session_id'])

    result['sessions'] = len(result['sessions'])
    return result


def import_trae_log_history() -> Dict:
    global _processed_positions
    _processed_positions = {}
    result = {'imported': 0, 'skipped': 0, 'models': {}}

    events = _parse_trae_logs()
    seen = set()

    for e in events:
        model = e.get('model', 'unknown')
        session = e.get('session_id', '')
        ts = e.get('timestamp', '')
        key = f"{model}:{session}:{ts}"
        if key in seen:
            result['skipped'] += 1
            continue
        seen.add(key)

        _record_trae_log_event(e)
        result['imported'] += 1
        result['models'][model] = result['models'].get(model, 0) + 1

    return result


def start_monitor():
    global _monitor_status
    _monitor_status['running'] = True
    _monitor_status['trae_watching'] = TRAE_LOG_DIR.exists()
    _monitor_status['message'] = 'Monitor started'
    _stop_event.clear()

    logger.info('TokenLens Monitor started')

    while not _stop_event.is_set():
        try:
            _monitor_status['last_check'] = datetime.now().isoformat()
            events = _parse_trae_logs()
            for e in events:
                _record_trae_log_event(e)
                _monitor_status['events_captured'] = _monitor_status.get('events_captured', 0) + 1
            time.sleep(10)
        except Exception as e:
            _monitor_status['message'] = str(e)
            break

    _monitor_status['running'] = False
    _monitor_status['message'] = 'Monitor stopped'


def stop_monitor():
    _stop_event.set()


def get_monitor_status() -> Dict:
    return dict(_monitor_status)
