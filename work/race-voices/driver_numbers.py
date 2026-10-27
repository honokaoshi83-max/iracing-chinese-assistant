"""Map current-session driver references to actual entry numbers, never ranks."""
import json,re,time
from pathlib import Path
from atomic_io import write_json

FILE=Path(__file__).resolve().parent/'driver_numbers.json'

def snapshot(info,positions=None):
    drivers=[r for r in info.get('Drivers',[]) if not r.get('CarIsPaceCar')]
    aliases={};conflicts=set()
    for row in drivers:
        number=str(row.get('CarNumber','')).strip()
        name=str(row.get('UserName','')).strip()
        if len(name)<2:continue
        if not number.isdigit():number=None
        for alias in {name,re.sub(r'\s+','',name),re.sub(r'\s+',' ',name)}:
            key=alias.casefold()
            if key in aliases and aliases[key]!=number:conflicts.add(key)
            aliases[key]=number
    for key in conflicts:aliases[key]=None
    result={'until':time.time()+15,'aliases':aliases,'ahead':None,'behind':None}
    own=next((r for r in drivers if r.get('CarIdx')==info.get('DriverCarIdx')),None)
    if own and positions is not None:
        def rank(row):
            index=row.get('CarIdx',-1)
            return positions[index] if isinstance(index,int) and 0<=index<len(positions) else 0
        my_rank=rank(own)
        if my_rank>0:
            for row in drivers:
                if row.get('CarClassID')!=own.get('CarClassID'):continue
                number=str(row.get('CarNumber','')).strip()
                if number.isdigit():
                    if rank(row)==my_rank-1:result['ahead']=number
                    elif rank(row)==my_rank+1:result['behind']=number
    return result

def write_snapshot(info,positions=None):
    write_json(FILE,snapshot(info,positions),ensure_ascii=False)

def clear_snapshot():FILE.unlink(missing_ok=True)

def normalize_references(text,data=None):
    if text.startswith('我是'):return text
    if data is None:
        try:data=json.loads(FILE.read_text(encoding='utf-8'))
        except (OSError,ValueError):return text
    if data.get('until',0)<time.time():return text
    aliases=data.get('aliases',{})
    if aliases:
        pattern='|'.join((r'(?<![A-Za-z0-9])'+re.escape(name)+r'(?![A-Za-z0-9])') if re.search('[A-Za-z]',name)
                         else re.escape(name) for name in sorted(aliases,key=len,reverse=True))
        text=re.sub(pattern,lambda m:(aliases[m.group().casefold()]+'号车') if aliases[m.group().casefold()] else '该赛车',text,flags=re.I)
    for word,key in [('前一名','ahead'),('后一名','behind')]:
        if data.get(key):text=re.sub(word+r'(?!的?[0-9零〇一二两三四五六七八九十百千万]+号车)',word+'的'+data[key]+'号车',text)
    return text
