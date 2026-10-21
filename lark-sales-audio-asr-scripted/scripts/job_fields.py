"""Resolve stable field IDs and synchronize durations/statuses without ASR calls."""
import json
import math
from pathlib import Path
import subprocess
import time

ROOT = Path(__file__).resolve().parent.parent
FAILURES = {'prepare_error', 'submit_error', 'asr_failed', 'asr_error', 'role_error', 'result_error', 'write_error'}
ACTIVE = {'submitted', 'pending', 'running', 'submit_uncertain', 'asr_uncertain'}


def cli(profile, action, *args):
    from pipeline import resolve_lark
    if action == '+field-list' and '--limit' not in args:
        args = (*args, '--limit', '200')
    cmd = [resolve_lark(), 'base', action, '--base-token', profile['base_token'], '--table-id', profile['table_id'], *args, '--as', 'user', '--format', 'json']
    for attempt in range(3):
        proc = subprocess.run(cmd, capture_output=True, text=True)
        value = json.loads(proc.stdout or proc.stderr)
        if not proc.returncode and value.get('ok'):
            return value['data']
        error = value.get('error') or {}
        transient = error.get('type') == 'network' or '1254291' in json.dumps(error)
        # This helper only performs reads and idempotent field-value upserts.
        if transient and attempt < 2:
            time.sleep(2 ** (attempt + 1))
            continue
        raise RuntimeError('飞书作业字段操作失败：' + json.dumps(error, ensure_ascii=False))


def refresh(run_dir=None, profile_name='default', config_path=None):
    path = Path(config_path) if config_path else ROOT / 'config.local.json'
    cfg = json.loads(path.read_text())
    state = None
    if run_dir:
        state = json.loads((Path(run_dir) / 'state.json').read_text())
        profile_name = state['profile_name']
    profile = cfg['profiles'][profile_name]
    if state and state['profile']['table_id'] != profile['table_id']:
        raise RuntimeError('运行批次与配置的数据表不一致，停止写入')
    fields = cli(profile, '+field-list')['fields']
    by_id = {f['id']: f for f in fields}
    by_name = {f['name']: f for f in fields}
    for key in ('transcript_field', 'duration_field', 'status_field'):
        fid = profile.get(key + '_id')
        if fid:
            if fid not in by_id:
                raise RuntimeError(f'已配置字段不存在：{fid}')
            profile[key] = by_id[fid]['name']
        elif profile.get(key):
            if profile[key] not in by_name:
                raise RuntimeError(f'已配置字段不存在：{profile[key]}')
            profile[key + '_id'] = by_name[profile[key]]['id']
    if profile.get('duration_field'):
        if by_id[profile['duration_field_id']]['type'] != 'number':
            raise RuntimeError('通话时长写入字段必须是秒数 number，不能写显示公式')
    if profile.get('status_field'):
        schema = by_id[profile['status_field_id']]
        if schema['type'] != 'select' or schema.get('multiple'):
            raise RuntimeError('转写状态必须是单选字段')
        if not {'成功', '失败'} <= {x['name'] for x in schema['options']}:
            raise RuntimeError('转写状态缺少成功/失败选项')
    path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + '\n')
    if run_dir:
        path = Path(run_dir) / 'state.json'
        for key in ('transcript_field', 'transcript_field_id', 'duration_field', 'duration_field_id', 'status_field', 'status_field_id'):
            if key in profile:
                state['profile'][key] = profile[key]
        path.write_text(json.dumps(state, ensure_ascii=False, indent=2) + '\n')
    return profile


def payload_for(profile, record):
    payload = {}
    duration = record.get('duration_seconds')
    if profile.get('duration_field') and isinstance(duration, (int, float)) and math.isfinite(duration) and duration > 0:
        payload[profile['duration_field']] = int(math.floor(duration + .5))
    if profile.get('status_field'):
        status = record.get('status')
        if status == 'written':
            payload[profile['status_field']] = '成功'
        elif status in FAILURES:
            payload[profile['status_field']] = '失败'
        elif status in ACTIVE:
            payload[profile['status_field']] = None
    return payload


def read_fields(profile, rid, names):
    args = ['--record-id', rid]
    for name in names:
        args += ['--field-id', name]
    value = cli(profile, '+record-get', *args)
    if len(value['data']) != 1:
        raise RuntimeError('作业记录复读不唯一')
    fields = dict(zip(value['fields'], value['data'][0]))
    # CLI单选读取为选项名数组，写入时使用字符串。
    status_name = profile.get('status_field')
    if status_name in fields and isinstance(fields[status_name], list):
        options = fields[status_name]
        fields[status_name] = options[0] if len(options) == 1 else None if not options else options
    return fields


def sync(run_dir):
    run_dir = Path(run_dir)
    path = run_dir / 'state.json'
    state = json.loads(path.read_text())
    profile = state['profile']
    if not profile.get('duration_field') and not profile.get('status_field'):
        return
    # Read a fresh projected snapshot once; changed rows still receive exact readback.
    names = [profile[k] for k in ('transcript_field', 'duration_field', 'status_field') if profile.get(k)]
    live = {}
    offset = 0
    while True:
        args = ['--limit', '200', '--offset', str(offset)]
        for name in names:
            args += ['--field-id', name]
        page = cli(profile, '+record-list', *args)
        for rid, row in zip(page['record_id_list'], page['data']):
            values = dict(zip(page['fields'], row))
            key = profile.get('status_field')
            if key in values and isinstance(values[key], list):
                values[key] = values[key][0] if len(values[key]) == 1 else None if not values[key] else values[key]
            live[rid] = values
        if not page.get('has_more'):
            break
        if not page['data']:
            raise RuntimeError('作业字段快照分页异常')
        offset += len(page['data'])
    for rid, record in state['records'].items():
        status = record.get('status')
        if status not in {'written'} | FAILURES | ACTIVE:
            continue
        payload = payload_for(profile, record)
        try:
            if rid not in live:
                raise RuntimeError('目标记录不存在于指定表，停止写入')
            current = live[rid]
            if status == 'written':
                expected = Path(record['transcript_path']).read_text().strip()
                actual = current[profile['transcript_field']]
                if not expected or actual != expected:
                    raise RuntimeError('转写正文与本地产物不一致，不能标成功')
            changed = {k: v for k, v in payload.items() if current.get(k) != v}
            if changed:
                cli(profile, '+record-upsert', '--record-id', rid, '--json', json.dumps(changed, ensure_ascii=False))
            actual = read_fields(profile, rid, payload) if changed else current
            if any(actual.get(k) != v for k, v in payload.items()):
                raise RuntimeError('时长或状态写后复读不一致')
            record['job_fields_verified'] = True
            record.pop('job_fields_error', None)
            print(json.dumps({'record_id': rid, 'job_fields': payload, 'verified': True}, ensure_ascii=False))
        except Exception as exc:
            record['job_fields_verified'] = False
            record['job_fields_error'] = str(exc)
            path.write_text(json.dumps(state, ensure_ascii=False, indent=2) + '\n')
            raise
        path.write_text(json.dumps(state, ensure_ascii=False, indent=2) + '\n')
