"""Merge speech-only assistant settings, preserving unrelated personal setup."""
import json,re,shutil,sys
from datetime import datetime
from runtime_settings import documents,settings,data_path
from remove_dialogue import transform
from atomic_io import write_bytes

ROLES=('Race Engineer','Race Spotter','Race Strategist')

def portable_configure(text,port):
    text=transform(text);lines=text.splitlines(); section='';seen=set();out=[]
    def entry(role,value='true||'):
        fields=value.split('|',2)
        if len(fields)!=3:raise ValueError('Unexpected assistant plugin format')
        args=[a.strip() for a in fields[2].split(';') if a.strip()]
        args=[a for a in args if a.split(':',1)[0].strip().lower() not in ('listener','language','speaker','speakervocalics','synthesizer','raceassistant')]
        voice='clorinde' if role=='Race Engineer' else 'furina'
        if not any(a.split(':',1)[0].strip().lower() in ('name','raceassistantname') for a in args):
            args.append('name: '+('克洛琳德' if role=='Race Engineer' else '芙宁娜'))
        args.append('raceAssistant: On')
        args+=['language: zh','listener: Off','speaker: gpt-sovits/'+voice,'speakerVocalics: 100,0,0',f'synthesizer: OpenAI|http://127.0.0.1:{port}|local']
        return role+'=true|'+fields[1]+'|'+'; '.join(args)
    for line in lines:
        if line.startswith('['):
            if section=='Plugins':out.extend(entry(r) for r in ROLES if r not in seen)
            section=line.strip('[]')
        if section=='Plugins' and '=' in line and line.split('=',1)[0] in ROLES:
            key,value=line.split('=',1);line=entry(key,value);seen.add(key)
        out.append(line)
    if section=='Plugins':out.extend(entry(r) for r in ROLES if r not in seen)
    if '[Plugins]' not in out:out+=['[Plugins]',*(entry(r) for r in ROLES)]
    return '\n'.join(out)+'\n'

def main():
    target=documents()/'Simulator Controller/Config/Simulator Configuration.ini'
    if not target.is_file():raise SystemExit('请先用 Simulator Setup 保存基础配置，再点击接入。')
    stamp=datetime.now().strftime('%Y%m%d-%H%M%S')
    raw=target.read_bytes();enc='utf-16' if raw[:2] in (b'\xff\xfe',b'\xfe\xff') else 'utf-8-sig'
    after=portable_configure(raw.decode(enc),settings()['port']).encode(enc)
    if after!=raw:
        shutil.copy2(target,target.with_name(target.name+'.before-chinese-assistant-'+stamp+'.bak'))
        write_bytes(target,after,'.portable.tmp')
    folder=documents()/'Simulator Controller/Grammars';folder.mkdir(parents=True,exist_ok=True)
    for source in data_path('grammars').glob('*.zh'):
        dest=folder/source.name
        if dest.exists():shutil.copy2(dest,dest.with_name(dest.name+'.before-chinese-assistant-'+stamp+'.bak'))
        # Windows INI readers require an explicit Unicode encoding and CRLF.
        content=source.read_text(encoding='utf-8-sig')
        dest.write_bytes(('\r\n'.join(content.splitlines())+'\r\n').encode('utf-16'))
    print('已备份并接入三个助手。请重新启动 Simulator Controller。')

if __name__=='__main__':main()
