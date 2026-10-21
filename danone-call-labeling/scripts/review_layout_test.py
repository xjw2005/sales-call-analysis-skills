"""Synthetic, offline regressions for filterable status/reason/evidence exports."""
import contextlib
import copy
import io
import json
from pathlib import Path
import tempfile
import unittest

import field_adapter as adapter
import review_layout as layout

TABLE = next(t for t, b in adapter.BINDINGS['tables'].items() if b['project']=='p3m')
FIELD = 'A2 是否本人决策'


def fields():
    result = [{'id': fid, 'name': name, 'type': 'select', 'multiple': True,
               'options': [{'name': o['value']} for o in adapter.pipeline.fields_for('p3m')[name]['options']]}
              for name, fid in adapter.binding_for(TABLE)['fields'].items()]
    result.extend({'id': f'fldMeta{i}', **spec} for i, spec in enumerate(layout.field_specs('p3m')))
    return result


def bundle():
    source = {'record': {'id': 'recSynthetic1', 'project': 'p3m', 'transcript_complete': True,
              'utterances': [{'id': 'U1', 'role': '渠道专员', 'text': '请问您负责决定采购吗？'},
                             {'id': 'U2', 'role': '客户', 'text': '我不能决定采购，请直接联系采购负责人。'}]},
              'times': {'U1': '00:00.000–00:02.000', 'U2': '00:02.000–00:05.000'}}
    value = {'id': 'recSynthetic1', 'standard_sha256': adapter.pipeline.CATALOG['standard_sha256'],
             'result': {'project': 'p3m', 'labels': {FIELD: ['拿不了主']}, 'review_items': [],
                        'evidence': [{'field': FIELD, 'option': '拿不了主', 'utterance_id': 'U2',
                         'quote': '我不能决定采购', 'product': '不适用', 'time_scope': '本次通话', 'context_ids': ['U1']}]},
             'business_fields': {'录音转写': 'must never export'}}
    return {'source': source, 'analysis': value}


