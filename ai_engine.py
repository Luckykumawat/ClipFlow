"""Local inference, semantic selection, timed captions and visual speaker reframing."""
import json
import math
import os
from pathlib import Path
import subprocess
import tempfile
import sys
from functools import lru_cache

ROOT = Path(__file__).resolve().parent
MODELS = Path(os.environ.get('CLIPFLOW_MODELS_DIR', ROOT / 'models')).resolve()
MODELS.mkdir(parents=True, exist_ok=True)
os.environ.setdefault('HF_HOME', str(MODELS / 'huggingface'))
os.environ.setdefault('HF_HUB_DISABLE_XET', '1')
os.environ.setdefault('TOKENIZERS_PARALLELISM', 'false')
CLOUD_LITE=os.environ.get('CLOUD_LITE')=='1'
EMBED_MODEL='minishlab/potion-base-8M' if CLOUD_LITE else 'sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2'
ASR = {}
EMBEDDER = None


def transcribe(source, folder, model='base', language='', progress=lambda *_: None, task='transcribe'):
    # Keep PyAV and OpenCV native libraries in separate processes on macOS.
    # Releasing Whisper before rendering also reduces peak memory.
    with tempfile.TemporaryFile() as errors:
        process=subprocess.Popen([sys.executable,str(Path(__file__).resolve()),'--transcribe',str(Path(source).resolve()),str(Path(folder).resolve()),model,language,task],stdout=subprocess.PIPE,stderr=errors,text=True)
        try:
            for line in process.stdout:
                try:event=json.loads(line)
                except json.JSONDecodeError:continue
                if 'status' in event:progress(event['status'],event['detail'])
            code=process.wait()
            if code:
                errors.seek(0);message=errors.read().decode(errors='replace')
                raise RuntimeError('Transcription failed: '+message[-1600:])
        except BaseException:
            if process.poll() is None:process.kill();process.wait()
            raise
    return json.loads((folder/('highlight-transcript.json' if task=='translate' else 'transcript.json')).read_text())


