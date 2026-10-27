"""Protected presets, owned custom models and pinned Hugging Face sources."""
import hashlib,json,re,shutil,uuid
from pathlib import Path,PurePosixPath
from urllib.parse import urlparse,unquote,quote
import requests
from atomic_io import write_json

DEFAULTS={'clorinde':'克洛琳德','furina':'芙宁娜'}
ROLE_DEFAULTS={'engineer':'clorinde','observer':'furina'}
SUPPORTED=('v1','v2','v2Pro','v2ProPlus')

class HParams:
    """Data-only checkpoint compatibility; no upstream constructor is executed."""
    pass
HParams.__module__='utils'

def atomic(path,value):write_json(path,value,'.library.tmp',ensure_ascii=False,indent=2)

def safe_relative(root,name):
    name=str(name).replace('\\','/');p=PurePosixPath(name)
    if p.is_absolute() or '..' in p.parts or ':' in name:raise ValueError('文件路径无效')
    result=(Path(root)/name).resolve()
    if not result.is_relative_to(Path(root).resolve()):raise ValueError('文件路径超出音色目录')
    return result

class Catalog:
    def __init__(self,root):self.root=Path(root).resolve();self.path=self.root/'voice_library.json'
    def state(self):
        if not self.path.exists():return {'custom':{},'roles':dict(ROLE_DEFAULTS)}
        data=json.loads(self.path.read_text(encoding='utf-8'));data.setdefault('custom',{});data.setdefault('roles',dict(ROLE_DEFAULTS));return data
    def builtin(self):return json.loads((self.root/'voices.json').read_text(encoding='utf-8'))
    def entries(self):
        base=self.builtin();items={key:{'id':key,'name':name,'builtin':True,'profile':base[key]} for key,name in DEFAULTS.items()}
        for key,entry in self.state()['custom'].items():
            if key in base or not re.fullmatch(r'voice-[a-zA-Z0-9-]+',key):raise ValueError('自定义音色标识无效')
            items[key]={'id':key,'name':entry['name'],'builtin':False,'profile':entry['profile'],'source':entry.get('source',{})}
        return items
    def roles(self):
        items=self.entries();roles=self.state()['roles'];return {role:roles.get(role) if roles.get(role) in items else default for role,default in ROLE_DEFAULTS.items()}
    def profiles(self):
        profiles=self.builtin();items=self.entries()
        for key,item in items.items():profiles[key]=item['profile']
        for role,alias in ROLE_DEFAULTS.items():profiles[alias]=items[self.roles()[role]]['profile']
        return profiles
    def register(self,key,profile,name,source=None):
        if key in self.builtin() or not re.fullmatch(r'voice-[a-zA-Z0-9-]+',key):raise ValueError('默认音色不能覆盖')
        if not name.strip() or len(name)>60:raise ValueError('请输入有效音色名称')
        for field in ('gpt','sovits','reference'):
            owned=self.root/'models/custom'/key
            if not safe_relative(self.root,profile[field]).is_relative_to(owned.resolve()):raise ValueError('自定义音色文件必须在专属目录内')
        state=self.state()
        if key in state['custom']:raise ValueError('音色标识已存在')
        state['custom'][key]={'name':name.strip(),'profile':profile,'source':source or {}};atomic(self.path,state)
    def assign(self,role,key):
        if role not in ROLE_DEFAULTS or key not in self.entries():raise ValueError('角色或音色无效')
        state=self.state();state['roles'][role]=key;atomic(self.path,state)
    def assign_roles(self,roles):
        if set(roles)!=set(ROLE_DEFAULTS) or any(key not in self.entries() for key in roles.values()):raise ValueError('角色或音色无效')
        state=self.state();state['roles']=dict(roles);atomic(self.path,state)
    def delete(self,key):
        if key in DEFAULTS:raise ValueError('默认音色不可删除')
        state=self.state()
        if key not in state['custom']:raise ValueError('未找到自定义音色')
        owned=safe_relative(self.root/'models/custom',key)
        del state['custom'][key]
        for role,default in ROLE_DEFAULTS.items():
            if state['roles'].get(role)==key:state['roles'][role]=default
        atomic(self.path,state)
        if owned.exists():shutil.rmtree(owned)

