"""Offline invariants for telesales requests, readback and safe retries."""
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import job_fields
import pipeline
import readability


class ProjectTests(unittest.TestCase):
    def test_display_changes_only_role_prefix(self):
        source = '[00:00.000–00:02.000] 销售：您说你们销售没来吗？'
        shown = pipeline.display_roles(source, {'role_display_names': {'销售': '渠道专员'}})
        self.assertEqual(shown, '[00:00.000–00:02.000] 渠道专员：您说你们销售没来吗？')
        self.assertEqual(pipeline.display_roles(source, {}), source)

    def test_request_hotwords_are_per_record_and_task_ids_are_reused(self):
        cfg = {'operator_id': 'las_asr_seed-2-0-lite', 'operator_version': 'v1',
               'hotwords': ['品牌', '品牌'], 'record_hotwords': {'r1': ['一店简称']},
               'asr_request': {'enable_punc': True, 'enable_ddc': False}}
        requests = []
        def fake(_, action, *args):
            if action == 'file-upload':
                return {'presigned_url': 'https://example.invalid/audio'}
            requests.append(json.loads(args[-1]))
            return {'task_id': 'task-' + str(len(requests)), 'task_status': 'PENDING'}
        with tempfile.TemporaryDirectory() as tmp, patch.object(pipeline, 'las_command', side_effect=fake):
            for rid, store in [('r1', '一店'), ('r2', '二店')]:
                folder = Path(tmp) / rid; folder.mkdir()
                record = {'record_id': rid, 'audio_path': 'fake.mp3', 'audio_format': 'mp3',
                          'hotword_context': {'门店名称': store}}
                pipeline.submit_one(cfg, record, folder)
                archived = json.loads((folder / 'request.json').read_text())
                self.assertNotIn('example.invalid', archived['audio']['url'])
            reused = {'task_id': 'saved', 'status': 'pending', 'record_id': 'r1',
                      'audio_path': 'fake.mp3', 'audio_format': 'mp3'}
            self.assertEqual(pipeline.submit_one(cfg, reused, Path(tmp))['task_id'], 'saved')
        words = [[v['word'] for v in json.loads(req['request']['corpus']['context'])['hotwords']] for req in requests]
        self.assertEqual(words, [['品牌', '一店', '一店简称'], ['品牌', '二店']])
        self.assertTrue(requests[0]['request']['show_utterances'])
        self.assertFalse(requests[0]['request']['enable_ddc'])

    def test_punctuation_keeps_words_numbers_and_negation(self):
        for changed in ['我已经同意', '我不买10', '我不买1，0罐']:
            with self.assertRaises(ValueError):
                readability.validate_insertion('我不买10罐', changed)
        self.assertEqual(readability.protect_negation('我不买10罐', '我不，买10罐。'), '我不买10罐。')
        with tempfile.TemporaryDirectory() as tmp:
            source = '[00:00.000–00:02.000] 渠道专员：对啊我不买10罐'
            call = lambda *_: json.dumps({'items': [{'id': 'U0001', 'text': '对啊，我不买10罐。'}]}, ensure_ascii=False)
            out = readability.punctuate({}, Path(tmp), source, call, json.loads)
            self.assertEqual(out, '[00:00.000–00:02.000] 渠道专员：对啊，我不买10罐。')

    def test_only_explicit_429_is_retried(self):
        failed = SimpleNamespace(returncode=1, stderr='http_status=429', stdout='')
        ok = SimpleNamespace(returncode=0, stderr='', stdout='{"task_id":"saved"}')
        with patch.dict(os.environ, {'LAS_API_KEY': 'offline-test'}), patch.object(pipeline, 'las_bin', return_value='fake'), patch.object(pipeline.time, 'sleep'):
            with patch.object(pipeline.subprocess, 'run', side_effect=[failed, ok]) as call:
                self.assertEqual(pipeline.las_command({'las_min_request_interval': 0}, 'submit')['task_id'], 'saved')
                self.assertEqual(call.call_count, 2)
            with patch.object(pipeline.subprocess, 'run', return_value=SimpleNamespace(returncode=1, stderr='connection lost', stdout='')) as call:
                with self.assertRaises(RuntimeError):
                    pipeline.las_command({'las_min_request_interval': 0}, 'submit')
                self.assertEqual(call.call_count, 1)

    def test_saved_profile_uses_own_ids_and_rejects_other_table(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); cfg = root / 'config.local.json'; run = root / 'run'; run.mkdir()
            cfg.write_text(json.dumps({'profiles': {'default': {'table_id': 'tblCore'}, 'lyqy': {
                'table_id': 'tblLyqy', 'transcript_field': '旧名', 'transcript_field_id': 'fldLyqy'}}}))
            state = {'profile_name': 'lyqy', 'profile': {'table_id': 'tblLyqy'}}
            (run / 'state.json').write_text(json.dumps(state))
            def fake(profile, *_):
                self.assertEqual(profile['table_id'], 'tblLyqy')
                return {'fields': [{'id': 'fldLyqy', 'name': '新转写名', 'type': 'text'}]}
            with patch.object(job_fields, 'cli', side_effect=fake):
                value = job_fields.refresh(run, config_path=cfg)
                self.assertEqual(value['transcript_field'], '新转写名')
                saved = json.loads((run / 'state.json').read_text())
                self.assertEqual(saved['profile']['transcript_field_id'], 'fldLyqy')
                saved['profile']['table_id'] = 'tblOther'; (run / 'state.json').write_text(json.dumps(saved))
                with self.assertRaises(RuntimeError): job_fields.refresh(run, config_path=cfg)

    def test_success_requires_exact_transcript_and_readback(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); transcript = root / 'text.txt'; transcript.write_text('原话')
            profile = {'table_id': 'tblTest', 'transcript_field': '正文', 'duration_field': '秒', 'status_field': '状态'}
            state = {'profile': profile, 'records': {'r1': {'status': 'written', 'duration_seconds': 60.5, 'transcript_path': str(transcript)}}}
            (root / 'state.json').write_text(json.dumps(state)); live = {'正文': '原话', '秒': None, '状态': None}; writes = []
            def fake(_, action, *args):
                if action == '+record-list': return {'fields': list(live), 'record_id_list': ['r1'], 'data': [list(live.values())], 'has_more': False}
                if action == '+record-upsert':
                    values = json.loads(args[args.index('--json')+1]); writes.append(values); live.update(values); return {}
                names = [args[i+1] for i, x in enumerate(args) if x == '--field-id']
                return {'fields': names, 'data': [[live[n] for n in names]]}
            with patch.object(job_fields, 'cli', side_effect=fake):
                job_fields.sync(root)
                self.assertEqual(writes, [{'秒': 61, '状态': '成功'}])
                job_fields.sync(root); self.assertEqual(len(writes), 1)
                live['正文'] = '其他文字'
                with self.assertRaises(RuntimeError): job_fields.sync(root)
                self.assertFalse(json.loads((root / 'state.json').read_text())['records']['r1']['job_fields_verified'])


if __name__ == '__main__': unittest.main()
