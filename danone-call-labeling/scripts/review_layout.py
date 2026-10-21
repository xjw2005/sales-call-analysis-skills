"""Deterministic presentation of saved label results; never calls a model."""
import re
import pipeline as labels

STATUSES=['有原话依据','规则默认','待复核','未输出','技术失败']
REASONS=['身份冲突','产品不清','回答含糊','证据不足','规则争议','技术异常']

def review_reasons(field,reviews,error=None):
    text='；'.join(x['reason'] for x in reviews)+('；'+error if error else '')
    found=set()
    if error:found.add('技术异常')
    if re.search(r'冲突|矛盾|不一致|身份漂移|自我修正|口误',text) and (field.startswith('A1 ') or re.search(r'身份|老板|店员|店长|采购|说话人',text)):found.add('身份冲突')
    if re.search(r'产品|版本|段位|领熠|亲熠|卓傲',text) and re.search(r'不清|不明|未明确|无法|混淆|不确定|未确认|指代',text):found.add('产品不清')
    if re.search(r'短答|复合|附和|含糊|模糊|礼貌|泛泛|未正面|简单回应|单独.*嗯|简短.*肯定',text):found.add('回答含糊')
    if re.search(r'C/D|C级|D级|C/D级|规则|标准|证据等级|边界|级规则',text):found.add('规则争议')
    if re.search(r'证据|不足|无法确定|不能确认|未提供|未明确|未提及|不详|无法判定|缺少',text):found.add('证据不足')
    if reviews and not found:found.add('证据不足')
    return [r for r in REASONS if r in found]

def cells(item,value,field,errors):
    result=value['result'];selected=result['labels'][field]
    review=[x for x in result['review_items'] if x['label']==field]
    evidence=[x for x in result['evidence'] if x['field']==field]
    if field in errors:status='技术失败'
    elif review:status='待复核'
    elif selected and evidence:status='有原话依据'
    elif selected:
        if not set(selected)<=labels.defaults(item['record']['project'],field):
            raise ValueError('没有原句依据的非默认标签，停止导出')
        status='规则默认'
    else:status='未输出'
    reasons=review_reasons(field,review,errors.get(field)) if status in {'待复核','技术失败'} else []
    by_id={u['id']:u for u in item['record']['utterances']};positions={u['id']:i for i,u in enumerate(item['record']['utterances'])}
    parts=[];seen=set()
    def add(uid,text):
        key=(uid,text)
        if key not in seen:
            seen.add(key);parts.append(f"[{item['times'][uid]}] {by_id[uid]['role']}：{text}")
    for e in evidence:
        uid=e['utterance_id'];source=by_id[uid]
        if source['role']!='客户' or e['quote'] not in source['text']:
            raise ValueError('校验依据不是客户连续原句')
        # Keep a complete conflicting sentence rather than clipping away its contradiction.
        quote=source['text'] if status=='待复核' else e['quote']
        contexts=[x for x in e['context_ids'] if x!=uid and by_id[x]['role'] in {'销售','坐席','渠道专员'}]
        needs_question=(len(quote.strip('，。！？、；：,.!? '))<=12 or re.match(r'^(这|那|他|她|它|对|嗯|是的|可以|没问题)',quote) or status=='待复核')
        if contexts and needs_question:
            prior=[x for x in contexts if positions[x]<positions[uid]]
            context=max(prior,key=lambda x:positions[x]) if prior else min(contexts,key=lambda x:abs(positions[x]-positions[uid]))
            add(context,by_id[context]['text'])
        add(uid,quote)
    # A review may quote a customer without selecting a label; recover only literal unique matches.
    if review and not evidence:
        for note in review:
            for quote in re.findall(r'“([^”\n]{2,})”',note['reason']):
                matches=[u for u in by_id.values() if u['role']=='客户' and quote in u['text']]
                if len(matches)==1:add(matches[0]['id'],matches[0]['text'])
    return {'status':status,'reasons':reasons,'evidence':'\n'.join(parts)}