def parse_hf(value):
    value=value.strip()
    if '://' in value:
        url=urlparse(value)
        if url.scheme!='https' or url.hostname!='huggingface.co' or url.username or url.port:raise ValueError('请输入 Hugging Face 模型仓库链接')
        value=unquote(url.path.strip('/'))
    parts=value.strip('/').split('/')
    if len(parts)<2 or not all(re.fullmatch(r'[A-Za-z0-9_.-]+',x) for x in parts[:2]):raise ValueError('请输入 用户名/模型仓库，不能使用 models 列表页')
    repo='/'.join(parts[:2]);revision='main';folder=''
    if len(parts)>2:
        if parts[2]!='tree' or len(parts)<4:raise ValueError('请提供仓库主页或 tree 目录链接')
        revision=parts[3];folder='/'.join(parts[4:])
    if '..' in parts or not re.fullmatch(r'[A-Za-z0-9_.-]+',revision):raise ValueError('仓库路径无效')
    return repo,revision,folder

def repository(value):
    repo,revision,folder=parse_hf(value)
    response=requests.get(f'https://huggingface.co/api/models/{repo}/revision/{quote(revision,safe="")}',params={'blobs':'true'},timeout=30);response.raise_for_status();info=response.json()
    commit=info.get('sha','')
    if not re.fullmatch('[0-9a-f]{40}',commit):raise ValueError('无法取得模型固定版本')
    files=[]
    for item in info.get('siblings',[]):
        name=item.get('rfilename','')
        if folder and not name.startswith(folder.rstrip('/')+'/'):continue
        if PurePosixPath(name).suffix.lower() in ('.ckpt','.pth','.wav','.mp3','.flac','.ogg'):
            safe_relative(Path.cwd(),name)
            files.append({'path':name,'size':item.get('size') or (item.get('lfs') or {}).get('size'),'sha256':(item.get('lfs') or {}).get('sha256')})
    return {'repo':repo,'revision':commit,'files':files,'license':(info.get('cardData') or {}).get('license','未注明')}

def file_hash(path):
    h=hashlib.sha256()
    with path.open('rb') as f:
        while block:=f.read(1024*1024):h.update(block)
    return h.hexdigest()

def fetch_file(info,name,target):
    if parse_hf(info['repo'])[0]!=info['repo'] or not re.fullmatch('[0-9a-f]{40}',info.get('revision','')):raise ValueError('仓库固定版本无效')
    selected=next((x for x in info['files'] if x['path']==name),None)
    if not selected:raise ValueError('选中文件不在仓库清单中')
    if selected.get('size') and selected['size']>2*1024**3:raise ValueError('单个模型文件超过导入大小限制')
    url=f'https://huggingface.co/{info["repo"]}/resolve/{info["revision"]}/{quote(name,safe="/")}'
    partial=target.with_suffix(target.suffix+'.part');done=0;last=0
    with requests.get(url,stream=True,timeout=(15,90)) as r:
        r.raise_for_status()
        with partial.open('wb') as f:
            for chunk in r.iter_content(1024*1024):
                if not chunk:continue
                done+=len(chunk)
                if done>2*1024**3:raise ValueError('下载文件超过大小限制')
                f.write(chunk)
                if done-last>=10*1024*1024:print(f'下载 {Path(name).name}：{done//1024**2} MB',flush=True);last=done
    if selected.get('size') and done!=selected['size']:raise ValueError('下载文件大小不匹配')
    if selected.get('sha256') and file_hash(partial)!=selected['sha256']:raise ValueError('下载文件校验失败')
    partial.replace(target)

def validate_weights(gpt,sovits):
    import torch,io
    def load(path):
        data=path.read_bytes()
        if data[:2]!=b'PK':data=b'PK'+data[2:]
        try:
            with torch.serialization.safe_globals([HParams]):
                obj=torch.load(io.BytesIO(data),map_location='cpu',weights_only=True)
            if isinstance(obj,dict) and isinstance(obj.get('config'),HParams):obj['config']=vars(obj['config'])
            return obj
        except Exception as e:raise ValueError('权重不能安全读取，或不是兼容的导出模型：'+type(e).__name__) from e
    semantic=load(gpt);acoustic=load(sovits)
    for obj in (semantic,acoustic):
        if not isinstance(obj,dict) or not isinstance(obj.get('weight'),dict) or not isinstance(obj.get('config'),dict):raise ValueError('需要 GPT-SoVITS 导出的配套权重文件')
    head=sovits.open('rb').read(2)
    version={b'00':'v1',b'01':'v2',b'05':'v2Pro',b'06':'v2ProPlus'}.get(head)
    if head==b'PK':
        symbols=acoustic['weight'].get('enc_p.text_embedding.weight')
        if symbols is None:raise ValueError('SoVITS 权重缺少文本嵌入')
        version='v1' if symbols.shape[0]==322 else 'v2'
    if version not in SUPPORTED:raise ValueError('当前支持 v1、v2、v2Pro 和 v2ProPlus；该版本尚未适配')
    return version

