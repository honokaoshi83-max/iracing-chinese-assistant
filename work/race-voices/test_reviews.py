import json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from review_phrases import classify,SAVED
from review_store import save_review,recent_reviews
from session_review import ReviewCoordinator,time_trial_summary,session_mode,snapshot

def context(**kw):
    return dict(dict(key='race-1',mode='race',track='筑波2000',layout='2000',car='BMW M2',state=3,clock=10,
                     completed=0,position=6,class_position=2,cars=20,grid=8,official=False),**kw)

class ReviewTests(unittest.TestCase):
    def test_native_review_during_leader_checkered_cannot_end_player_race(self):
        self.c.observe(context(state=5,clock=300,player_checkered=False,time_remaining=0),1000)
        self.c.native(dict(id='early',kind='start',event='request',text='芙宁娜：P 4 ，下次再进一步。'),1001)
        self.assertIsNone(self.c.current.get('ended_at'))
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.sent=[];self.c=ReviewCoordinator(self.root,self.sent.append)
    def test_exact_review_phrases_and_save_prompts(self):
        self.assertEqual(classify('要生成赛后报告吗？')['kind'],'save')
        self.assertEqual(classify('赛后报告已经保存。')['kind'],'save')
        self.assertEqual(classify('芙宁娜：P 6 同组别，稳定但可提升。')['kind'],'start')
        self.assertEqual(classify('但是，圈速很稳定。')['kind'],'end')
        self.assertIsNone(classify('前方有慢车。'))
    def test_recent_five_do_not_delete_old_files_and_utf8_metadata(self):
        for i in range(7):save_review(self.root,dict(id=str(i),saved_at=f'2026-10-15T12:00:0{i}+09:00',track='筑波',mode='race',summary=['复盘内容']))
        recent=recent_reviews(self.root)
        self.assertEqual([r['id'] for r in recent],['6','5','4','3','2'])
        self.assertEqual(len(list((self.root/'复盘').glob('*.txt'))),7)
        self.assertIn('比赛模式',Path(recent[0]['path']).read_text(encoding='utf-8-sig'))
    def test_invalid_laps_are_counted_even_when_too_few_laps_to_rate(self):
        result=time_trial_summary([dict(seconds=100,valid=True),dict(seconds=100,valid=True),dict(seconds=100,valid=False)])
        self.assertEqual(result['invalid_laps'],1);self.assertEqual(result['stability'],'insufficient')
        self.assertIn('无效', ''.join(result['summary']));self.assertNotIn('排名',''.join(result['summary']))
        self.assertEqual(result['valid_average'],100)
    def test_pace_average_excludes_invalid_but_volatility_includes_it(self):
        result=time_trial_summary([dict(seconds=100,valid=True),dict(seconds=100,valid=True),dict(seconds=130,valid=False)])
        self.assertEqual(result['valid_average'],100);self.assertGreater(result['standard_deviation'],10)
    def test_zero_valid_laps_still_gets_honest_review(self):
        result=time_trial_summary([dict(seconds=100,valid=False),dict(seconds=102,valid=False)])
        self.assertIsNone(result['best_valid']);self.assertIn('没有有效圈速',''.join(result['summary']))
    def test_practice_qualify_replay_do_not_become_time_trial(self):
        self.assertEqual(session_mode('Time Trial','Lone Qualify'),'time_trial')
        self.assertIsNone(session_mode('Race','Lone Qualify'))
        self.assertIsNone(session_mode('Practice','Practice'))
    def test_race_capture_completes_once_with_ranking_and_real_dates(self):
        self.c.observe(context(),1000);self.c.observe(context(state=4,clock=15),1005)
        self.c.observe(context(state=6,clock=195,completed=2,official=True),1185)
        self.c.native(dict(event='request',id='a',kind='start',text='芙宁娜：P 6 同组别，稳定但可提升。',time=1185),1185)
        self.c.native(dict(event='ready',id='a',kind='start',text='',time=1200),1200)
        self.c.native(dict(event='request',id='b',kind='end',text='但是，圈速很稳定。',time=1201),1201)
        self.c.tick(1300);self.assertEqual(recent_reviews(self.root),[]) # still synthesizing
        self.c.native(dict(event='ready',id='b',kind='end',text='',time=1301),1301)
        self.c.tick(1304);self.c.tick(1400)
        r=recent_reviews(self.root);self.assertEqual(len(r),1)
        content=Path(r[0]['path']).read_text(encoding='utf-8-sig')
        self.assertIn('总排名：6',content);self.assertIn('组别排名：2',content)
        self.assertIn('3分00秒',content);self.assertIn('+',r[0]['started_at'])
        self.assertEqual([str(t) for t in self.sent],[SAVED])
    def test_failed_save_never_announces_saved_and_retries(self):
        self.c.observe(context(mode='time_trial',state=4),1000)
        self.c.add_lap(1,100,True);self.c.add_lap(2,101,False)
        self.c.end('会话结束',1210)
        with patch('session_review.save_review',side_effect=OSError('disk full')):self.c.tick(1250)
        self.assertEqual(self.sent,[])
        self.c.tick(1255);self.assertEqual(len(recent_reviews(self.root)),1);self.assertEqual(str(self.sent[-1]),SAVED)
    def test_time_trial_no_rank_and_restart_does_not_duplicate(self):
        self.c.observe(context(mode='time_trial',state=4),1000)
        self.c.add_lap(1,100,True);self.c.add_lap(2,102,False);self.c.end('退出会话',1205);self.c.tick(1212)
        r=recent_reviews(self.root)[0];content=Path(r['path']).read_text(encoding='utf-8-sig')
        self.assertNotIn('总排名',content);self.assertIn('比赛总用时：未知',content)
        self.assertIn('赛道：筑波2000',content);self.assertIn('比赛模式：Time Trial',content)
        other=ReviewCoordinator(self.root,self.sent.append);other.tick(1300)
        self.assertEqual(len(recent_reviews(self.root)),1)
    def test_pit_laps_and_outlaps_not_counted(self):
        self.c.observe(context(mode='time_trial',state=4),1000)
        self.c.add_lap(0,120,False);self.c.add_lap(1,150,False,pit=True)
        self.c.add_lap(2,100,True);self.c.end('会话结束',1200);self.c.tick(1210)
        self.assertEqual(recent_reviews(self.root),[])
    def test_short_disconnect_can_resume_without_premature_report(self):
        self.c.observe(context(mode='time_trial',state=4),1000)
        self.c.add_lap(1,100,True);self.c.add_lap(2,101,True)
        self.c.disconnect(1200);self.c.tick(1210)
        self.assertEqual(recent_reviews(self.root),[])
        self.c.observe(context(mode='time_trial',state=4,clock=220,completed=2),1211)
        self.c.tick(1250);self.assertEqual(recent_reviews(self.root),[])
    def test_disconnect_finalizes_after_grace_period(self):
        self.c.observe(context(mode='time_trial',state=4),1000)
        self.c.add_lap(1,100,True);self.c.add_lap(2,101,True)
        self.c.disconnect(1200);self.c.tick(1230)
        self.assertEqual(len(recent_reviews(self.root)),1)
    def test_snapshot_handles_empty_results_and_preserves_official_rank_numbering(self):
        ir=dict(WeekendInfo={'EventType':'Time Trial'},SessionInfo={'Sessions':[dict(SessionNum=0,SessionType='Lone Qualify',ResultsPositions=None)]},
                DriverInfo={'DriverCarIdx':0,'Drivers':[dict(CarIdx=0,CarScreenName='BMW')]},SessionNum=0)
        result=snapshot(ir,'筑波');self.assertEqual(result['mode'],'time_trial')
        ir['SessionInfo']['Sessions'][0].update(SessionType='Race',ResultsOfficial=1,ResultsPositions=[dict(CarIdx=0,Position=6,ClassPosition=1)])
        ir['WeekendInfo']['EventType']='Race'
        result=snapshot(ir,'筑波');self.assertEqual(result['position'],6);self.assertEqual(result['class_position'],2)
    def test_sampler_accepts_score_before_counter_and_confirmed_identical_laps(self):
        self.c.observe(context(mode='time_trial',state=4,last_seconds=0),1000)
        self.c.observe(context(mode='time_trial',state=4,last_seconds=100),1100)
        self.c.observe(context(mode='time_trial',state=4,last_seconds=100,completed=1,lap_valid=True),1101)
        self.c.observe(context(mode='time_trial',state=4,last_seconds=100,completed=1,lap_valid=True),1102)
        self.c.observe(context(mode='time_trial',state=4,last_seconds=100,completed=2,score_lap=2,score_seconds=100,lap_valid=False),1200)
        self.c.observe(context(mode='time_trial',state=4,last_seconds=100,completed=2,score_lap=2,score_seconds=100,lap_valid=False),1201)
        self.assertEqual(len(self.c.current['laps']),2)
        self.assertFalse(self.c.current['laps'][1]['valid'])
    def test_simulator_still_running_disconnect_does_not_end_time_trial(self):
        self.c.observe(context(mode='time_trial',state=4),1000)
        self.c.add_lap(1,100,True);self.c.add_lap(2,101,False)
        self.c.disconnect(1200,simulator_running=True);self.c.tick(1300)
        self.assertEqual(recent_reviews(self.root),[])
        self.c.observe(context(mode='time_trial',state=4,clock=330),1301)
        self.assertIsNone(self.c.current.get('ended_at'))
    def test_outlap_timer_reset_does_not_exclude_first_complete_timed_lap(self):
        self.c.observe(context(mode='time_trial',state=4,pit=True,sdk_lap=1,last_seconds=0,current_time=50),1000)
        self.c.observe(context(mode='time_trial',state=4,pit=False,sdk_lap=1,last_seconds=0,current_time=.1),1050)
        for now in (1150,1151):self.c.observe(context(mode='time_trial',state=4,pit=False,sdk_lap=2,completed=1,last_seconds=100,current_time=.5,score_lap=1,score_seconds=100,lap_valid=True),now)
        self.assertEqual(len(self.c.current['laps']),1)
    def test_invalid_without_published_time_remains_in_incident_count(self):
        result=time_trial_summary([dict(seconds=100,valid=True),dict(seconds=100,valid=True),dict(seconds=None,valid=False)])
        self.assertEqual(result['invalid_laps'],1);self.assertEqual(result['stability'],'insufficient')

    def test_race_without_native_review_gets_local_comment_after_grace(self):
        self.c.observe(context(state=4),1000)
        self.c.add_lap(1,100,True);self.c.add_lap(2,101,False)
        self.c.end('会话结束',1210)
        self.c.tick(1220);self.assertEqual(recent_reviews(self.root),[])
        self.c.tick(1241)
        record=recent_reviews(self.root)[0]
        self.assertIn('下一场',''.join(record['summary']));self.assertEqual(record['stats']['invalid_laps'],1)
        self.assertEqual([str(line) for line in self.sent],[SAVED])

    def test_detailed_time_trial_comment_does_not_lengthen_existing_speech(self):
        self.c.observe(context(mode='time_trial',state=4),1000)
        for number,seconds in enumerate((102,101,100,99),1):self.c.add_lap(number,seconds,True)
        self.c.end('会话结束',1410);self.c.tick(1420)
        expected=time_trial_summary(self.c.current['laps'])['summary']
        self.assertEqual([str(line) for line in self.sent],['复盘文本已保存，请进入软件查看。'])
        self.assertIn('下一场',''.join(recent_reviews(self.root)[0]['summary']))

    def test_same_race_resume_after_exit_generates_updated_final_review(self):
        self.c.observe(context(state=4,clock=10),1000)
        self.c.add_lap(1,100,True);self.c.add_lap(2,101,False)
        self.c.observe(context(state=4,clock=220,completed=2),1210)
        self.c.disconnect(1210,simulator_running=False);self.c.tick(1250)
        previous=recent_reviews(self.root)[0]['id']
        self.c.observe(context(state=4,clock=320,completed=3),1310)
        self.assertFalse(self.c.current.get('saved',False));self.assertIsNone(self.c.current.get('ended_at'))
        self.c.add_lap(4,99,True)
        self.c.observe(context(state=6,clock=420,completed=4,position=3,position_source='SDK正式结果'),1410)
        self.c.tick(1441)
        newest=recent_reviews(self.root)[0]
        self.assertEqual(newest['previous_review_id'],previous);self.assertEqual(newest['position'],3)
        self.assertEqual(newest['stats']['observed_laps'],3)
        self.assertEqual(newest['observed_seconds'],310)
        self.assertIn('重连缺口',''.join(newest['summary']))

    def test_early_time_trial_exit_waits_for_last_sdk_countdown(self):
        self.c.observe(context(mode='time_trial',state=4,clock=0,time_remaining=1800),1000)
        self.c.add_lap(1,100,True);self.c.add_lap(2,101,False)
        self.c.observe(context(mode='time_trial',state=4,clock=1200,time_remaining=600,completed=2),2200)
        self.c.disconnect(2201,simulator_running=False)
        for now in (2230,2500,2799):self.c.tick(now)
        self.assertEqual(recent_reviews(self.root),[]);self.assertEqual(self.sent,[])
        restarted=ReviewCoordinator(self.root,self.sent.append)
        restarted.tick(2800);restarted.tick(2900)
        records=recent_reviews(self.root);self.assertEqual(len(records),1)
        row=records[0];self.assertTrue(row['left_early']);self.assertEqual(row['observed_seconds'],1200)
        self.assertEqual(row['stats']['observed_laps'],2);self.assertIsNone(row['total_seconds'])
        text=Path(row['path']).read_text(encoding='utf-8-sig')
        self.assertIn('预计赛事结束时间',text);self.assertIn('退出前',text)
        self.assertEqual(sum(str(x)==SAVED for x in self.sent),1)

    def test_waiting_review_survives_switch_to_other_session_and_ten_minute_cleanup(self):
        self.c.observe(context(mode='time_trial',state=4,time_remaining=1800),1000)
        self.c.add_lap(1,100,True);self.c.add_lap(2,101,True)
        self.c.observe(dict(context(key='practice',mode=None,state=4)),1100)
        self.c.tick(2100);self.assertEqual(recent_reviews(self.root),[])
        self.assertEqual(len(self.c.pending),1)
        self.c.tick(2800);self.assertEqual(len(recent_reviews(self.root)),1)

    def test_reconnect_before_expected_end_continues_same_record(self):
        self.c.observe(context(mode='time_trial',state=4,clock=0,time_remaining=1800),1000)
        original=self.c.current['id'];self.c.add_lap(1,100,True);self.c.add_lap(2,101,True)
        self.c.observe(context(mode='time_trial',state=4,clock=1200,time_remaining=600),2200)
        self.c.disconnect(2201,simulator_running=False);self.c.tick(2230)
        self.c.observe(context(mode='time_trial',state=4,clock=1300,time_remaining=500),2300)
        self.assertEqual(self.c.current['id'],original);self.assertIsNone(self.c.current.get('ended_at'))
        self.c.add_lap(3,99,True)
        self.c.observe(context(mode='time_trial',state=6,clock=1800,time_remaining=0,completed=3),2800)
        self.c.tick(2807)
        records=recent_reviews(self.root);self.assertEqual(len(records),1)
        self.assertFalse(records[0].get('left_early',False));self.assertEqual(records[0]['stats']['observed_laps'],3)

    def test_unlimited_remaining_is_not_treated_as_a_seven_day_timer(self):
        self.c.observe(context(mode='time_trial',state=4,time_remaining=604800),1000)
        self.c.add_lap(1,100,True);self.c.add_lap(2,101,True)
        self.c.disconnect(1200,simulator_running=False);self.c.tick(1230)
        self.assertEqual(len(recent_reviews(self.root)),1)
        self.assertIsNone(self.c.current.get('expected_end_wall'))

    def test_snapshot_reads_and_sanitizes_remaining_time(self):
        ir=dict(WeekendInfo={'EventType':'Time Trial'},SessionInfo={'Sessions':[]},SessionTimeRemain=600)
        self.assertEqual(snapshot(ir,'筑波')['time_remaining'],600)
        for bad in (604800,float('nan'),float('inf'),-1,None):
            ir['SessionTimeRemain']=bad
            self.assertIsNone(snapshot(ir,'筑波')['time_remaining'])

    def test_return_to_waiting_session_after_visiting_another_session(self):
        self.c.observe(context(mode='time_trial',state=4,clock=0,time_remaining=1800),1000)
        original=self.c.current['id'];self.c.add_lap(1,100,True);self.c.add_lap(2,101,True)
        self.c.observe(context(key='practice',mode=None,state=4,clock=0),1100)
        self.c.observe(context(mode='time_trial',state=4,clock=200,time_remaining=1600),1200)
        self.assertEqual(self.c.current['id'],original)
        self.assertEqual(len(self.c.current['laps']),2);self.assertEqual(self.c.pending,[])

    def test_native_review_cannot_bypass_countdown_but_confirmed_finish_can(self):
        self.c.observe(context(state=4,clock=1200,time_remaining=600),2200)
        self.c.add_lap(1,100,True);self.c.add_lap(2,101,True)
        self.c.native(dict(event='request',kind='start',id='native-start',text='原生复盘'),2201)
        self.c.native(dict(event='ready',kind='start',id='native-start'),2202)
        self.c.tick(2250);self.assertEqual(recent_reviews(self.root),[])
        self.c.observe(context(state=4,clock=1250,time_remaining=550),2250)
        self.c.add_lap(3,99,True)
        self.assertEqual(len(self.c.current['laps']),3)
        self.c.observe(context(state=6,clock=1300,time_remaining=500,official=True),2300)
        self.c.tick(2331)
        records=recent_reviews(self.root);self.assertEqual(len(records),1)
        self.assertFalse(records[0]['left_early'])

if __name__=='__main__':unittest.main()
