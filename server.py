#!/usr/bin/env python3
import json, math, os, subprocess, threading, uuid, mimetypes, base64, hmac, shutil, zipfile, tempfile
from pathlib import Path
from urllib.parse import urlparse, unquote, parse_qs
import sys, importlib.util
sys.path.insert(0,str(Path(__file__).resolve().parent))
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
ROOT=Path(__file__).resolve().parent
DATA=Path(os.environ.get('DATA_DIR',str(ROOT/'data'))); DATA.mkdir(parents=True,exist_ok=True)
PORT=int(os.environ.get('PORT','8765'))
CLOUD_LITE=os.environ.get('CLOUD_LITE')=='1'
MAX_UPLOAD=int(os.environ.get('MAX_UPLOAD_BYTES',str(95*1024**2 if CLOUD_LITE else 2*1024**3)))
MAX_SOURCE_SECONDS=300 if CLOUD_LITE else 0
PUBLIC_ORIGIN=os.environ.get('PUBLIC_ORIGIN') or os.environ.get('RENDER_EXTERNAL_URL') or f'http://127.0.0.1:{PORT}'
APP_USER=os.environ.get('APP_USER','')
APP_PASSWORD=os.environ.get('APP_PASSWORD','')
BIND=os.environ.get('BIND_HOST','127.0.0.1')
if BIND!='127.0.0.1' and (not APP_USER or len(APP_PASSWORD)<20):
    raise RuntimeError('Network deployment requires APP_USER and APP_PASSWORD of at least 20 characters')
LOCK=threading.Lock()
def load():
    try: return json.loads((DATA/'jobs.json').read_text())
    except FileNotFoundError: return []
JOBS=load()
for j in JOBS:
    if j['status'] in ('queued','downloading','transcribing','analyzing','cutting'): j.update(status='failed',error='App restarted. Please create this job again.')
def save():
    with LOCK:
        p=DATA/'jobs.tmp'; p.write_text(json.dumps(JOBS)); p.replace(DATA/'jobs.json')