def field_specs(project):
    """Definitions only: callers must explicitly create missing fields in Base."""
    specs = []
    for field in labels.fields_for(project):
        specs.extend([
            {'name': '判断状态·'+field, 'type': 'select', 'multiple': False,
             'options': [{'name': name} for name in STATUSES]},
            {'name': '复核原因·'+field, 'type': 'select', 'multiple': True,
             'options': [{'name': name} for name in REASONS]},
            {'name': '依据校验·'+field, 'type': 'text'},
        ])
    return specs


def view_order(visible, project):
    if not isinstance(visible, list) or any(not isinstance(x, str) for x in visible) or len(set(visible)) != len(visible):
        raise ValueError('视图字段必须是无重复的名称数组')
    names = set(labels.fields_for(project))
    metadata = {s['name'] for s in field_specs(project)}
    result = []
    for name in visible:
        if name in metadata:
            continue
        result.append(name)
        if name in names:
            result.extend(['判断状态·'+name, '复核原因·'+name, '依据校验·'+name])
    return result


def source_with_times(source):
    """Accept raw timestamped transcripts or a normalized record plus its time map."""
    if not isinstance(source, dict):
        raise ValueError('source必须是对象')
    raw = source.get('record', source)
    if raw.get('utterances') is not None and raw.get('transcript'):
        raise ValueError('只能提供一种话语来源，避免转写与时间戳错配')
    record = labels.normalize(raw)
    times = source.get('times')
    if times is None and isinstance(raw.get('transcript'), str):
        lines = [line for line in raw['transcript'].splitlines() if line.strip()]
        times = {f'U{i:04d}': labels.LINE.fullmatch(line)[1] for i, line in enumerate(lines, 1)}
    ids = {u['id'] for u in record['utterances']}
    if not isinstance(times, dict) or set(times) != ids:
        raise ValueError('需提供与全部话语ID一一对应的原始时间戳')
    clock = r'\d{2}:\d{2}(?::\d{2})?\.\d{3}'
    for value in times.values():
        if not isinstance(value, str) or not re.fullmatch(clock+'–'+clock, value):
            raise ValueError('时间戳需为原始起止时间，如00:01.000–00:03.000')
        def seconds(stamp):
            chunks = stamp.split(':')
            if float(chunks[-1]) >= 60 or (len(chunks) == 3 and int(chunks[-2]) >= 60):
                raise ValueError('时间戳秒数/分钟不合法')
            return float(chunks[-1])+int(chunks[-2])*60+(int(chunks[-3])*3600 if len(chunks)==3 else 0)
        start, end = value.split('–')
        if seconds(end) < seconds(start):
            raise ValueError('时间戳终点早于起点')
    return {'record': record, 'times': times}


