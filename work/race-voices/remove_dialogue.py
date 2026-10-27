"""Remove cloud dialogue configuration while preserving local speech routing."""
import re
from pathlib import Path
from datetime import datetime
import shutil
from atomic_io import write_bytes

def transform(text):
    section=''; result=[]; newline='\r\n' if '\r\n' in text else '\n'
    for line in text.splitlines(keepends=True):
        header=re.match(r'^\[([^\]]+)\]',line)
        if header: section=header[1]
        if section in ('Conversation Booster','Agent Booster','Driving Coach Service','Driving Coach Conversations'):
            if header: result.append(line)
            continue
        if section=='Voice Control' and '=' in line:
            key=line.split('=',1)[0]
            if key=='Listener': line='Listener=false'+newline
            elif key=='PushToTalk': line='PushToTalk='+newline
            elif key=='PushToTalkMode': line='PushToTalkMode=Press'+newline
        result.append(line)
    return ''.join(result)

def main():
    target=Path.home()/'Documents/Simulator Controller/Config/Simulator Configuration.ini'
    raw=target.read_bytes(); enc='utf-16' if raw[:2] in (b'\xff\xfe',b'\xfe\xff') else 'utf-8-sig'
    before=raw.decode(enc); after=transform(before)
    if before!=after:
        backup=target.with_name(target.name+'.before-status-button-'+datetime.now().strftime('%Y%m%d-%H%M%S')+'.bak')
        shutil.copy2(target,backup)
        write_bytes(target,after.encode(enc),'.status.tmp')
    print('Cloud dialogue sections removed; microphone/PTT disabled; local TTS preserved.')

if __name__=='__main__': main()
