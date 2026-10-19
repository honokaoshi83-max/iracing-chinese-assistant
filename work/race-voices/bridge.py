"""Loopback speech adapter for Simulator Controller and three GPT-SoVITS voices."""
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time

ROOT = Path(__file__).resolve().parent
os.environ.setdefault('NLTK_DATA',str(ROOT/'nltk_data'))
VOICES = ('furina', 'clorinde', 'ganyu')
from runtime_settings import ffmpeg_path, settings, instance_id
FFMPEG = ffmpeg_path()

def normalize_audio(data, fmt):
    """Restore speech loudness without radio filtering, noise or distortion."""
    codec = ['-codec:a', 'libmp3lame', '-b:a', '128k'] if fmt == 'mp3' else ['-codec:a', 'pcm_s16le']
    return subprocess.run([str(FFMPEG), '-hide_banner', '-loglevel', 'error',
        '-i', 'pipe:0', '-af', 'loudnorm=I=-16:TP=-1.5:LRA=7', '-ar', '32000',
        *codec, '-f', fmt, 'pipe:1'], input=data, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, check=True,
        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0)).stdout

def adjust_delivery(data, rate):
    """Small tempo adjustment without shifting pitch or adding radio effects."""
    fmt='wav' if data.startswith(b'RIFF') else 'mp3'
    codec=['-codec:a','pcm_s16le'] if fmt=='wav' else ['-codec:a','libmp3lame','-b:a','128k']
    return subprocess.run([str(FFMPEG),'-hide_banner','-loglevel','error','-i','pipe:0',
        '-af',f'atempo={rate},loudnorm=I=-16:TP=-1.5:LRA=7','-ar','32000',*codec,'-f',fmt,'pipe:1'],
        input=data,stdout=subprocess.PIPE,stderr=subprocess.PIPE,check=True,
        creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0)).stdout

class VoiceService:
    def __init__(self, cache, render, fingerprint, active_voice=None, postprocess=None, style_processor=None):
        self.cache, self.render, self.fingerprint = Path(cache), render, fingerprint
        self.cache.mkdir(parents=True, exist_ok=True)
        from button_priority import PriorityLock
        self.lock = PriorityLock()
        self.voices = tuple(active_voice) if isinstance(active_voice, (tuple, list)) else (active_voice,) if active_voice else VOICES
        self.postprocess = postprocess
        from emotion_profiles import load_config
        self.emotions = load_config()
        self.style_processor = style_processor

    def speech(self, voice, text, speed=1.0, fmt='mp3', emotion='auto',manual=False,safety=False):
        if voice not in self.voices:
            raise ValueError('Unknown voice')
        if not isinstance(text, str) or not text.strip() or len(text) > 600:
            raise ValueError('Text must contain 1–600 characters')
        if not isinstance(speed, (float, int)) or not .5 <= speed <= 2:
            raise ValueError('Speed must be between 0.5 and 2')
        # Pydantic defaults can be int while explicit JSON rates become float.
        # A rate of 1 and 1.0 must not generate separate audio/cache entries.
        speed = float(speed)
        if fmt not in ('wav', 'mp3'):
            raise ValueError('Only wav and mp3 are supported')
        from telemetry_announcer import clean_phrase
        from speech_reading import normalize_reading, pronunciation_signature
        from name_reading import normalize_names
        legacy_engineer='Jona' in text and '我是' in text
        text = normalize_reading(normalize_names(clean_phrase(text.strip())))
        if legacy_engineer:text=text.replace('芙宁娜','克洛琳德')
        from voice_routing import route_voice
        voice=route_voice(text,voice)
        if voice not in self.voices:raise ValueError('Required role voice is not enabled')
        reading_fingerprint=self.fingerprint+pronunciation_signature(text)
        if voice == 'clorinde':
            text = text.replace('芙宁娜', '克洛琳德')
            if text == '赛车状态和油量监控已就绪。':
                text = '我是克洛琳德，负责赛车状态、油量和维修提醒。'
        from emotion_profiles import classify, resolve
        style=classify(text) if emotion=='auto' else emotion
        base=getattr(self.render,'profiles',{}).get(voice,{})
        delivery=resolve(voice,style,base,self.emotions)
        if style != 'calm' and (self.style_processor or delivery['reference_ready']):
            key=hashlib.sha256(json.dumps([reading_fingerprint,voice,text,speed,fmt,delivery['identity'],'delivery-v1'],ensure_ascii=False).encode()).hexdigest()
            output=self.cache/(key+'-emotion.'+fmt)
            if output.exists():
                return output.read_bytes()
            # Reuse original speech when no suitable emotional reference exists.
            raw=None if delivery['reference_ready'] else self.speech(voice,text,speed,fmt,emotion='calm',manual=manual,safety=safety)
            with self.lock.hold(manual,safety):
                if output.exists():
                    return output.read_bytes()
                if delivery['reference_ready']:
                    data=self.render.render_profile(voice,text,speed*delivery['speed'],fmt,delivery['profile'])
                    if self.postprocess:
                        data=self.postprocess(data,fmt)
                else:
                    data=self.style_processor(raw,delivery['speed'])
                if not data:
                    raise RuntimeError('Empty styled audio')
                temp=output.with_suffix('.part'); temp.write_bytes(data); temp.replace(output)
                return data
        key = hashlib.sha256(json.dumps([reading_fingerprint, voice, text, speed, fmt], ensure_ascii=False).encode()).hexdigest()
        file = self.cache / (key + '.' + fmt)
        output = self.cache / (key + '-loud-v1.' + fmt) if self.postprocess else file
        if output.exists():
            return output.read_bytes()
        # Shared model switching and inference must be serialized. Cache hits
        # bypass this lock so an urgent cached call is not held by inference.
        with self.lock.hold(manual,safety):
            if output.exists():
                return output.read_bytes()
            data = file.read_bytes() if file.exists() else self.render(voice, text, speed, fmt)
            if not data:
                raise RuntimeError('Empty audio from speech engine')
            if self.postprocess:
                data = self.postprocess(data, fmt)
            temp = output.with_suffix('.part')
            temp.write_bytes(data)
            temp.replace(output)
            return data

