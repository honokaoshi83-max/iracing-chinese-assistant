"""Set the native iRacing spotter detection range without editing other options."""
import hashlib,math
from pathlib import Path
from simulator_identity import identity_value
from atomic_io import write_bytes

MAX_DISTANCE=150
SECTION='Assistant.Spotter'
KEY='SlowCar.Distance.Ahead.Threshold'

def ensure_slow_car_limit(home):
    home=Path(home)
    identity=home/'Config/ID'
    if not identity.is_file():return False
    try:owner=identity_value(identity.read_bytes())
    except ValueError:return False
    target=home/'Database/User/IRC/Settings.CSV'
    raw=target.read_bytes() if target.exists() else b''
    encoding='utf-16' if raw.startswith((b'\xff\xfe',b'\xfe\xff')) else 'utf-8-sig' if raw.startswith(b'\xef\xbb\xbf') else 'utf-8'
    original=raw.decode(encoding)
    lines=original.splitlines(keepends=True);updated=[];global_found=False
    for line in lines:
        content=line.rstrip('\r\n');ending=line[len(content):]
        fields=content.split(';')
        if (len(fields)==8 and fields[0].casefold()==owner.casefold()
                and fields[4].casefold()==SECTION.casefold() and fields[5].casefold()==KEY.casefold()):
            global_found |= all(fields[i]=='*' for i in (1,2,3,7))
            try:value=float(fields[6])
            except ValueError:value=float('nan')
            if not math.isfinite(value) or value<=0 or value>MAX_DISTANCE:
                fields[6]=str(MAX_DISTANCE);content=';'.join(fields)
        updated.append(content+ending)
    result=''.join(updated)
    if not global_found:
        if result and not result.endswith(('\r','\n')):result+='\r\n'
        result+=';'.join([owner,'*','*','*',SECTION,KEY,str(MAX_DISTANCE),'*'])+'\r\n'
    if result!=original:
        target.parent.mkdir(parents=True,exist_ok=True)
        if target.exists():
            backup=target.with_name(target.name+'.before-slow-car-'+hashlib.sha256(raw).hexdigest()[:12]+'.bak')
            if not backup.exists():backup.write_bytes(raw)
        write_bytes(target,result.encode(encoding),'.assistant-tmp')
    return True
