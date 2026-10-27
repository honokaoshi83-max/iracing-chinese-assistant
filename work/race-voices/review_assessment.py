"""Offline, evidence-based session comments. No network or inference runtime."""
import hashlib,statistics
from review_consistency import measure,description,pace_trend,timed

def assess_session(row):
    from telemetry_announcer import chinese_number,lap_phrase
    from speech_reading import normalize_reading
    number=lambda value:chinese_number(int(value))
    amount=lambda value:'两' if value==2 else number(value)
    rows=[r for r in row.get('laps',[]) if isinstance(r,dict)]
    times=[r['seconds'] for r in rows if timed(r.get('seconds'))]
    valid=[r['seconds'] for r in rows if r.get('valid') and timed(r.get('seconds'))]
    invalid=sum(not r.get('valid',False) for r in rows)
    count=len(rows);ratio=invalid/count if count else None
    average=statistics.mean(times) if times else None
    sd=statistics.pstdev(times) if len(times)>=2 else None
    valid_sd=statistics.pstdev(valid) if len(valid)>=2 else None
    consistency=measure(rows)
    stability=consistency['stability']
    longest=streak=0;previous=None
    for index,entry in enumerate(rows):
        lap=entry.get('lap',index+1)
        if previous is not None and lap!=previous+1:streak=0
        streak=streak+1 if entry.get('valid') and timed(entry.get('seconds')) else 0
        longest=max(longest,streak);previous=lap
    trend,delta=pace_trend(valid)
    lines=[];race=row.get('mode')=='race'
    if race:
        pos=row.get('position');grid=row.get('grid');cls=row.get('class_position')
        if row.get('disqualified'):lines.append('本场比赛被取消资格。以下只评价已记录的圈速和有效圈表现。')
        else:
            official=row.get('position_source')=='SDK正式结果' or (row.get('official') and not row.get('position_source'))
            ranking=('本场正式总排名第' if official else '本场最后记录的总排名第')+number(pos)+'名' if type(pos) is int and pos>0 else '本场没有可用的总排名数据'
            if type(cls) is int and cls>0:ranking+='，组别第'+number(cls)+'名'
            if type(pos) is int and pos>0 and type(grid) is int and grid>0:
                change=grid-pos
                ranking+=('，比发车位置'+('提升'+number(change)+'位' if change>0 else '下降'+number(-change)+'位' if change<0 else '保持不变'))
            lines.append(ranking+'。')
    prefix='本次正赛' if race else '本次计时赛'
    lines.append(prefix+'记录'+amount(count)+'个完成圈，其中'+amount(len(valid))+'个有效计时圈，'+amount(invalid)+'个犯规无效圈。')
    if valid:lines.append('最快有效圈'+lap_phrase(min(valid))[2:]+'有效平均圈速'+lap_phrase(statistics.mean(valid))[2:])
    else:lines.append('本次没有有效圈速，暂时无法评价有效圈的速度。')
    if trend in ('faster','slower'):
        lines.append(normalize_reading(f"后半段有效圈平均比前半段{'快' if trend=='faster' else '慢'}{abs(delta):.1f}秒。"))
    elif trend=='steady':lines.append('前后半段的平均有效圈速接近。')
    else:lines.append('有效圈数据不足，暂不比较前后段速度变化。')
    lines.extend(description(consistency,inline_rules=True))
    slow=consistency['slow_outliers']
    if slow:
        qualifier='个别圈明显变慢' if slow/len(valid)<=.2 else '部分圈明显变慢'
        lines.append('有效圈中有'+amount(slow)+'圈明显慢于本次典型用时，'+qualifier+'；这些圈保留在统计中。')
    if longest>=2:lines.append('记录中最长连续完成'+amount(longest)+'个有效圈。')
    if invalid:
        percentage=f'{ratio*100:.1f}'.rstrip('0').rstrip('.')
        lines.append(normalize_reading(f'犯规无效圈占已记录完成圈的{percentage}%。有用时的无效圈计入全部计时圈波动率，犯规比例单独评价。'))
        missing=sum(not r.get('valid') and not timed(r.get('seconds')) for r in rows)
        if missing:lines.append('其中'+number(missing)+'圈没有可用用时，仅计入犯规比例。')
    elif count:lines.append('已记录的完成圈均有效。')
    seed=int(hashlib.sha256(str(row.get('id','')).encode()).hexdigest()[:8],16)%2
    if invalid:
        choices=('下一场先减少犯规，把连续有效圈跑稳，再逐步提高速度。','下一场的重点是先减少犯规，保持连续有效圈，再争取更快成绩。')
    elif consistency['valid_stability']=='insufficient':
        choices=('下一场先积累连续有效圈，再比较速度和稳定性。','下一场先完成更多连续有效圈，建立可比较的圈速记录。')
    elif consistency['valid_stability']!='good':
        choices=('下一场先缩小各圈差距，争取连续复现自己的正常圈速。','下一场以稳定连续圈为重点，减少较快圈与较慢圈之间的差距。')
    elif trend=='slower':
        choices=('下一场重点保持后半段节奏，争取把前段的圈速延续下来。','下一场关注后半段的连续表现，减少后程用时增加。')
    else:
        choices=('下一场保持当前的连续节奏，在有效圈基础上逐步争取更快成绩。','下一场延续稳定的有效圈表现，再逐步提高整体速度。')
    lines.append(choices[seed])
    if race and not row.get('race_started_at'):lines.append('以上评价基于已记录时段，不代表整场比赛的全部表现。')
    if row.get('coverage_gaps'):lines.append('记录中包含重连缺口，缺失期间的圈速未参与评价。')
    return dict(observed_laps=count,timed_laps=len(times),valid_laps=len(valid),invalid_laps=invalid,
                best_valid=min(valid) if valid else None,valid_average=statistics.mean(valid) if valid else None,
                standard_deviation=sd,valid_standard_deviation=valid_sd,stability=stability,
                longest_valid_streak=longest,trend=trend,trend_delta=delta,summary=lines,**{
                    key:value for key,value in consistency.items() if key not in ('standard_deviation','valid_standard_deviation','stability')})