def run(args):
    env=os.environ.copy()
    if args[0]=='yt-dlp':
        local=ROOT/'vendor'/'bin'/'yt-dlp'
        args=[str(local) if local.exists() else 'yt-dlp','--ignore-config',*args[1:]]
        if local.exists(): env['PYTHONPATH']=str(ROOT/'vendor')
        node=shutil.which('node')
        if node: args[1:1]=['--js-runtimes','node:'+node]
    p=subprocess.run(args,env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
    if p.returncode: raise RuntimeError(p.stderr[-1600:] or 'Processing failed')
    return p.stdout

def friendly_error(error, browser=''):
    message=str(error)
    # Warnings can mention age restrictions even when the actual failure is unrelated.
    lines=[line.strip() for line in message.splitlines() if line.strip().startswith('ERROR:')]
    actual='\n'.join(lines) if lines else message
    lower=actual.lower()
    if any(word in lower for word in ('keychain','decrypt','cookie database','could not copy','cookies database')):
        return 'Browser login could not be read. '+actual[-1000:]+' Check macOS access prompts and use the browser/profile where this video plays.'
    if 'confirm your age' in lower or 'age-restricted' in lower:
        if browser:
            return 'YouTube still requires age verification with the selected '+browser+' login. The browser selection was received. Make sure this video plays in that browser’s signed-in profile; a different Chrome profile may be needed. You can enter its profile folder below (for example Default or Profile 1).'
        return 'This video requires an age-verified YouTube login. Confirm playback in your browser, then select that browser under YouTube login and create a new job.'
    if 'sign in to confirm' in lower:
        return 'YouTube requires a login or bot check. Confirm playback in the selected browser and profile, then try again. '+actual[-600:]
    return actual[-1600:]
SEM=threading.Semaphore(1)
def process(job):
    with SEM:
        folder=DATA/job['id']; folder.mkdir(exist_ok=True)
        try:
            def progress(status, detail):
                job.update(status=status,detail=detail); save()
            if job.get('upload'):
                source=DATA/'uploads'/job['upload']
                if not source.is_file(): raise ValueError('Uploaded video is missing')
            else:
                progress('downloading','Downloading source video…')
                browser_spec=job.get('browser','')+(':'+job['profile'] if job.get('profile') else '')
                login=['--cookies-from-browser',browser_spec] if job.get('browser') else []
                info=json.loads(run(['yt-dlp',*login,'--no-playlist','--max-filesize',str(MAX_UPLOAD),'--dump-single-json','--skip-download','--',job['url']]))
                if MAX_SOURCE_SECONDS and float(info.get('duration') or 0)>MAX_SOURCE_SECONDS:raise ValueError('Free cloud mode supports source videos up to 5 minutes. Upload a shorter recording.')
                job['title']=info.get('title','Untitled video'); job['duration']=float(info.get('duration') or 0); save()
                run(['yt-dlp',*login,'--no-playlist','--max-filesize',str(MAX_UPLOAD),'-f','bv*[height<=1080]+ba/b[height<=1080]','--merge-output-format','mp4','-o',str(folder/'source.%(ext)s'),'--',job['url']])
                sources=[p for p in folder.glob('source.*') if p.suffix not in ('.part','.ytdl')]
                if not sources: raise RuntimeError('No source video downloaded')
                source=sources[0]
            duration=float(run(['ffprobe','-v','error','-show_entries','format=duration','-of','default=noprint_wrappers=1:nokey=1',str(source)]))
            if MAX_SOURCE_SECONDS and duration>MAX_SOURCE_SECONDS:raise ValueError('Free cloud mode supports source videos up to 5 minutes')
            offset=job.get('start',0)
            end=min(duration,job.get('end') or duration)
            if offset>=end: raise ValueError('Source start must be before the video end')
            span=end-offset
            transcript={'segments':[]}
            needs_asr=job.get('captions',False) or job.get('selection')=='highlights'
            if needs_asr:
                import ai_engine
                transcript=ai_engine.transcribe(source,folder,job.get('model','base'),job.get('language',''),progress)
                job['transcript']=True;job['detected_language']=transcript['language'];save()
                if not transcript['segments'] and job.get('captions'): job.setdefault('warnings',[]).append('No clear speech found; captions could not be generated.')
            if job.get('selection')=='highlights':
                highlight_segments=transcript['segments']
                if CLOUD_LITE and transcript.get('language')!='en':
                    progress('transcribing','Translating speech for the lightweight English highlight model…')
                    translated=ai_engine.transcribe(source,folder,'tiny',transcript.get('language',''),progress,task='translate')
                    highlight_segments=translated['segments']
                    job.setdefault('warnings',[]).append('Cloud highlights use translated English speech; captions keep the original language.')
                windows=ai_engine.select_highlights(highlight_segments,offset,end,job['seconds'],job['max_clips'],job.get('topic',''),progress)
            else:
                windows=[{'start':offset+i*job['seconds'],'duration':min(job['seconds'],end-offset-i*job['seconds'])} for i in range(min(math.ceil(span/job['seconds']),job.get('max_clips',500)))]
            job.update(duration=duration,status='cutting',total=len(windows),clips=[]); save()
            for i,window in enumerate(windows):
                start=window['start']; length=window['duration']; name=f'clip-{i+1:03d}.mp4'
                layout=job.get('layout','original')
                filters={'portrait':'scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,setsar=1','square':'scale=1080:1080:force_original_aspect_ratio=increase,crop=1080:1080,setsar=1','portrait-fit':'scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2,setsar=1'}
                if CLOUD_LITE:
                    filters={key:value.replace('1080','720').replace('1920','1280') for key,value in filters.items()}
                    filters['original']="scale=w='min(1280,iw)':h='min(1280,ih)':force_original_aspect_ratio=decrease:force_divisible_by=2"
                vf=['-vf',filters[layout]] if layout in filters else []
                metadata={}
                if job.get('captions') or job.get('tracking'):
                    import ai_engine
                    cues=ai_engine.caption_cues(transcript['segments'],start,length) if job.get('captions') else []
                    if cues: ai_engine.write_srt(folder/Path(name).with_suffix('.srt'),cues)
                    metadata=ai_engine.render(source,folder/name,start,length,layout,cues,job.get('tracking',False),job.get('caption_style','bold'),progress)
                    if cues: metadata['srt']=Path(name).with_suffix('.srt').name
                    if job.get('tracking') and not metadata['face_samples']: job.setdefault('warnings',[]).append(f'Clip {i+1}: no face detected; framing stays centered.')
                else:
                    run(['ffmpeg','-nostdin','-v','error','-y','-ss',str(start),'-i',str(source),'-t',str(length),'-map','0:v:0','-map','0:a?',*vf,'-c:v','libx264','-threads','1' if CLOUD_LITE else '0','-preset','fast','-crf','22','-c:a','aac','-movflags','+faststart',str(folder/name)])
                job['clips'].append(dict(name=name,start=start,duration=length,title=window.get('title',''),relevance=window.get('relevance'),reason=window.get('reason',''),**metadata)); save()
            job.update(status='ready',detail='Export complete'); save()
        except Exception as e: job.update(status='failed',error=friendly_error(e,job.get('browser',''))); save()
class Handler(BaseHTTPRequestHandler):
    def authorized(self):
        if not APP_PASSWORD: return True
        expected='Basic '+base64.b64encode((APP_USER+':'+APP_PASSWORD).encode()).decode()
        if hmac.compare_digest(self.headers.get('Authorization',''),expected): return True
        self.send_response(401); self.send_header('WWW-Authenticate','Basic realm="Clipflow"'); self.send_header('Content-Length','0'); self.end_headers(); return False
    def respond(self,code,data):
        body=json.dumps(data).encode(); self.send_response(code); self.send_header('Content-Type','application/json'); self.send_header('Content-Length',str(len(body))); self.end_headers(); self.wfile.write(body)
    def do_GET(self):
        if urlparse(self.path).path=='/healthz':return self.respond(200,{'status':'ok'})
        if not self.authorized(): return
        path=unquote(urlparse(self.path).path)
        if path=='/api/jobs': return self.respond(200,JOBS)
        if path=='/api/health':
            return self.respond(200,{'ai':all(importlib.util.find_spec(m) is not None for m in ('faster_whisper','fastembed','cv2','PIL')),'face_model':(ROOT/'models'/'face_detection_yunet_2023mar.onnx').is_file(),'cloud_lite':CLOUD_LITE,'upload_limit':MAX_UPLOAD,'max_source_seconds':MAX_SOURCE_SECONDS,'max_clips':3 if CLOUD_LITE else 500})
        if path.startswith('/api/bundle/'):
            job=next((j for j in JOBS if j['id']==path.rsplit('/',1)[-1]),None)
            if not job or job['status']!='ready': return self.respond(404,{'error':'Completed project not found'})
            with tempfile.TemporaryFile() as bundle:
                with zipfile.ZipFile(bundle,'w',compression=zipfile.ZIP_STORED) as archive:
                    for clip in job['clips']:
                        archive.write(DATA/job['id']/clip['name'],clip['name'])
                        if clip.get('srt'): archive.write(DATA/job['id']/clip['srt'],clip['srt'])
                    transcript=DATA/job['id']/'transcript.json'
                    if transcript.is_file():archive.write(transcript,'transcript.json')
                size=bundle.tell(); bundle.seek(0)
                self.send_response(200); self.send_header('Content-Type','application/zip'); self.send_header('Content-Disposition','attachment; filename="clipflow-clips.zip"'); self.send_header('Content-Length',str(size)); self.end_headers()
                shutil.copyfileobj(bundle,self.wfile)
            return
        if path.startswith('/media/'):
            file=(DATA/path.removeprefix('/media/')).resolve(); base=DATA.resolve()
            if base not in file.parents or not ((file.suffix in ('.mp4','.srt') and file.name.startswith('clip-')) or file.name=='transcript.json'): return self.respond(404,{'error':'Not found'})
        else: file=ROOT/'web'/('index.html' if path=='/' else path.lstrip('/'))
        if (ROOT/'web').resolve() not in file.resolve().parents and not path.startswith('/media/'): return self.respond(404,{'error':'Not found'})
        if not file.is_file(): return self.respond(404,{'error':'Not found'})
        size=file.stat().st_size; start=0;end=size-1;partial=False
        range_header=self.headers.get('Range','')
        if file.suffix=='.mp4' and range_header:
            try:
                if not range_header.startswith('bytes=') or ',' in range_header:raise ValueError()
                left,right=range_header[6:].split('-',1)
                if left:start=int(left);end=min(size-1,int(right)) if right else size-1
                else:start=max(0,size-int(right))
                if not 0<=start<=end<size:raise ValueError()
                partial=True
            except ValueError:
                self.send_response(416);self.send_header('Content-Range',f'bytes */{size}');self.send_header('Content-Length','0');self.end_headers();return
        self.send_response(206 if partial else 200);self.send_header('Content-Type',mimetypes.guess_type(str(file))[0] or 'application/octet-stream');self.send_header('Content-Length',str(end-start+1))
        if file.suffix=='.mp4':self.send_header('Accept-Ranges','bytes')
        if partial:self.send_header('Content-Range',f'bytes {start}-{end}/{size}')
        self.end_headers()
        try:
            with file.open('rb') as f:
                f.seek(start);remaining=end-start+1
                while remaining:
                    chunk=f.read(min(1024*1024,remaining))
                    if not chunk:break
                    self.wfile.write(chunk);remaining-=len(chunk)
        except (BrokenPipeError,ConnectionResetError):pass
    def do_POST(self):
        if not self.authorized(): return
        if self.headers.get('Origin') not in (None,PUBLIC_ORIGIN,'http://localhost:8765'): return self.respond(403,{'error':'Invalid origin'})
        if urlparse(self.path).path=='/api/upload':
            try:
                size=int(self.headers.get('Content-Length','0'))
                if not 1<=size<=MAX_UPLOAD:raise ValueError(f'Choose a video up to {MAX_UPLOAD//1024**2} MB')
                name=parse_qs(urlparse(self.path).query).get('name',['video.mp4'])[0]
                suffix=Path(name).suffix.lower()
                if suffix not in ('.mp4','.mov','.mkv','.webm','.m4v'):raise ValueError('Use MP4, MOV, MKV, WebM or M4V')
                folder=DATA/'uploads';folder.mkdir(exist_ok=True);token=uuid.uuid4().hex+suffix
                target=folder/token;remaining=size
                try:
                    with target.open('wb') as output:
                        while remaining:
                            chunk=self.rfile.read(min(1024*1024,remaining))
                            if not chunk:raise ValueError('Upload was interrupted')
                            output.write(chunk);remaining-=len(chunk)
                    width=run(['ffprobe','-v','error','-select_streams','v:0','-show_entries','stream=width','-of','csv=p=0',str(target)]).strip()
                    if not width:raise ValueError('The uploaded file contains no video stream')
                except Exception:
                    target.unlink(missing_ok=True);raise
                return self.respond(201,{'upload':token,'title':Path(name).name})
            except (ValueError,RuntimeError) as e:return self.respond(400,{'error':str(e)})
        if self.path!='/api/jobs': return self.respond(404,{'error':'Not found'})
        try:
            size=int(self.headers.get('Content-Length','0'))
            if size<1 or size>8192: raise ValueError('Request too large')
            body=json.loads(self.rfile.read(size))
            if not isinstance(body,dict):raise ValueError('Expected a JSON object')
            url=str(body.get('url','')).strip(); parsed=urlparse(url)
            upload=body.get('upload','')
            if upload and (not isinstance(upload,str) or Path(upload).name!=upload or not (DATA/'uploads'/upload).is_file()): raise ValueError('Upload not found')
            if not upload and (parsed.scheme!='https' or parsed.hostname not in ('youtube.com','www.youtube.com','m.youtube.com','youtu.be')): raise ValueError('Enter a valid HTTPS YouTube video URL')
            seconds=float(body['minutes'])*60
            if not math.isfinite(seconds) or not 1<=seconds<=3600: raise ValueError('Clip length must be between 1 second and 60 minutes')
            if not body.get('rights'): raise ValueError('Confirm that you can use this video')
            browser=body.get('browser','')
            if browser not in ('','chrome','firefox','safari','edge','brave'): raise ValueError('Invalid browser selection')
            if browser and BIND!='127.0.0.1': raise ValueError('Browser login is only available in local mode')
            profile=str(body.get('profile','')).strip()
            if profile and (len(profile)>60 or not all(c.isalnum() or c in ' _-' for c in profile)): raise ValueError('Enter a profile folder such as Default or Profile 1')
            layout=body.get('layout','original')
            if layout not in ('original','portrait','square','portrait-fit'): raise ValueError('Invalid output layout')
            start=float(body.get('start',0)); end=float(body.get('end') or 0); max_clips=int(body.get('max_clips',50))
            if not math.isfinite(start) or not math.isfinite(end) or start<0 or end<0 or (end and end<=start): raise ValueError('Enter valid source start and end seconds')
            if not 1<=max_clips<=500: raise ValueError('Clip limit must be between 1 and 500')
            selection=body.get('selection','even')
            model=body.get('model','base');language=body.get('language','');style=body.get('caption_style','bold')
            if selection not in ('even','highlights') or model not in ('tiny','base','small') or language not in ('','en','hi','es','fr','de','pt','ja','ko','zh','ar') or style not in ('bold','yellow','box'):raise ValueError('Invalid AI option')
            if CLOUD_LITE and (model!='tiny' or max_clips>3 or seconds>60):raise ValueError('Free cloud mode uses Tiny, up to 3 clips, and a target length of at most 1 minute')
            captions=body.get('captions',False);tracking=body.get('tracking',False)
            if not isinstance(captions,bool) or not isinstance(tracking,bool):raise ValueError('Invalid checkbox option')
            if tracking and layout not in ('portrait','square'):raise ValueError('Speaker framing needs Portrait crop or Square crop')
            topic=str(body.get('topic','')).strip()
            if len(topic)>500:raise ValueError('Topic must be 500 characters or fewer')
            if selection=='highlights' or captions or tracking:
                if not all(importlib.util.find_spec(m) is not None for m in ('faster_whisper','fastembed','cv2','PIL')):raise ValueError('Start with Start.command to use the AI environment. Run Setup AI.command if needed.')
            job=dict(upload=upload,selection=selection,model=model,language=language,caption_style=style,captions=captions,tracking=tracking,topic=topic,layout=layout,start=start,end=end,max_clips=max_clips,profile=profile,browser=browser,id=uuid.uuid4().hex,url=url,seconds=seconds,title=str(body.get('title','Uploaded video'))[:200] if upload else 'New YouTube video',status='queued',clips=[],total=0)
            JOBS.insert(0,job); save(); threading.Thread(target=process,args=(job,),daemon=True).start(); return self.respond(201,job)
        except (ValueError,KeyError,TypeError,OverflowError) as e: return self.respond(400,{'error':str(e)})
if __name__=='__main__':
    server=ThreadingHTTPServer((BIND,PORT),Handler)
    print('Clipflow: '+PUBLIC_ORIGIN,flush=True)
    server.serve_forever()