def _transcribe_local(source, folder, model='base', language='', progress=lambda *_: None, task='transcribe'):
    from faster_whisper import WhisperModel
    progress('transcribing', 'Loading local Whisper ' + model + ' model…')
    if model not in ASR:
        ASR.clear()
        ASR[model] = WhisperModel(model, device='cpu', compute_type='int8', cpu_threads=1 if CLOUD_LITE else min(6, os.cpu_count() or 2), download_root=str(MODELS / 'whisper'))
    import numpy as np
    duration=float(subprocess.check_output(['ffprobe','-v','error','-show_entries','format=duration','-of','default=noprint_wrappers=1:nokey=1',str(source)]))
    output=[]; detected_language=language or None
    # Decode bounded audio windows rather than retaining a long recording in RAM.
    for offset in range(0,math.ceil(duration),60):
        decoded=subprocess.run(['ffmpeg','-nostdin','-v','error','-ss',str(offset),'-i',str(source),'-t','60','-vn','-ac','1','-ar','16000','-f','f32le','pipe:1'],stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        if decoded.returncode:raise RuntimeError('Could not read speech audio: '+decoded.stderr.decode(errors='replace')[-800:])
        audio=np.frombuffer(decoded.stdout,dtype=np.float32)
        if len(audio)==0:continue
        segments,info=ASR[model].transcribe(audio,language=detected_language,beam_size=3,task=task,word_timestamps=True,vad_filter=True,condition_on_previous_text=False)
        chunk=[]
        for segment in segments:
            if segment.no_speech_prob>.85:continue
            chunk.append({'start':offset+segment.start,'end':min(duration,offset+segment.end),'text':segment.text.strip(),'words':
                [{'start':offset+w.start,'end':min(duration,offset+w.end),'text':w.word.strip()} for w in (segment.words or [])]})
        if chunk and not detected_language:detected_language=info.language
        output.extend(chunk)
        progress('transcribing',f'Transcribed {min(duration,offset+60):.0f} / {duration:.0f} seconds')
    result = {'language':detected_language or language or 'en','segments':output}
    (folder / ('highlight-transcript.json' if task=='translate' else 'transcript.json')).write_text(json.dumps(result, ensure_ascii=False, indent=2))
    return result


def candidates(segments, start, end, seconds):
    """Build overlapping speech windows on word boundaries, preferring sentence ends."""
    words=[]
    for segment in segments:
        for word in (segment.get('words') or [{'start':segment['start'],'end':segment['end'],'text':segment['text']}]):
            if word['start']>=start and word['end']<=end and word['text'].strip():words.append(word)
    if not words:return []
    output=[];last=-1000;step=min(8,max(1,seconds*.4))
    for i,word in enumerate(words):
        sentence_start=i==0 or words[i-1]['text'].rstrip().endswith(('.', '?', '!', '।')) or word['start']-words[i-1]['end']>.65
        if word['start']-last<step and not sentence_start:continue
        a=word['start'];limit=min(end,a+seconds)
        selected=[]
        for word_index in range(i,len(words)):
            w=words[word_index]
            if w['end']>limit:break
            selected.append(w)
        if not selected:continue
        endings=[k for k,w in enumerate(selected) if w['end']-a>=min(3,seconds*.55) and w['text'].rstrip().endswith(('.', '?', '!', '।'))]
        if endings:selected=selected[:endings[-1]+1]
        b=min(end,selected[-1]['end']+.08);text=' '.join(w['text'] for w in selected)
        if b-a>=min(3,seconds*.5) and len(text.strip())>8:
            output.append({'start':a,'duration':b-a,'text':text});last=a
    return output


def select_highlights(segments, start, end, seconds, count, topic='', progress=lambda *_: None):
    global EMBEDDER
    import numpy as np
    from fastembed import TextEmbedding
    options = candidates(segments, start, end, seconds)
    if not options: raise ValueError('No clear speech found in this range.')
    progress('analyzing', 'Ranking speech with a local multilingual semantic model…')
    if EMBEDDER is None:
        EMBEDDER = TextEmbedding(EMBED_MODEL, cache_dir=str(MODELS / 'embeddings'), threads=None if CLOUD_LITE else min(4, os.cpu_count() or 2), **({'mmap':True} if CLOUD_LITE else {}))
    prompts = [topic] if topic.strip() else [
        'A surprising insight, important lesson, or useful practical advice.',
        'An engaging story with a clear conclusion or a memorable opinion.',
        'An explanation of how to solve a problem or avoid a common mistake.']
    vectors = np.array(list(EMBEDDER.embed([x['text'] for x in options] + prompts)))
    vectors /= np.maximum(np.linalg.norm(vectors, axis=1, keepdims=True), 1e-8)
    similarities = vectors[:len(options)] @ vectors[len(options):].T
    for i, c in enumerate(options):
        c['relevance'] = round(float(similarities[i].max()), 3)
        c['title'] = c['text'][:90].strip()
        c['reason'] = 'Semantic match to your topic' if topic.strip() else 'Semantic match to insights, stories or practical advice'
    ranked = sorted(options, key=lambda x:x['relevance'], reverse=True)
    chosen = []
    for c in ranked:
        if all(min(c['start']+c['duration'], p['start']+p['duration']) <= max(c['start'], p['start']) for p in chosen):
            chosen.append(c)
            if count and len(chosen) >= count: break
    if CLOUD_LITE:
        EMBEDDER=None
        import gc
        gc.collect()
    return chosen


def caption_cues(segments, start, duration):
    words=[]
    for s in segments:
        source_words=s.get('words') or [{'start':s['start'], 'end':s['end'], 'text':s['text']}]
        for w in source_words:
            a=max(0, w['start']-start); b=min(duration,w['end']-start)
            if b>a and w['text'].strip(): words.append({'start':a,'end':b,'text':w['text'].strip()})
    cues=[]; group=[]
    for w in words:
        if group and (len(group)>=5 or w['end']-group[0]['start']>2.6 or w['start']-group[-1]['end']>.6):
            cues.append({'start':group[0]['start'],'end':group[-1]['end'],'text':' '.join(x['text'] for x in group) }); group=[]
        group.append(w)
    if group: cues.append({'start':group[0]['start'],'end':group[-1]['end'],'text':' '.join(x['text'] for x in group)})
    return cues


def srt_time(seconds):
    n=max(0,round(seconds*1000)); h,n=divmod(n,3600000); m,n=divmod(n,60000); s,ms=divmod(n,1000)
    return f'{h:02}:{m:02}:{s:02},{ms:03}'


def write_srt(path, cues):
    path.write_text('\n\n'.join(f"{i+1}\n{srt_time(c['start'])} --> {srt_time(c['end'])}\n{c['text']}" for i,c in enumerate(cues))+'\n', encoding='utf-8')


class SpeakerTracker:
    """Face tracking plus mouth-motion heuristic; no voice identity or diarization."""
    def __init__(self):
        import cv2
        self.cv2=cv2
        model=MODELS/'face_detection_yunet_2023mar.onnx'
        if not model.is_file(): raise RuntimeError('Face model is missing. Run Setup AI.command first.')
        self.detector=cv2.FaceDetectorYN.create(str(model),'',(640,360),.65,.3,5000)
        self.previous=[]; self.center=None; self.last_switch=-10; self.selected=None; self.detections=0

    def update(self, frame, t):
        import numpy as np
        cv2=self.cv2; h,w=frame.shape[:2]; scale=min(1,640/w)
        small=cv2.resize(frame,(int(w*scale),int(h*scale)))
        self.detector.setInputSize((small.shape[1],small.shape[0]))
        _,faces=self.detector.detect(small)
        current=[]
        if faces is not None:
            for f in faces:
                x,y,fw,fh=[float(v/scale) for v in f[:4]]
                # Compare stabilized lower-face patches rather than whole-frame motion.
                x0,x1=max(0,int(x)),min(w,int(x+fw)); y0,y1=max(0,int(y+fh*.55)),min(h,int(y+fh*.95))
                if x1<=x0 or y1<=y0: continue
                patch=cv2.resize(cv2.cvtColor(frame[y0:y1,x0:x1],cv2.COLOR_BGR2GRAY),(40,20))
                center=x+fw/2
                prior=min(self.previous,key=lambda p:abs(p['x']-center),default=None)
                motion=float(np.mean(cv2.absdiff(patch,prior['patch'])))/255 if prior and abs(prior['x']-center)<fw else 0
                current.append({'x':center,'y':y+fh*.5,'area':fw*fh,'patch':patch,'motion':motion})
        if current:
            self.detections+=1
            best=max(current,key=lambda p:p['motion']+.01*math.sqrt(p['area']/(w*h)))
            previous=min(current,key=lambda p:abs(p['x']-(self.selected or w/2)))
            if self.selected is None or t-self.last_switch>1.5 and best['motion']>previous['motion']*1.35+.008:
                self.selected=best['x'];self.last_switch=t
            else: self.selected=previous['x']
            target=self.selected
        else: target=w/2 if self.center is None else self.center
        self.center=target if self.center is None else .7*self.center+.3*target
        self.previous=current
        return self.center


@lru_cache(maxsize=1)
def font_path():
    paths=[ROOT/'assets'/'NotoSansDevanagari.ttf',Path('/System/Library/Fonts/Supplemental/Arial Unicode.ttf'),Path('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf')]
    return next((str(p) for p in paths if p.is_file()),None)


@lru_cache(maxsize=24)
def caption_font(size):
    from PIL import ImageFont
    return ImageFont.truetype(font_path(),size) if font_path() else ImageFont.load_default(size=size)


def draw_caption(frame, text, style='bold'):
    from PIL import Image, ImageDraw, ImageFont
    import cv2
    image=Image.fromarray(cv2.cvtColor(frame,cv2.COLOR_BGR2RGB)); draw=ImageDraw.Draw(image)
    width,height=image.size
    size=max(18,int(min(width,height)*.052))
    font=caption_font(size)
    # Actual glyph widths preserve Hindi and other Unicode text.
    lines=[];line=''
    for word in text.split():
        proposed=(line+' '+word).strip()
        if draw.textbbox((0,0),proposed,font=font)[2]>width*.86 and line: lines.append(line);line=word
        else: line=proposed
    if line:lines.append(line)
    lines=lines[:3]
    line_h=int(size*1.5); top=int(height*.78)-len(lines)*line_h//2
    for i,line in enumerate(lines):
        box=draw.textbbox((0,0),line,font=font); tw=box[2]-box[0]; x=(width-tw)//2; y=top+i*line_h
        if style=='box': draw.rounded_rectangle((x-12,y-6,x+tw+12,y+line_h),radius=8,fill=(15,18,25))
        draw.text((x,y),line,font=font,fill='#ffe251' if style=='yellow' else 'white',stroke_width=2,stroke_fill='black')
    return cv2.cvtColor(__import__('numpy').array(image),cv2.COLOR_RGB2BGR)


def render(source, destination, start, duration, layout, cues, tracking, style, progress=lambda *_: None):
    import cv2
    import numpy as np
    cap=cv2.VideoCapture(str(source))
    if not cap.isOpened(): raise RuntimeError('Cannot open source video for AI export')
    cap.set(cv2.CAP_PROP_POS_MSEC,start*1000)
    fps=float(cap.get(cv2.CAP_PROP_FPS)) or 30
    if not math.isfinite(fps) or fps<=0:fps=30
    w=int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)); h=int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    if layout=='original': out_w,out_h=w//2*2,h//2*2
    elif layout=='square':out_w,out_h=1080,1080
    else:out_w,out_h=1080,1920
    if CLOUD_LITE:
        if layout!='original':out_w=720;out_h=720 if layout=='square' else 1280
        else:
            factor=min(1,1280/max(w,h));out_w=max(2,int(w*factor)//2*2);out_h=max(2,int(h*factor)//2*2)
    tracker=SpeakerTracker() if tracking else None
    total=math.ceil(duration*fps); center=w/2;cue_i=0; frames=0
    # Pipe rendered frames into FFmpeg and take the original audio at the same offset.
    args=['ffmpeg','-nostdin','-v','error','-y','-f','rawvideo','-pix_fmt','bgr24','-s',f'{out_w}x{out_h}','-r',str(fps),'-i','pipe:0','-ss',str(start),'-i',str(source),'-map','0:v:0','-map','1:a?','-t',str(duration),'-c:v','libx264','-threads','1' if CLOUD_LITE else '0','-preset','fast','-crf','22','-pix_fmt','yuv420p','-c:a','aac','-movflags','+faststart',str(destination)]
    with tempfile.TemporaryFile() as errors:
        encoder=subprocess.Popen(args,stdin=subprocess.PIPE,stderr=errors)
        try:
            for index in range(total):
                ok,frame=cap.read()
                if not ok:break
                t=index/fps
                if tracker and index%max(1,round(fps/4))==0:center=tracker.update(frame,t)
                if layout=='portrait-fit':
                    factor=min(out_w/w,out_h/h); scaled=cv2.resize(frame,(max(1,round(w*factor)),max(1,round(h*factor))))
                    canvas=np.zeros((out_h,out_w,3),np.uint8); sh,sw=scaled.shape[:2]; canvas[(out_h-sh)//2:(out_h-sh)//2+sh,(out_w-sw)//2:(out_w-sw)//2+sw]=scaled; frame=canvas
                elif layout!='original':
                    ratio=out_w/out_h; cw=min(w,int(h*ratio));ch=min(h,int(w/ratio)); x=max(0,min(w-cw,round(center-cw/2)));y=(h-ch)//2
                    frame=cv2.resize(frame[y:y+ch,x:x+cw],(out_w,out_h))
                else: frame=cv2.resize(frame,(out_w,out_h))
                while cue_i<len(cues) and cues[cue_i]['end']<=t:cue_i+=1
                if cue_i<len(cues) and cues[cue_i]['start']<=t<cues[cue_i]['end']:frame=draw_caption(frame,cues[cue_i]['text'],style)
                encoder.stdin.write(frame.tobytes());frames+=1
                if index%max(1,round(fps*2))==0:progress('cutting',f'Exporting {min(100,round(index/total*100))}%')
            encoder.stdin.close()
            code=encoder.wait(timeout=120)
            if code: errors.seek(0);raise RuntimeError(errors.read().decode(errors='replace')[-1800:])
            if not frames:raise RuntimeError('No frames decoded for selected clip')
        except BaseException:
            encoder.kill();encoder.wait();raise
        finally:cap.release()
    return {'frames':frames,'face_samples':tracker.detections if tracker else 0,'tracking':'face + mouth motion' if tracker else 'off'}


if __name__=='__main__' and len(sys.argv)==7 and sys.argv[1]=='--transcribe':
    _transcribe_local(Path(sys.argv[2]),Path(sys.argv[3]),sys.argv[4],sys.argv[5],lambda status,detail:print(json.dumps({'status':status,'detail':detail}),flush=True),task=sys.argv[6])