class SoVITSEngine:
    def __init__(self):
        self.model = None
        self.voice = None
        from voice_library import Catalog
        self.profiles = Catalog(ROOT).profiles()

    def __call__(self, voice, text, speed, fmt):
        return self.render_profile(voice,text,speed,fmt,self.profiles[voice])

    def render_profile(self, voice, text, speed, fmt, profile):
        import numpy as np
        import soundfile as sf
        import torch
        repo = ROOT/'GPT-SoVITS'
        os.chdir(repo)
        sys.path[:0] = [str(repo), str(repo/'GPT_SoVITS')]
        # Pure Python jieba avoids a C compiler requirement on Windows.
        import jieba, jieba.posseg
        sys.modules.setdefault('jieba_fast', jieba)
        sys.modules.setdefault('jieba_fast.posseg', jieba.posseg)
        torch.set_num_threads(4)
        from GPT_SoVITS.TTS_infer_pack.TTS import TTS, TTS_Config
        from speech_reading import install_overrides
        install_overrides()
        if self.model is None:
            cfg = TTS_Config({'custom': {
                'device': 'cpu', 'is_half': False, 'version': profile['version'],
                't2s_weights_path': str(ROOT/profile['gpt']),
                'vits_weights_path': str(ROOT/profile['sovits']),
                'bert_base_path': str(repo/'GPT_SoVITS/pretrained_models/chinese-roberta-wwm-ext-large'),
                'cnhuhbert_base_path': str(repo/'GPT_SoVITS/pretrained_models/chinese-hubert-base'),
            }})
            cfg.configs_path = str(ROOT/'active_inference.yaml')
            self.model = TTS(cfg)
            self.voice = voice
        elif self.voice != voice:
            # Update version before computing semantic mute-token embeddings.
            self.model.init_vits_weights(str(ROOT/profile['sovits']))
            self.model.init_t2s_weights(str(ROOT/profile['gpt']))
            self.model.prompt_cache = {key: ([] if isinstance(value, list) else None) for key,value in self.model.prompt_cache.items()}
            self.voice = voice
        inputs = {
            'text': text, 'text_lang': 'all_zh', 'prompt_lang': 'all_zh',
            'ref_audio_path': str(ROOT/profile['reference']), 'prompt_text': profile['reference_text'],
            'text_split_method': 'cut5', 'batch_size': 1, 'speed_factor': speed,
            'seed': 42, 'streaming_mode': False, 'parallel_infer': True,
            'top_k': 15, 'top_p': 1, 'temperature': 1, 'repetition_penalty': 1.35,
        }
        started = time.perf_counter()
        chunks = list(self.model.run(inputs))
        if not chunks:
            raise RuntimeError('No audio was synthesized')
        sample_rate = chunks[0][0]
        audio = np.concatenate([chunk[1] for chunk in chunks])
        wav = io.BytesIO()
        sf.write(wav, audio, sample_rate, format='WAV', subtype='PCM_16')
        data = wav.getvalue()
        if fmt == 'mp3':
            data = subprocess.run([str(FFMPEG), '-hide_banner', '-loglevel', 'error', '-f', 'wav', '-i', 'pipe:0', '-f', 'mp3', '-codec:a', 'libmp3lame', '-b:a', '128k', 'pipe:1'], input=data, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0)).stdout
        import psutil
        metric = {'voice':voice, 'text':text, 'seconds':round(time.perf_counter()-started,3), 'audio_seconds':round(len(audio)/sample_rate,3), 'rss_mb':round(psutil.Process().memory_info().rss/1024**2,1)}
        with (ROOT/'metrics.jsonl').open('a', encoding='utf-8') as f:
            f.write(json.dumps(metric, ensure_ascii=False)+'\n')
        return data

