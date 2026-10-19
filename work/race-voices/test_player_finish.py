import unittest
from flag_monitor import FlagMonitor

class FinishTests(unittest.TestCase):
    def test_missing_per_car_flags_waits_for_next_player_crossing(self):
        from flag_monitor import PlayerFinish
        t=PlayerFinish()
        base=dict(mode='race',state=4,completed=7,player_flags_available=False,player_checkered=False,position=4)
        self.assertFalse(t.update(base))
        self.assertFalse(t.update(dict(base,state=5)))
        self.assertTrue(t.update(dict(base,state=5,completed=8)))

    def test_player_flags_are_authoritative_before_crossing_fallback(self):
        from flag_monitor import PlayerFinish
        t=PlayerFinish()
        s=dict(mode='race',state=5,completed=7,player_flags_available=True,player_checkered=False)
        self.assertFalse(t.update(s))
        self.assertFalse(t.update(dict(s,completed=8)))
        self.assertTrue(t.update(dict(s,completed=8,player_checkered=True)))

    def test_missing_flags_overall_leader_can_finish_on_same_tick(self):
        from flag_monitor import PlayerFinish
        t=PlayerFinish()
        s=dict(mode='race',state=4,completed=7,player_flags_available=False,position=1)
        self.assertFalse(t.update(s))
        self.assertTrue(t.update(dict(s,state=5,completed=8)))
    def test_global_checkered_waits_for_player(self):
        m=FlagMonitor()
        self.assertEqual(m.update(1,0,player_finished=False),[])
        self.assertEqual(m.update(1,1,player_finished=False),[])
        lines=m.update(1,2,player_finished=True)
        self.assertEqual(len(lines),1)
        self.assertIn('本次比赛结束',lines[0])
        self.assertEqual(m.update(1,3,player_finished=True),[])

    def test_other_flags_still_work_before_player_finishes(self):
        lines=FlagMonitor().update(1|0x20,0,player_finished=False)
        self.assertEqual(len(lines),1);self.assertIn('蓝旗',lines[0])