def import_voice(root,request):
    import subprocess,wave
    catalog=Catalog(root);key='voice-'+uuid.uuid4().hex
    directory=safe_relative(catalog.root/'models/custom',key);directory.mkdir(parents=True)
    try:
        text=request.get('reference_text','').strip()
        if not text or len(text)>400:raise ValueError('参考音频对应文字不能为空，最长 400 字')
        paths={'gpt':directory/'gpt.ckpt','sovits':directory/'sovits.pth'}
        audio_name=request['files']['reference'];audio=directory/('source'+Path(audio_name).suffix.lower())
        paths['reference']=audio
        if Path(request['files']['gpt']).suffix.lower()!='.ckpt' or Path(request['files']['sovits']).suffix.lower()!='.pth':raise ValueError('请选择 .ckpt 和 .pth 配套权重')
        if audio.suffix not in ('.wav','.mp3','.flac','.ogg'):raise ValueError('参考音频格式不支持')
        source=request.get('source')
        for field,path in paths.items():
            print('准备 '+field,flush=True)
            if source:fetch_file(source,request['files'][field],path)
            else:
                original=Path(request['files'][field]).resolve()
                if not original.is_file() or original.stat().st_size>2*1024**3:raise ValueError('本地文件不存在或超过大小限制')
                shutil.copy2(original,path)
        print('检查模型结构与版本…',flush=True);version=validate_weights(paths['gpt'],paths['sovits'])
        reference=directory/'reference.wav'
        try:
            from runtime_settings import ffmpeg_path
            ffmpeg=ffmpeg_path()
        except ImportError:
            from bridge import FFMPEG
            ffmpeg=FFMPEG
        subprocess.run([str(ffmpeg),'-y','-hide_banner','-loglevel','error','-i',str(audio),'-ar','32000','-ac','1',str(reference)],check=True,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        with wave.open(str(reference)) as w:seconds=w.getnframes()/w.getframerate()
        if not 3<=seconds<=10:raise ValueError('参考录音应为 3~10 秒，请更换录音')
        profile={'version':version,'gpt':paths['gpt'].relative_to(catalog.root).as_posix(),'sovits':paths['sovits'].relative_to(catalog.root).as_posix(),'reference':reference.relative_to(catalog.root).as_posix(),'reference_text':text}
        metadata={'kind':'huggingface' if source else 'local','repo':source['repo'] if source else '', 'revision':source['revision'] if source else '', 'license':source.get('license','未注明') if source else '用户本地导入','sha256':{field:file_hash(path) for field,path in paths.items()}}
        catalog.register(key,profile,request['name'],metadata)
        print('导入成功：'+request['name']+'。尚未替换当前角色。',flush=True);return key
    except Exception:
        # Only remove this newly-created, resolved, application-owned directory.
        if directory.resolve().is_relative_to((catalog.root/'models/custom').resolve()):shutil.rmtree(directory)
        raise

if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser();p.add_argument('action',choices=['import','delete','assign']);p.add_argument('--request');p.add_argument('--voice');p.add_argument('--role');args=p.parse_args();root=Path(__file__).resolve().parent
    try:
        if args.action=='import':import_voice(root,json.loads(Path(args.request).read_text(encoding='utf-8')))
        elif args.action=='delete':Catalog(root).delete(args.voice);print('已删除自定义音色；默认预设保留。')
        else:Catalog(root).assign(args.role,args.voice);print('角色音色已保存，重启播报后生效。')
    except Exception as e:print('操作失败：'+str(e),flush=True);raise SystemExit(1)