def create_app(service):
    from fastapi import FastAPI, HTTPException, Response, Request
    from pydantic import BaseModel, Field
    app = FastAPI(title='Local Race Voices')
    @app.middleware('http')
    async def finish_component_request(http_request,call_next):
        try:return await call_next(http_request)
        finally:
            token=getattr(http_request.state,'component_priority_token',None)
            if token:
                from opening_sequence import complete_component_request
                complete_component_request(token)
    class SpeechRequest(BaseModel):
        model: str = 'gpt-sovits'
        voice: str
        input: str = Field(min_length=1, max_length=600)
        response_format: str = 'mp3'
        speed: float = 1
        emotion: str = 'auto'
        opening: bool = False
        flag: bool = False
        review: bool = False
        manual: bool = False
        local: bool = False
    @app.get('/health')
    def health():
        return {'status':'ready', 'voices':list(service.voices), 'device':'cpu', 'instance':instance_id(), 'engine':settings()['engine'],
                'audio_processing':'clean-loudness-v1' if service.postprocess else 'raw',
                'delivery_profiles':['calm','warning','encouraging'],
                'delivery_mode':'reference_or_tempo_fallback',
                'emotion_reference_voices':[v for v,styles in service.emotions.get('voices',{}).items() if styles]}
    @app.get('/voices')
    def voices():
        return {'voices':['gpt-sovits/'+voice for voice in service.voices]}
    @app.post('/v1/audio/speech')
    def speech(request: SpeechRequest,http_request: Request):
        if request.model != 'gpt-sovits':
            raise HTTPException(400, 'Use model gpt-sovits')
        from opening_sequence import native_greeting,silent_audio,opening_active,split_opening_state
        from flag_monitor import native_flag,managed
        from review_phrases import classify,record_event
        review_phrase=classify(request.input) if not request.review and not request.opening else None
        save_prompt=review_phrase is not None and review_phrase['kind']=='save'
        suppressed=not request.opening and (native_greeting(request.input) or
                    (opening_active() and split_opening_state(request.input)) or
                    (not request.flag and managed() and native_flag(request.input)))
        suppressed=suppressed or review_phrase is not None
        if not request.opening and not request.manual and not request.local:
            from opening_sequence import record_component_priority
            http_request.state.component_priority_token=record_component_priority(request.input,suppressed,pending=not suppressed)
        with (ROOT/'speech_requests.jsonl').open('a',encoding='utf-8') as record:
            from voice_routing import route_voice
            record.write(json.dumps({'time':time.strftime('%Y-%m-%d %H:%M:%S'),
                'voice':request.voice,'text':request.input,'opening':request.opening,'flag':request.flag,'manual':request.manual,
                'effective_voice':route_voice(request.input,request.voice),
                'action':'suppressed_review' if review_phrase is not None else 'suppressed_opening_duplicate' if suppressed else 'speech_request'},ensure_ascii=False)+'\n')
        if suppressed:
            if request.voice not in service.voices:
                raise HTTPException(400,'Unknown voice')
            if request.response_format not in ('wav','mp3'):
                raise HTTPException(400,'Only wav and mp3 are supported')
            return Response(silent_audio(request.response_format),media_type='audio/wav' if request.response_format=='wav' else 'audio/mpeg')
        review_id=None
        def journal(event):
            nonlocal review_id
            if review_phrase is None:return
            try:
                from telemetry_announcer import clean_phrase
                row=dict(review_phrase,text=clean_phrase(request.input))
                review_id=record_event(ROOT,row,event,review_id)
            except (OSError,ValueError):pass # Disk errors must not interrupt existing speech.
        journal('request')
        from button_priority import spotter_safety
        safety=not request.manual and not request.opening and spotter_safety(request.input)
        def wait_for_manual():
            if not request.manual and not safety:
                from button_priority import manual_active
                while manual_active(ROOT):time.sleep(.03)
        wait_for_manual()
        try:
            if safety:data=service.speech(request.voice,request.input,request.speed,request.response_format,request.emotion,safety=True)
            elif request.manual:data=service.speech(request.voice,request.input,request.speed,request.response_format,request.emotion,manual=True)
            else:data=service.speech(request.voice,request.input,request.speed,request.response_format,request.emotion)
        except ValueError as e:
            journal('failed')
            raise HTTPException(400, str(e)) from e
        except Exception:
            journal('failed');raise
        journal('ready')
        wait_for_manual()
        from output_volume import apply_volume
        data=apply_volume(data,request.response_format,ROOT,FFMPEG)
        if request.response_format=='wav':
            from wav_playback import finalize_wav
            data=finalize_wav(data)
        from emotion_profiles import classify as delivery_class
        if not (request.local or request.manual or request.opening or request.review or safety or delivery_class(request.input)=='warning'):
            from local_playback import active
            from button_priority import manual_active
            while active(ROOT) or manual_active(ROOT):time.sleep(.03)
        return Response(data, media_type='audio/mpeg' if request.response_format=='mp3' else 'audio/wav')
    return app