class Tests(unittest.TestCase):
    def test_supported_tag_has_exact_quote_and_no_protected_updates(self):
        item = bundle(); before = copy.deepcopy(item)
        draft = layout.update_draft(TABLE, [item], fields())
        payload = draft['update_records']['recSynthetic1']
        by_name = {f['name']: f['id'] for f in fields()}
        self.assertEqual(payload[by_name['判断状态·'+FIELD]], '有原话依据')
        self.assertEqual(payload[by_name['复核原因·'+FIELD]], [])
        proof = payload[by_name['依据校验·'+FIELD]]
        self.assertIn('[00:02.000–00:05.000] 客户：我不能决定采购', proof)
        self.assertNotIn('匹配校验', proof)
        self.assertEqual(set(payload), {adapter.binding_for(TABLE)['fields'][FIELD],
                         by_name['判断状态·'+FIELD], by_name['复核原因·'+FIELD], by_name['依据校验·'+FIELD]})
        self.assertEqual(item, before)

    def test_review_suppresses_business_label_and_uses_filterable_reason(self):
        item = bundle();item['analysis']['result']['review_items'] = [
            {'label': FIELD, 'reason': '客户身份存在矛盾，需复核。'}]
        draft = layout.update_draft(TABLE, [item], fields())['update_records']['recSynthetic1']
        by_name = {f['name']: f['id'] for f in fields()}
        self.assertNotIn(adapter.binding_for(TABLE)['fields'][FIELD], draft)
        self.assertEqual(draft[by_name['判断状态·'+FIELD]], '待复核')
        self.assertIn('身份冲突', draft[by_name['复核原因·'+FIELD]])
        self.assertIn('请直接联系采购负责人。', draft[by_name['依据校验·'+FIELD]])

    def test_default_empty_and_failure_are_distinct(self):
        item = bundle();record = layout.source_with_times(item['source']);value=item['analysis']
        value['result'] = {'project': 'p3m', 'labels': {'A1 联系人身份': ['身份不明']}, 'evidence': [], 'review_items': []}
        self.assertEqual(layout.cells(record,value,'A1 联系人身份',{}), {'status':'规则默认','reasons':[],'evidence':''})
        value['result']['labels']={'B1 价格与利润':[]}
        self.assertEqual(layout.cells(record,value,'B1 价格与利润',{}), {'status':'未输出','reasons':[],'evidence':''})
        value['result']['labels']={FIELD:[]};value['result']['review_items']=[{'label':FIELD,'reason':'技术校验失败'}]
        item['technical_errors']={FIELD:'模型输出不完整'}
        draft=layout.update_draft(TABLE,[item],fields())['update_records']['recSynthetic1']
        by_name={f['name']:f['id'] for f in fields()}
        self.assertEqual(draft[by_name['判断状态·'+FIELD]],'技术失败')
        self.assertNotIn(adapter.binding_for(TABLE)['fields'][FIELD],draft)
        item['analysis']['result']['labels'][FIELD]=['拿不了主']
        with self.assertRaises(ValueError):layout.update_draft(TABLE,[item],fields())

    def test_wrong_quote_role_version_identity_and_times_are_rejected(self):
        original=bundle()
        for mutate in [lambda b:b['analysis']['result']['evidence'][0].update(quote='不存在的原话'),
                       lambda b:b['analysis']['result']['evidence'][0].update(utterance_id='U1',quote='请问您'),
                       lambda b:b['analysis'].update(standard_sha256='old'),
                       lambda b:b['analysis'].update(id='recOther'),
                       lambda b:b['source']['times'].pop('U2'),
                       lambda b:b['source']['times'].update(U2='00:06.000–00:02.000')]:
            item=copy.deepcopy(original);mutate(item)
            with self.assertRaises(ValueError):layout.update_draft(TABLE,[item],fields())
        with self.assertRaises(ValueError):layout.update_draft(TABLE,[original,original],fields())

    def test_schema_drift_is_rejected_and_view_order_is_stable(self):
        schema=fields();target=next(f for f in schema if f['name']=='判断状态·'+FIELD);target['multiple']=True
        with self.assertRaises(ValueError):layout.update_draft(TABLE,[bundle()],schema)
        visible=['编号',FIELD,'省份','依据校验·'+FIELD,'复核原因·'+FIELD]
        wanted=['编号',FIELD,'判断状态·'+FIELD,'复核原因·'+FIELD,'依据校验·'+FIELD,'省份']
        self.assertEqual(layout.view_order(visible,'p3m'),wanted)
        self.assertEqual(layout.view_order(wanted,'p3m'),wanted)

    def test_timestamped_raw_transcript_and_cli_need_no_network(self):
        item=bundle();item['source']={'id':'recSynthetic1','project':'p3m','transcript_complete':True,
            'transcript':'[00:00.000–00:02.000] 渠道专员：请问您负责决定采购吗？\n[00:02.000–00:05.000] 客户：我不能决定采购，请直接联系采购负责人。'}
        e=item['analysis']['result']['evidence'][0];e['utterance_id']='U0002';e['context_ids']=['U0001']
        with tempfile.TemporaryDirectory() as temp, contextlib.redirect_stdout(io.StringIO()):
            root=Path(temp);schema=root/'fields.json';source=root/'source.ndjson';target=root/'draft.json'
            schema.write_text(json.dumps({'ok':True,'data':{'fields':fields(),'total':len(fields())}}))
            source.write_text(json.dumps(item,ensure_ascii=False)+'\n')
            args=['export','--table-id',TABLE,'--schema',str(schema),'--input',str(source),'--output',str(target)]
            self.assertEqual(layout.main(args),0)
            self.assertIn('recSynthetic1',json.loads(target.read_text())['update_records'])
            with self.assertRaises(SystemExit):layout.main(args)


if __name__=='__main__':unittest.main(verbosity=2)
