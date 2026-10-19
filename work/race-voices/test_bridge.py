import concurrent.futures
import tempfile
import time
import unittest
from pathlib import Path

try:
    from bridge import VoiceService
except ImportError:
    VoiceService = None

class VoiceTests(unittest.TestCase):
    def test_native_review_evaluation_is_silent(self):
        from fastapi.testclient import TestClient
        from bridge import create_app
        client=TestClient(create_app(self.service))
        for text in ('芙宁娜：P 6 同组别，稳定但可提升。','但是，圈速很稳定。'):
            self.assertEqual(client.post('/v1/audio/speech',json=dict(voice='furina',input=text,response_format='mp3')).status_code,200)
        self.assertEqual(self.calls,[])
    def test_old_report_prompts_are_silent_without_synthesis(self):
        from fastapi.testclient import TestClient
        from bridge import create_app
        client=TestClient(create_app(self.service))
        for text in ('要生成赛后报告吗？','要保存设置并生成赛后报告吗？','赛后报告已经保存。'):
            response=client.post('/v1/audio/speech',json=dict(voice='furina',input=text,response_format='mp3'))
            self.assertEqual(response.status_code,200)
        self.assertEqual(self.calls,[])

    def test_review_notice_is_not_suppressed_and_native_evaluation_is_ignored(self):
        from fastapi.testclient import TestClient
        from bridge import create_app
        import json
        client=TestClient(create_app(self.service))
        response=client.post('/v1/audio/speech',json=dict(voice='furina',input='复盘文本已保存，请进入软件查看。',response_format='mp3',review=True))
        self.assertEqual(response.status_code,200);self.assertEqual(self.calls[-1][1],'复盘文本已保存，请进入软件查看。')
        client.post('/v1/audio/speech',json=dict(voice='furina',input='芙宁娜：P 6 同组别，稳定但可提升。',response_format='mp3'))
        self.assertFalse((Path(self.tmp.name)/'review_native_events.jsonl').exists())
        self.assertEqual(len(self.calls),1)
    def test_lap_time_always_uses_furina_in_dual_voice_mode(self):
        self.service.speech('clorinde','本圈一分零九秒七。比最快圈慢0.3秒。',1,'mp3')
        self.assertEqual(self.calls[-1][0],'furina')
        self.service.speech('clorinde','剩余油量二十升。',1,'mp3')
        self.assertEqual(self.calls[-1][0],'clorinde')
    def setUp(self):
        self.assertIsNotNone(VoiceService, 'Local speech adapter is missing')
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        from unittest.mock import patch
        for target,value in [('opening_sequence.PRIORITY',Path(self.tmp.name)/'priority.json'),
                             ('bridge.ROOT',Path(self.tmp.name))]:
            replacement=patch(target,value);replacement.start();self.addCleanup(replacement.stop)
        self.calls = []
        def render(voice, text, speed, fmt):
            self.calls.append((voice, text, speed, fmt))
            time.sleep(.01)
            return b'RIFF-test-wave' if fmt == 'wav' else b'ID3-test-mp3'
        self.service = VoiceService(Path(self.tmp.name), render, 'test-model-hash')

    def test_voices_are_independent_and_repeated_phrases_cached(self):
        a = self.service.speech('furina', '中文语音测试。', 1, 'wav')
        self.assertEqual(a, self.service.speech('furina', '中文语音测试。', 1, 'wav'))
        self.service.speech('ganyu', '中文语音测试。', 1, 'wav')
        self.assertEqual(len(self.calls), 2)

    def test_reading_is_normalized_before_render_and_cache(self):
        self.service.speech('furina','筑波2000，还可以跑131圈。',1,'wav')
        self.assertEqual(self.calls[-1][1],'筑波两千，还可以跑一百三十一圈。')
        self.service.speech('furina','筑波两千，还可以跑一百三十一圈。',1,'wav')
        self.assertEqual(len(self.calls),1)

    def test_old_pronunciation_cache_is_not_reused(self):
        import hashlib,json
        text='当前赛道是筑波。'
        key=hashlib.sha256(json.dumps(['test-model-hash','furina',text,1.0,'wav'],ensure_ascii=False).encode()).hexdigest()
        (self.service.cache/(key+'.wav')).write_bytes(b'old wrong pronunciation')
        self.assertNotEqual(self.service.speech('furina',text,1,'wav'),b'old wrong pronunciation')
        self.assertEqual(len(self.calls),1)

    def test_integer_and_float_speed_share_cache(self):
        self.service.speech('clorinde','轮胎检查。',1,'wav')
        self.service.speech('clorinde','轮胎检查。',1.0,'wav')
        self.assertEqual(len(self.calls),1)

    def test_simulator_controller_mp3_format(self):
        self.assertTrue(self.service.speech('clorinde', '准备进站。', 1, 'mp3').startswith(b'ID3'))
        self.assertEqual(self.calls[0][-1], 'mp3')

    def test_engineer_identity_and_cached_intro(self):
        self.service.speech('clorinde', '赛车状态和油量监控已就绪。', 1, 'wav')
        self.assertEqual(self.calls[-1][1], '我是克洛琳德，负责赛车状态、油量和维修提醒。')
        self.service.speech('clorinde', '我是Jona，系统检查完成。', 1, 'wav')
        self.assertIn('克洛琳德', self.calls[-1][1])
        self.assertNotIn('芙宁娜', self.calls[-1][1])

    def test_enabled_pair_excludes_ganyu(self):
        service = VoiceService(Path(self.tmp.name), self.service.render, 'pair', active_voice=('furina','clorinde'))
        self.assertEqual(service.voices, ('furina','clorinde'))
        with self.assertRaises(ValueError):
            service.speech('ganyu', '准备进站。', 1, 'wav')

    def test_concurrent_duplicate_requests_generate_once(self):
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda _: self.service.speech('ganyu', '左侧有车。', 1, 'wav'), range(4)))
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(len(set(results)), 1)

    def test_invalid_input_rejected_before_inference(self):
        for args in [('unknown','正常文本',1,'wav'), ('furina','',1,'wav'), ('furina','正常文本',0,'wav'), ('furina','正常文本',1,'exe')]:
            with self.assertRaises(ValueError):
                self.service.speech(*args)
        self.assertEqual(self.calls, [])

    def test_cache_changes_with_model_version(self):
        self.service.speech('furina', '准备进站。', 1, 'wav')
        other = VoiceService(Path(self.tmp.name), self.service.render, 'new-model-hash')
        other.speech('furina', '准备进站。', 1, 'wav')
        self.assertEqual(len(self.calls), 2)

    def test_openai_request_and_invalid_voice_response(self):
        from fastapi.testclient import TestClient
        from bridge import create_app
        client = TestClient(create_app(self.service))
        response = client.post('/v1/audio/speech', json={'model':'gpt-sovits', 'voice':'ganyu', 'input':'左侧有车。', 'response_format':'mp3'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers['content-type'], 'audio/mpeg')
        self.assertTrue(response.content.startswith(b'ID3'))
        response = client.post('/v1/audio/speech', json={'voice':'unknown', 'input':'左侧有车。'})
        self.assertEqual(response.status_code, 400)

    def test_native_opening_is_silent_but_ordered_opening_is_rendered(self):
        from fastapi.testclient import TestClient
        from bridge import create_app
        from opening_sequence import ENGINEER
        client=TestClient(create_app(self.service))
        payload={'voice':'clorinde','input':ENGINEER,'response_format':'wav'}
        response=client.post('/v1/audio/speech',json=payload)
        self.assertEqual(response.status_code,200)
        self.assertTrue(response.content.startswith(b'RIFF'))
        self.assertEqual(self.calls,[])
        payload['opening']=True
        from unittest.mock import patch
        with patch('wav_playback.finalize_wav',side_effect=lambda data:data):
            response=client.post('/v1/audio/speech',json=payload)
        self.assertEqual(response.status_code,200)
        self.assertTrue(self.calls)

    def test_single_voice_mode_rejects_model_switching(self):
        from fastapi.testclient import TestClient
        from bridge import create_app
        service = VoiceService(Path(self.tmp.name), self.service.render, 'single', active_voice='furina')
        client = TestClient(create_app(service))
        self.assertEqual(client.get('/health').json()['voices'], ['furina'])
        response = client.post('/v1/audio/speech', json={'voice':'ganyu', 'input':'左侧有车。'})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.calls, [])

if __name__ == '__main__':
    unittest.main()