if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=settings()['port'])
    parser.add_argument('--engine', choices=['system','gpt-sovits'], default=settings()['engine'])
    parser.add_argument('--sample', choices=VOICES)
    parser.add_argument('--active-voice', choices=VOICES, default='furina')
    parser.add_argument('--active-voices', nargs='+', choices=VOICES)
    parser.add_argument('--text', default='工程师已上线，祝你比赛顺利。')
    parser.add_argument('--format', default='wav', choices=['wav','mp3'])
    args = parser.parse_args()
    os.environ['HF_HUB_OFFLINE'] = '1'
    os.environ['TRANSFORMERS_OFFLINE'] = '1'
    os.environ['PYTHONUTF8'] = '1'
    if args.engine=='system':
        from system_engine import SystemEngine
        engine=SystemEngine()
        fingerprint='windows-system-v1-'+engine.fingerprint
    else:
        from voice_library import Catalog
        engine=SoVITSEngine()
        extra=(ROOT/'voice_library.json').read_bytes() if (ROOT/'voice_library.json').exists() else b''
        fingerprint=hashlib.sha256((ROOT/'asset_manifest.json').read_bytes()+(ROOT/'voices.json').read_bytes()+b'cpu4-seed42-v1'+extra).hexdigest()
    active=args.sample or args.active_voices or args.active_voice
    if args.engine=='gpt-sovits' and not args.sample:
        active=list(active) if isinstance(active,(list,tuple)) else [active]
        active += [key for key,item in Catalog(ROOT).entries().items() if not item['builtin']]
    service = VoiceService(ROOT/'cache', engine, fingerprint, active_voice=active, postprocess=normalize_audio, style_processor=adjust_delivery)
    if args.sample:
        target = ROOT/'samples'/(args.sample+'.'+args.format)
        target.parent.mkdir(exist_ok=True)
        target.write_bytes(service.speech(args.sample, args.text, 1, args.format))
        print(target)
    else:
        import uvicorn
        uvicorn.run(create_app(service), host='127.0.0.1', port=args.port)