def update_draft(table_id, bundles, fields, visible=None):
    """Validate source evidence again and export metadata + non-empty business labels."""
    import field_adapter
    binding = field_adapter.binding_for(table_id)
    report = field_adapter.check_schema(table_id, fields)
    if not report['compatible']:
        raise ValueError('业务字段schema已变化，停止导出')
    by_name = {f['name']: f for f in fields}
    for spec in field_specs(binding['project']):
        field = by_name.get(spec['name'])
        if not field or field.get('type') != spec['type']:
            raise ValueError('缺少或错误类型的展示字段：'+spec['name'])
        if spec['type']=='select':
            wanted = {x['name'] for x in spec['options']}
            actual = [x['name'] for x in field.get('options', [])]
            if field.get('multiple', False)!=spec['multiple'] or set(actual)!=wanted or len(actual)!=len(set(actual)):
                raise ValueError('展示字段多选属性或选项变化：'+spec['name'])
    updates, seen = {}, set()
    for bundle in bundles:
        item = source_with_times(bundle['source'])
        record, value = item['record'], bundle['analysis']
        rid = record['id']
        if rid in seen or rid != value.get('id') or record['project'] != binding['project']:
            raise ValueError('记录ID重复、不一致或跨项目')
        seen.add(rid)
        if value.get('standard_sha256') != labels.CATALOG['standard_sha256']:
            raise ValueError('规则版本不一致')
        names = list(value['result']['labels'])
        labels.validate_result(record, value['result'], names)
        errors = bundle.get('technical_errors', {})
        if not isinstance(errors, dict) or any(f not in names or not isinstance(e, str) or not e.strip() for f, e in errors.items()):
            raise ValueError('技术失败信息必须对应本次分析字段')
        reviewed = {r['label'] for r in value['result']['review_items']}
        if any(value['result']['labels'][f] or f not in reviewed for f in errors):
            raise ValueError('技术失败字段必须留空且列入复核')
        payload = field_adapter.update_draft(table_id, [value], fields)['update_records'].get(rid, {})
        for name in names:
            formatted = cells(item, value, name, errors)
            for prefix, key in [('判断状态·', 'status'), ('复核原因·', 'reasons'), ('依据校验·', 'evidence')]:
                payload[by_name[prefix+name]['id']] = formatted[key]
        updates[rid] = payload
    output = {'base_token': field_adapter.BINDINGS['base_token'], 'table_id': table_id,
              'standard_sha256': labels.CATALOG['standard_sha256'], 'update_records': updates}
    if visible is not None:
        output['view_config'] = {'visible_fields': view_order(visible, binding['project'])}
    return output


def main(argv=None):
    import argparse
    import json
    from pathlib import Path
    import field_adapter
    parser = argparse.ArgumentParser(description='Export four-column review layouts; no model calls or Base writes.')
    parser.add_argument('command', choices=['schema', 'export'])
    parser.add_argument('--project', choices=['p3m', 'lyqy', 'crown'])
    parser.add_argument('--table-id', choices=list(field_adapter.BINDINGS['tables']))
    parser.add_argument('--schema', type=Path, help='完整field-list成功JSON；省略时只读查询')
    parser.add_argument('--input', type=Path, help='每行一个source+analysis的NDJSON')
    parser.add_argument('--view-fields', type=Path, help='可选：当前可见字段名数组或CLI成功响应JSON')
    parser.add_argument('--output', type=Path, help='新建本地草稿，拒绝覆盖')
    args = parser.parse_args(argv)
    if args.command == 'schema':
        if not args.project:
            parser.error('schema需要--project')
        print(json.dumps(field_specs(args.project), ensure_ascii=False, indent=2))
        return 0
    if not args.table_id or not args.input or not args.output:
        parser.error('export需要--table-id、--input、--output')
    if args.output.exists():
        parser.error('输出已存在，请选择新文件')
    bundles = [json.loads(line) for line in args.input.read_text(encoding='utf-8-sig').splitlines() if line.strip()]
    if not bundles:
        raise ValueError('输入不能为空')
    visible = None
    if args.view_fields:
        visible = json.loads(args.view_fields.read_text(encoding='utf-8-sig'))
        if isinstance(visible, dict):
            if visible.get('ok') is not True:
                raise ValueError('视图读取失败')
            visible = visible['data']['visible_fields']
    draft = update_draft(args.table_id, bundles, field_adapter.read_schema(args.table_id, args.schema), visible)
    with args.output.open('x', encoding='utf-8') as stream:
        json.dump(draft, stream, ensure_ascii=False, indent=2)
        stream.write('\n')
    print(f'已生成{len(draft["update_records"])}条四列布局草稿；未调用模型、未写入飞书')
    return 0


if __name__ == '__main__':
    import sys
    try:
        sys.exit(main())
    except (ValueError, OSError, KeyError, TypeError) as error:
        print('展示草稿导出失败：'+str(error), file=sys.stderr)
        sys.exit(1)
