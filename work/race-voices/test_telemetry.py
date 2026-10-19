import unittest
from telemetry_announcer import LapTracker, TrackCatalog, clean_phrase
from pathlib import Path

class TelemetryTests(unittest.TestCase):
    def test_old_outlap_score_is_not_a_best_lap_baseline(self):
        t=LapTracker();t.update(0,55,0)
        self.assertEqual(t.update(1,65,65),'本圈一分零五秒零。')
    def test_identical_score_with_official_completed_lap_is_announced(self):
        t=LapTracker();t.update(1,65,0)
        self.assertIsNone(t.update(2,65,65))
        self.assertIn('本圈一分零五秒',t.update(2,65,65.25,score_lap=2,score_seconds=65))

    def test_outlap_incident_score_first_then_counter_still_announces_first(self):
        from telemetry_announcer import LapIncidents
        incidents=LapIncidents();incidents.update(0,0);incidents.update(0,1);incidents.update(1,1)
        t=LapTracker();t.update(0,0,0,current_lap=64)
        t.update(0,65,65,current_lap=.1)
        line=t.update(1,65,65.25,valid=incidents.valid(1),current_lap=.35)
        self.assertEqual(line,'本圈一分零五秒零。')
    def test_first_lap_score_before_completed_counter_is_not_lost(self):
        t=LapTracker();t.update(0,0,0,current_lap=64.9)
        self.assertIsNone(t.update(0,65.2,65,current_lap=.1))
        self.assertEqual(t.update(1,65.2,65.25,current_lap=.35),'本圈一分零五秒二。')

    def test_outlap_old_score_is_not_reused_after_a_full_lap(self):
        t=LapTracker();t.update(0,0,0)
        self.assertIsNone(t.update(0,55,2))
        self.assertIsNone(t.update(1,55,65))
        self.assertIn('本圈一分零五秒',t.update(1,65,65.25))
    def test_corrupted_farewell_does_not_synthesize_ini_entries(self):
        self.assertEqual(clean_phrase('鏈椹鹃┒宸茬粨鏉燂紝Bye.2=badBye.3=badBye.4=bad'),
                         '本次比赛结束，期待下一次合作。')
    def test_outlap_incidents_do_not_taint_first_timed_lap(self):
        from telemetry_announcer import LapIncidents
        incidents=LapIncidents()
        incidents.update(0,0)
        incidents.update(0,1)
        incidents.update(1,1)
        self.assertTrue(incidents.valid(1))
        t=LapTracker();t.update(0,0,0)
        self.assertNotIn('事故记录',t.update(1,65,1,valid=incidents.valid(1)))
        self.assertIn('慢1.0秒',t.update(2,66,2))

    def test_incident_survives_current_lap_advancing_before_completed(self):
        from telemetry_announcer import LapIncidents
        incidents=LapIncidents();incidents.update(1,0)
        incidents.update(1,1);incidents.update(2,1)
        self.assertFalse(incidents.valid(1))
        self.assertTrue(incidents.valid(2))

    def test_opening_retries_after_more_than_two_interruptions(self):
        from telemetry_announcer import play_with_priority
        clock=[0.0];plays=[]
        def sleep(seconds):clock[0]+=seconds
        def play():plays.append(clock[0])
        def speaking():
            if plays and len(plays)<=3 and clock[0]-plays[-1]<.6:
                return True
            return False
        self.assertTrue(play_with_priority(play,lambda:None,speaking,1,0,180,opening=True,
                                          now=lambda:clock[0],sleep=sleep))
        self.assertEqual(len(plays),4)

    def test_short_silent_status_does_not_interrupt_opening(self):
        from telemetry_announcer import play_with_priority
        clock=[0.];plays=[];stops=[]
        def sleep(dt):clock[0]+=dt
        def speaking():return bool(plays and .2<=clock[0]-plays[-1]<.4)
        result=play_with_priority(lambda:plays.append(clock[0]),lambda:stops.append(clock[0]),
              speaking,1,0,180,opening=True,now=lambda:clock[0],sleep=sleep)
        self.assertTrue(result);self.assertEqual(len(plays),1);self.assertEqual(stops,[])

    def test_disconnect_stops_opening_without_retry(self):
        from telemetry_announcer import play_with_priority
        clock=[0.];plays=[];stops=[]
        result=play_with_priority(lambda:plays.append(clock[0]),lambda:stops.append(clock[0]),
              lambda:False,5,0,180,opening=True,now=lambda:clock[0],
              sleep=lambda dt:clock.__setitem__(0,clock[0]+dt),cancelled=lambda:clock[0]>=3)
        self.assertFalse(result);self.assertEqual(len(plays),1);self.assertEqual(len(stops),1)

    def test_outlap_timer_boundary_with_same_sdk_lap(self):
        from telemetry_announcer import LapIncidents
        incidents=LapIncidents()
        incidents.update(1,0,0,10,0)
        incidents.update(1,1,0,20,0)
        self.assertFalse(incidents.valid(1))
        incidents.update(1,1,0,.1,0)
        self.assertTrue(incidents.valid(1))
        incidents.update(1,2,0,15,0)
        self.assertFalse(incidents.valid(1))

    def test_race_formation_is_excluded(self):
        from telemetry_announcer import lap_reporting_allowed
        for state in (0,1,2,3):self.assertFalse(lap_reporting_allowed("Race",state))
        for state in (4,5,6):self.assertTrue(lap_reporting_allowed("Race",state))
        self.assertTrue(lap_reporting_allowed("Practice",3))

    def test_track_layout_from_game_identifier(self):
        c = TrackCatalog(Path(__file__).resolve().parents[2]/'data/tracks_zh.json')
        self.assertIn('福斯', c.resolve('oulton fosters', 'Oulton Park Circuit', 'Fosters'))
        self.assertIn('奥尔顿', c.resolve('oulton fosters', 'Oulton Park Circuit', 'Fosters'))
        self.assertEqual('New Track — New Layout', c.resolve('unknown', 'New Track', 'New Layout'))

    def test_grip_is_not_a_track_name(self):
        self.assertEqual('赛道橡胶较少，抓地力偏低。', clean_phrase('赛道是 绿色。'))
        self.assertEqual('赛道抓地状态良好。', clean_phrase('赛道是 Fast。'))

    def test_completed_lap_once_with_timing_delay(self):
        t = LapTracker()
        self.assertIsNone(t.update(0, 0, 0))
        self.assertIsNone(t.update(1, 0, 1))
        self.assertEqual('本圈一分零九秒七。', t.update(1, 69.733, 1.4))
        self.assertIsNone(t.update(1, 69.733, 2))
        self.assertIsNone(t.update(2, 69.733, 3))
        self.assertIn('快0.7秒', t.update(2, 69, 3.2))

    def test_stale_time_is_not_announced_after_half_second(self):
        t=LapTracker();t.update(5,90,0)
        self.assertIsNone(t.update(6,90,1))
        self.assertIsNone(t.update(6,90,1.6))
        self.assertIn('本圈一分二十九秒',t.update(6,89,2))

    def test_outlap_pending_expires_before_next_lap(self):
        t=LapTracker();t.update(0,0,0)
        self.assertIsNone(t.update(1,0,1))
        self.assertIsNone(t.update(1,0,7))
        self.assertIsNone(t.update(1,70,60))
        self.assertIsNone(t.update(2,70,61))
        self.assertIn('本圈一分零九秒',t.update(2,69,61.5))

    def test_timer_reset_does_not_authorize_stale_score(self):
        t=LapTracker();t.update(1,90,0,current_lap=89.8)
        self.assertIsNone(t.update(2,90,1,current_lap=.1))
        self.assertIn('本圈一分三十一秒',t.update(2,91,1.25,current_lap=.35))
        self.assertIsNone(t.update(2,91,1.5,current_lap=.6))

    def test_same_score_expires_without_guessing(self):
        t=LapTracker();t.update(1,90,0,current_lap=89.8)
        self.assertIsNone(t.update(2,90,1,current_lap=.1))
        self.assertIsNone(t.update(2,90,7,current_lap=6.1))
        self.assertIsNone(t.pending)

    def test_reset_and_invalid_lap(self):
        t = LapTracker()
        t.update(0, 0, 0)
        self.assertIn('事故记录', t.update(1, 100, 1, valid=False))
        self.assertIsNone(t.best)
        self.assertIsNone(t.update(0, 0, 2))

    def test_rounding_carries_into_next_minute(self):
        t = LapTracker()
        t.update(0, 0, 0)
        self.assertEqual('本圈一分零零秒零。', t.update(1, 59.96, 1))

    def test_presenter_names_are_unified(self):
        self.assertEqual('我是芙宁娜。', clean_phrase('我是 Elisa。'))
        self.assertEqual('你好，我是芙宁娜。', clean_phrase('你好，我是Jona。'))

if __name__ == '__main__': unittest.main()
