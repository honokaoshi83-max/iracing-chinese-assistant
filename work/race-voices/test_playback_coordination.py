import io,json,queue,tempfile,threading,unittest,wave
from pathlib import Path
from unittest.mock import Mock,patch

class PlaybackTests(unittest.TestCase):
    def test_brief_unknown_silent_marker_does_not_restart_lap(self):
        from telemetry_announcer import play_with_priority
        clock=[0.];plays=[];stops=[]
        def speaking():return bool(plays and .2<=clock[0]-plays[0]<.3)
        result=play_with_priority(lambda:plays.append(clock[0]),lambda:stops.append(clock[0]),speaking,1,0,25,
             now=lambda:clock[0],sleep=lambda dt:clock.__setitem__(0,clock[0]+dt),interrupt_delay=lambda:.35)
        self.assertTrue(result);self.assertEqual(len(plays),1);self.assertEqual(stops,[])
    def test_new_cached_speaking_marker_is_not_masked_by_old_ordinary_request(self):
        from opening_sequence import record_component_priority,component_interrupts_playback,complete_component_request
        with tempfile.TemporaryDirectory() as d,patch('opening_sequence.PRIORITY',Path(d)/'priority.json'):
            root=Path(d);flag=root/'Speaking.status';flag.write_text('ordinary')
            def marker(path=None):
                stat=flag.stat();return [stat.st_ino,stat.st_mtime_ns,stat.st_size]
            with patch('opening_sequence.component_marker',side_effect=marker):
                token=record_component_priority('剩余油量二十升。',False,pending=True)
                self.assertFalse(component_interrupts_playback(flag))
                flag.unlink();flag.write_text('cached safety')
                self.assertTrue(component_interrupts_playback(flag))
                complete_component_request(token)

    def test_completing_old_request_does_not_clear_new_pending_safety(self):
        from opening_sequence import record_component_priority,complete_component_request
        from button_priority import active_spotter_safety
        with tempfile.TemporaryDirectory() as d,patch('opening_sequence.PRIORITY',Path(d)/'priority.json'):
            first=record_component_priority('剩余油量二十升。',False,pending=True)
            record_component_priority('右侧有车。',False,pending=True)
            complete_component_request(first)
            self.assertTrue(active_spotter_safety())
    def test_pending_safety_is_not_overwritten_by_ordinary_request(self):
        from opening_sequence import record_component_priority,component_interrupts_playback
        from button_priority import active_spotter_safety
        with tempfile.TemporaryDirectory() as d,patch('opening_sequence.PRIORITY',Path(d)/'priority.json'):
            record_component_priority('左侧有车。',False,pending=True)
            record_component_priority('剩余油量二十升。',False,pending=True)
            self.assertTrue(active_spotter_safety())
            self.assertTrue(component_interrupts_playback())
    def test_suppressed_component_speaking_marker_does_not_cut_lap(self):
        from telemetry_announcer import Announcer
        from opening_sequence import record_component_priority
        a=Announcer.__new__(Announcer);a.items=Mock();a.items.get.side_effect=[(1,(0,0),'本圈一分零三秒。',False),StopIteration]
        a.error_latched=False
        stream=io.BytesIO()
        with wave.open(stream,'wb') as w:
            w.setnchannels(1);w.setsampwidth(2);w.setframerate(16000);w.writeframes(b'\x01\x00'*16000)
        a.synthesis=Mock();a.synthesis.response.return_value=Mock(content=stream.getvalue())
        clock=[0.];plays=[]
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);flag=root/'Speaking.status'
            def play(path,flags):
                plays.append(path)
                if path is not None:
                    flag.touch();record_component_priority('要生成赛后报告吗？',True)
            with patch('telemetry_announcer.ROOT',root),patch('telemetry_announcer.SC_TEMP',root),patch('opening_sequence.PRIORITY',root/'priority.json'),patch('opening_sequence.component_marker',side_effect=lambda path=None:[flag.stat().st_ino,flag.stat().st_mtime_ns,flag.stat().st_size] if flag.exists() else None),patch('telemetry_announcer.log'),patch('telemetry_announcer.time.monotonic',side_effect=lambda:clock[0]),patch('telemetry_announcer.time.sleep',side_effect=lambda dt:clock.__setitem__(0,clock[0]+dt)),patch('winsound.PlaySound',side_effect=play):
                with self.assertRaises(StopIteration):a.play()
        self.assertEqual(len(plays),1)
        self.assertIsNotNone(plays[0])
