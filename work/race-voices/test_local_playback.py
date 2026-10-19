import tempfile,unittest
from pathlib import Path
from unittest.mock import patch

class LocalPlaybackTests(unittest.TestCase):
    def test_new_manual_reservation_while_waiting_still_blocks_native_delivery(self):
        from local_playback import claim,release,active
        from bridge import create_app
        from fastapi.testclient import TestClient
        from unittest.mock import Mock
        from concurrent.futures import ThreadPoolExecutor,TimeoutError
        import threading
        waiting=threading.Event();manual=threading.Event()
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);token=claim(root,5)
            service=Mock();service.voices={'furina':{},'clorinde':{}};service.speech.return_value=b'ID3-test'
            def gate(root):waiting.set();return active(root)
            with patch('bridge.ROOT',root),patch('opening_sequence.PRIORITY',root/'priority.json'),patch('local_playback.active',side_effect=gate),patch('button_priority.manual_active',side_effect=lambda *_:manual.is_set()),ThreadPoolExecutor(1) as pool:
                client=TestClient(create_app(service));future=pool.submit(client.post,'/v1/audio/speech',json=dict(voice='clorinde',input='剩余油量二十升。'))
                try:
                    self.assertTrue(waiting.wait(2));manual.set();release(root,token)
                    with self.assertRaises(TimeoutError):future.result(.15)
                finally:manual.clear();release(root,token)
                self.assertEqual(future.result(3).status_code,200)
    def test_playback_gate_lifetime_and_old_release_preserve_new_audio(self):
        from local_playback import claim,active,release
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            old=claim(root,5);self.assertTrue(active(root))
            new=claim(root,5);release(root,old);self.assertTrue(active(root))
            release(root,new);self.assertFalse(active(root))

    def test_crashed_owner_does_not_hold_native_audio(self):
        from local_playback import claim,active
        import psutil
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);claim(root,5)
            with patch('local_playback.psutil.Process',side_effect=psutil.NoSuchProcess(123)):
                self.assertFalse(active(root))

    def test_component_ordinary_response_waits_for_playback_completion(self):
        from local_playback import claim,release
        from bridge import create_app
        from fastapi.testclient import TestClient
        from unittest.mock import Mock
        from concurrent.futures import ThreadPoolExecutor
        import threading
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);token=claim(root,5)
            rendered=threading.Event()
            def speech(*args,**kwargs):rendered.set();return b'ID3-test'
            service=Mock();service.voices={'furina':{},'clorinde':{}};service.speech.side_effect=speech
            with patch('bridge.ROOT',root),patch('opening_sequence.PRIORITY',root/'priority.json'),ThreadPoolExecutor(1) as pool:
                client=TestClient(create_app(service))
                future=pool.submit(client.post,'/v1/audio/speech',json=dict(voice='clorinde',input='剩余油量二十升。'))
                self.assertTrue(rendered.wait(2))
                try:self.assertFalse(future.done())
                finally:release(root,token)
                self.assertEqual(future.result(3).status_code,200)
