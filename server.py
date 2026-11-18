#!/usr/bin/env python3
import json, math, os, subprocess, threading, uuid, mimetypes, base64, hmac, shutil, zipfile, tempfile, secrets, hashlib
from pathlib import Path
from urllib.parse import urlparse, unquote, parse_qs
from http.cookies import SimpleCookie
import sys, importlib.util, re
sys.path.insert(0,str(Path(__file__).resolve().parent))
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
import storage
ROOT=Path(__file__).resolve().parent
DATA=Path(os.environ.get('DATA_DIR',str(ROOT/'data'))); DATA.mkdir(parents=True,exist_ok=True)
PORT=int(os.environ.get('PORT','8765'))
CLOUD_LITE=os.environ.get('CLOUD_LITE')=='1'
PUBLIC_ACCESS=os.environ.get('PUBLIC_ACCESS','1' if CLOUD_LITE else '0')=='1'
MAX_UPLOAD=int(os.environ.get('MAX_UPLOAD_BYTES','0'))
MAX_SOURCE_SECONDS=0
PUBLIC_ORIGIN=os.environ.get('PUBLIC_ORIGIN') or os.environ.get('RENDER_EXTERNAL_URL') or f'http://127.0.0.1:{PORT}'
APP_USER=os.environ.get('APP_USER','')
APP_PASSWORD=os.environ.get('APP_PASSWORD','')
BIND=os.environ.get('BIND_HOST','127.0.0.1')
if not PUBLIC_ACCESS and BIND!='127.0.0.1' and (not APP_USER or len(APP_PASSWORD)<20):
    raise RuntimeError('Network deployment requires APP_USER and APP_PASSWORD of at least 20 characters')
LOCK=threading.RLock()
def load():
    try: return json.loads((DATA/'jobs.json').read_text())
    except FileNotFoundError: return []
JOBS=load()
for j in JOBS:
    if j['status'] in ('queued','downloading','transcribing','analyzing','cutting'): j.update(status='failed',error='App restarted. Please create this job again.')
def save():
    with LOCK:
        p=DATA/'jobs.tmp'; p.write_text(json.dumps(JOBS)); p.replace(DATA/'jobs.json')
SESSION_KEY_FILE=DATA/'.session-secret'
if PUBLIC_ACCESS:
    if not SESSION_KEY_FILE.exists():
        with SESSION_KEY_FILE.open('xb') as secret_file:secret_file.write(secrets.token_bytes(32))
        SESSION_KEY_FILE.chmod(0o600)
    SESSION_KEY=SESSION_KEY_FILE.read_bytes()
else:SESSION_KEY=b''

def workspace(owner=''):
    if not owner:return DATA
    if not re.fullmatch(r'[a-f0-9]{32}',owner):raise ValueError('Invalid workspace')
    path=DATA/'visitors'/owner;path.mkdir(parents=True,exist_ok=True);return path

def public_job(job):
    return {key:value for key,value in job.items() if key!='owner'}

def run(args):
    env=os.environ.copy()
    if args[0]=='yt-dlp':
        local=ROOT/'vendor'/'bin'/'yt-dlp'
        installed=shutil.which('yt-dlp')
        if local.exists():
            cmd=[str(local)]
        elif importlib.util.find_spec('yt_dlp') is not None:
            cmd=[sys.executable,'-m','yt_dlp']
        elif installed:
            cmd=[installed]
        else:
            raise RuntimeError('YouTube downloader is missing. Install the dependencies from requirements-ai.txt or upload a video.')
        if local.exists(): env['PYTHONPATH']=str(ROOT/'vendor')
        base=[*cmd,'--ignore-config','--no-update','--socket-timeout','20']
        try:
            import nodejs_wheel.executable as nw
            nw_node=Path(nw.ROOT_DIR)/('node.exe' if os.name=='nt' else 'bin/node')
            if nw_node.is_file(): base.extend(['--js-runtimes',f'node:{nw_node}'])
        except Exception: pass
        for rt in ('deno','node'):
            rt_path=shutil.which(rt)
            if rt_path: base.extend(['--js-runtimes',f'{rt}:{rt_path}'])
        tail=args[1:]
        if '--extractor-args' in tail:
            attempts=[[*base,*tail]]
        else:
            clients='youtube:player_client=default,web_safari,mweb,android_vr,web_embedded'
            attempts=[[*base,*tail],[*base,'--extractor-args',clients,*tail]]
            if importlib.util.find_spec('curl_cffi') is not None:
                attempts.append([*base,'--extractor-args','youtube:player_client=web_safari,mweb,android_vr,web_embedded,tv,ios','--impersonate','chrome',*tail])
        last_err=''
        for attempt in attempts:
            p=subprocess.run(attempt,env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
            if not p.returncode: return p.stdout
            last_err=p.stderr[-1600:] or 'Processing failed'
        raise RuntimeError(last_err)
    p=subprocess.run(args,env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
    if p.returncode: raise RuntimeError(p.stderr[-1600:] or 'Processing failed')
    return p.stdout

def download_with_pytubefix(url, folder, job):
    from pytubefix import YouTube
    last_err=None
    max_res=720 if CLOUD_LITE else 1080
    for client in ('WEB','ANDROID_VR','MWEB','IOS','TV','WEB_EMBED'):
        try:
            yt=YouTube(url,client=client)
            streams=yt.streams
            if not streams: continue
            job['title']=yt.title or 'Untitled video'; job['duration']=float(yt.length or 0); save()
            prog=streams.filter(progressive=True,file_extension='mp4').order_by('resolution').desc().first()
            if prog:
                prog.download(output_path=str(folder),filename='source.mp4')
                return
            v_streams=[s for s in streams.filter(only_video=True) if getattr(s,'resolution',None) and str(s.resolution).endswith('p') and str(s.resolution)[:-1].isdigit() and int(str(s.resolution)[:-1])<=max_res]
            v_streams.sort(key=lambda s:(s.subtype=='mp4',int(str(s.resolution)[:-1])),reverse=True)
            v_stream=v_streams[0] if v_streams else streams.filter(only_video=True).order_by('resolution').desc().first()
            a_stream=streams.filter(only_audio=True,subtype='mp4').order_by('abr').desc().first() or streams.filter(only_audio=True).order_by('abr').desc().first()
            if not v_stream: continue
            v_path=folder/f'video_tmp.{v_stream.subtype or "mp4"}'
            v_stream.download(output_path=str(folder),filename=v_path.name)
            if a_stream:
                a_path=folder/f'audio_tmp.{a_stream.subtype or "m4a"}'
                a_stream.download(output_path=str(folder),filename=a_path.name)
                run(['ffmpeg','-nostdin','-v','error','-y','-i',str(v_path),'-i',str(a_path),'-c:v','copy','-c:a','aac',str(folder/'source.mp4')])
                v_path.unlink(missing_ok=True); a_path.unlink(missing_ok=True)
            else:
                v_path.replace(folder/'source.mp4')
            return
        except Exception as e:
            last_err=e
            for tmp in folder.glob('*_tmp.*'): tmp.unlink(missing_ok=True)
    raise RuntimeError(str(last_err or 'Unable to download YouTube video'))

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
        return 'This video requires an age-verified YouTube login. Confirm playback in your browser, then select that browser or paste cookies.txt under YouTube login options and create a new job.'
    if 'failed to extract any player response' in lower or 'sign in to confirm' in lower or ' blocked by youtube' in lower:
        if CLOUD_LITE:
            return 'YouTube could not be imported by this cloud server. Try the same link in local ClipFlow, or use Upload video with a saved copy you can edit.'
        return 'YouTube could not be imported. Check that the link plays in your browser, or upload a saved copy. '+actual[-600:]
    return actual[-1600:]
def normalize_cookies(raw):
    text=str(raw or '').strip()
    if not text: return ''
    domains=('youtube','youtu.be','google','ytimg','ggpht')
    lines=[]
    if text.startswith('['):
        try:
            for c in json.loads(text):
                if not isinstance(c,dict) or not c.get('name'): continue
                dom=str(c.get('domain') or '.youtube.com').strip()
                if any(d in dom.lower() for d in domains):
                    flag='TRUE' if dom.startswith('.') else 'FALSE'
                    path=str(c.get('path') or '/')
                    sec='TRUE' if c.get('secure',True) else 'FALSE'
                    exp=str(int(float(c.get('expirationDate') or c.get('expires') or 0)))
                    lines.append(f'{dom}\t{flag}\t{path}\t{sec}\t{exp}\t{c["name"]}\t{c.get("value","")}')
        except Exception: pass
    if not lines and '\t' in text:
        all_tab=[]
        for raw_line in text.splitlines():
            line=raw_line.strip()
            if not line or (line.startswith('#') and not line.startswith('#HttpOnly_')): continue
            parts=line.split('\t')
            if len(parts)>=7:
                dom=parts[0].removeprefix('#HttpOnly_').lower()
                row='\t'.join(parts[:7]); all_tab.append(row)
                if any(d in dom for d in domains): lines.append(row)
        if not lines: lines=all_tab
    if not lines:
        for chunk in text.replace('\n',';').split(';'):
            if '=' in chunk:
                k,v=chunk.split('=',1); k,v=k.strip(),v.strip()
                if k and not k.startswith('#'): lines.append(f'.youtube.com\tTRUE\t/\tTRUE\t0\t{k}\t{v}')
    return '# Netscape HTTP Cookie File\n'+'\n'.join(lines)+'\n' if lines else ''

def highlights_available():
    return all(importlib.util.find_spec(module) is not None for module in ('faster_whisper', 'fastembed'))

def select_windows(job, source, folder, start, end, progress):
    seconds, count = job['seconds'], job['max_clips']
    if job['selection'] == 'highlights':
        if highlights_available():
            try:
                import ai_engine
                transcript = ai_engine.transcribe(source, folder, job['model'], progress=progress)
                job.update(transcript=True, detected_language=transcript['language'])
                segments = transcript['segments']
                if CLOUD_LITE and segments and transcript['language'] != 'en':
                    segments = ai_engine.transcribe(source, folder, job['model'], transcript['language'], progress, task='translate')['segments']
                windows = ai_engine.select_highlights(segments, start, end, seconds, count, progress=progress)
                job['selection_used'] = 'highlights'
                return windows
            except ValueError as error:
                reason = str(error)
            except Exception as error:
                print(f"AI highlights failed for {job['id']}: {error}", file=sys.stderr, flush=True)
                reason = 'AI analysis could not finish.'
        else:
            reason = 'AI dependencies are unavailable on this server.'
        job.setdefault('warnings', []).append(reason + ' Exporting evenly spaced clips.')
    job['selection_used'] = 'even'
    total = math.ceil((end - start) / seconds)
    return [
        {'start': start + i * seconds, 'duration': min(seconds, end - start - i * seconds)}
        for i in range(min(total, count or total))
    ]

SEM=threading.Semaphore(1)
def process(job, cookies=''):
    with SEM:
        data=workspace(job.get('owner',''));folder=data/job['id']; folder.mkdir(exist_ok=True)
        try:
            def progress(status, detail):
                job.update(status=status,detail=detail); save()
            if job.get('upload'):
                source=data/'uploads'/job['upload']
                if not source.is_file(): raise ValueError('Uploaded video is missing')
            else:
                progress('downloading','Downloading source video…')
                cookie_text=normalize_cookies(cookies or os.environ.get('YOUTUBE_COOKIES',''))
                cookie_file=folder/'.cookies.txt'
                fmt='bv*[height<=720]+ba/b[height<=720]/b/bv*+ba' if CLOUD_LITE else 'bv*[height<=1080]+ba/b[height<=1080]/b/bv*+ba'
                dl_tail=['--no-playlist','--write-info-json','-f',fmt,'--merge-output-format','mp4','-o',str(folder/'source.%(ext)s'),'--',job['url']]
                try:
                    login=[]
                    if cookie_text:
                        with cookie_file.open('w',encoding='utf-8') as cf:cf.write(cookie_text)
                        cookie_file.chmod(0o600)
                        login=['--cookies',str(cookie_file)]
                    elif job.get('browser'):
                        browser_spec=job.get('browser','')+(':'+job['profile'] if job.get('profile') else '')
                        login=['--cookies-from-browser',browser_spec]
                    try:
                        run(['yt-dlp',*dl_tail])
                    except RuntimeError as download_error:
                        for partial in folder.glob('source.*'):
                            partial.unlink(missing_ok=True)
                        if login:
                            run(['yt-dlp',*login,*dl_tail])
                        elif importlib.util.find_spec('pytubefix') is not None:
                            try:
                                download_with_pytubefix(job['url'],folder,job)
                            except RuntimeError as fallback_error:
                                for partial in folder.glob('source.*'):
                                    partial.unlink(missing_ok=True)
                                raise download_error from fallback_error
                        else:
                            raise
                    info_file=folder/'source.info.json'
                    if info_file.is_file():
                        info=json.loads(info_file.read_text(encoding='utf-8',errors='replace'))
                        job['title']=info.get('title','Untitled video'); job['duration']=float(info.get('duration') or 0); save()
                finally:
                    cookie_file.unlink(missing_ok=True)
                    for extra in folder.glob('*.info.json'):extra.unlink(missing_ok=True)
                sources=[p for p in folder.glob('source.*') if p.suffix not in ('.part','.ytdl','.json')]
                if not sources: raise RuntimeError('No source video downloaded')
                source=sources[0]
            duration=float(run(['ffprobe','-v','error','-show_entries','format=duration','-of','default=noprint_wrappers=1:nokey=1',str(source)]))
            offset=job.get('start',0)
            end=min(duration,job.get('end') or duration)
            if offset>=end: raise ValueError('Source start must be before the video end')
            windows=select_windows(job,source,folder,offset,end,progress)
            job.update(duration=duration,status='cutting',detail='Cutting video clips…',total=len(windows),clips=[]); save()
            for i,window in enumerate(windows):
                start=window['start']; length=window['duration']; name=f'clip-{i+1:03d}.mp4'
                layout=job.get('layout','original')
                filters={'portrait':'scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,setsar=1','square':'scale=1080:1080:force_original_aspect_ratio=increase,crop=1080:1080,setsar=1','portrait-fit':"scale=w='trunc(iw*sar/2)*2':h=ih,setsar=1,scale=1080:1920:force_original_aspect_ratio=decrease:force_divisible_by=2,pad=1080:1920:(ow-iw)/2:(oh-ih)/2,setsar=1"}
                if CLOUD_LITE:
                    filters={key:value.replace('1080','720').replace('1920','1280') for key,value in filters.items()}
                    filters['original']="scale=w='min(1280,iw)':h='min(1280,ih)':force_original_aspect_ratio=decrease:force_divisible_by=2"
                vf=['-vf',filters[layout]] if layout in filters else []
                run(['ffmpeg','-nostdin','-v','error','-y','-ss',str(start),'-i',str(source),'-t',str(length),'-map','0:v:0','-map','0:a?',*vf,'-c:v','libx264','-threads','1' if CLOUD_LITE else '0','-preset','ultrafast' if CLOUD_LITE else 'fast','-crf','23','-c:a','aac','-movflags','+faststart',str(folder/name)])
                job['clips'].append(dict(name=name,start=start,duration=length,title=window.get('title', f'Clip {i+1}'),relevance=window.get('relevance'),reason=window.get('reason',''))); save()
            job.update(status='ready',detail='Export complete'); save()
        except Exception as e: job.update(status='failed',error=friendly_error(e,job.get('browser',''))); save()
class Handler(BaseHTTPRequestHandler):
    def visible_jobs(self):
        return [j for j in JOBS if j.get('owner','')==self.owner]
    def end_headers(self):
        if PUBLIC_ACCESS:
            self.send_header('Cache-Control','private, no-store')
            self.send_header('Vary','Cookie')
        if getattr(self,'session_cookie',None):self.send_header('Set-Cookie',self.session_cookie)
        super().end_headers()
    def authorized(self):
        self.owner='';self.session_cookie=None;self.data=DATA
        if PUBLIC_ACCESS:
            cookies=SimpleCookie()
            try:cookies.load(self.headers.get('Cookie',''))
            except Exception:pass
            token=cookies.get('clipflow_workspace')
            owner,separator,signature=(token.value if token else '').partition('.')
            expected=hmac.new(SESSION_KEY,owner.encode(),hashlib.sha256).hexdigest()
            if not (re.fullmatch(r'[a-f0-9]{32}',owner) and separator and hmac.compare_digest(signature,expected)):
                owner=uuid.uuid4().hex
                signature=hmac.new(SESSION_KEY,owner.encode(),hashlib.sha256).hexdigest()
                secure='; Secure' if PUBLIC_ORIGIN.startswith('https://') else ''
                self.session_cookie=f'clipflow_workspace={owner}.{signature}; Path=/; Max-Age=2592000; HttpOnly; SameSite=Lax'+secure
            self.owner=owner;self.data=workspace(owner)
            return True
        if not APP_PASSWORD: return True
        expected='Basic '+base64.b64encode((APP_USER+':'+APP_PASSWORD).encode()).decode()
        if hmac.compare_digest(self.headers.get('Authorization',''),expected): return True
        self.send_response(401); self.send_header('WWW-Authenticate','Basic realm="Clipflow"'); self.send_header('Content-Length','0'); self.end_headers(); return False
    def respond(self,code,data):
        body=json.dumps(data).encode(); self.send_response(code); self.send_header('Content-Type','application/json'); self.send_header('Cache-Control','no-store'); self.send_header('Content-Length',str(len(body))); self.end_headers(); self.wfile.write(body)
    def do_GET(self):
        if urlparse(self.path).path=='/healthz':return self.respond(200,{'status':'ok'})
        if not self.authorized(): return
        path=unquote(urlparse(self.path).path)
        if path=='/api/jobs':
            with LOCK:return self.respond(200,[public_job(j) for j in self.visible_jobs()])
        if path=='/api/storage':
            with LOCK:return self.respond(200,storage.inventory(self.data,self.visible_jobs()))
        if path=='/api/health':
            return self.respond(200,{'ai':highlights_available(),'face_model':(ROOT/'models'/'face_detection_yunet_2023mar.onnx').is_file(),'cloud_lite':CLOUD_LITE,'upload_limit':MAX_UPLOAD,'max_source_seconds':MAX_SOURCE_SECONDS,'max_clips':None,'public_access':PUBLIC_ACCESS})
        if path.startswith('/api/bundle/'):
            job=next((j for j in self.visible_jobs() if j['id']==path.rsplit('/',1)[-1]),None)
            if not job or job['status']!='ready': return self.respond(404,{'error':'Completed project not found'})
            with tempfile.TemporaryFile() as bundle:
                with LOCK, zipfile.ZipFile(bundle,'w',compression=zipfile.ZIP_STORED) as archive:
                    if job not in JOBS:return self.respond(404,{'error':'Project was deleted'})
                    for clip in job['clips']:
                        archive.write(self.data/job['id']/clip['name'],clip['name'])
                        if clip.get('srt'): archive.write(self.data/job['id']/clip['srt'],clip['srt'])
                    transcript=self.data/job['id']/'transcript.json'
                    if transcript.is_file():archive.write(transcript,'transcript.json')
                size=bundle.tell(); bundle.seek(0)
                self.send_response(200); self.send_header('Content-Type','application/zip'); self.send_header('Content-Disposition','attachment; filename="clipflow-clips.zip"'); self.send_header('Content-Length',str(size)); self.end_headers()
                shutil.copyfileobj(bundle,self.wfile)
            return
        if path.startswith('/media/'):
            parts=path.removeprefix('/media/').split('/')
            if PUBLIC_ACCESS and (len(parts)!=2 or not re.fullmatch(r'[a-f0-9]{32}',parts[0]) or not any(j['id']==parts[0] for j in self.visible_jobs())):return self.respond(404,{'error':'Not found'})
            file=(self.data/path.removeprefix('/media/')).resolve(); base=self.data.resolve()
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
                if size<1:raise ValueError('Choose a non-empty video file')
                if MAX_UPLOAD and size>MAX_UPLOAD:raise ValueError(f'This server is configured for uploads up to {MAX_UPLOAD//1024**2} MB')
                if size>shutil.disk_usage(self.data).free:return self.respond(507,{'error':'Not enough free disk space for this upload. Download and remove old files or use a server with more storage.'})
                name=parse_qs(urlparse(self.path).query).get('name',['video.mp4'])[0]
                suffix=Path(name).suffix.lower()
                if suffix not in ('.mp4','.mov','.mkv','.webm','.m4v'):raise ValueError('Use MP4, MOV, MKV, WebM or M4V')
                folder=self.data/'uploads';folder.mkdir(exist_ok=True);token=uuid.uuid4().hex+suffix
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
            except OSError as e:return self.respond(507,{'error':'Upload could not be stored. Check available server disk space.'})
            except (ValueError,RuntimeError) as e:return self.respond(400,{'error':str(e)})
        match=re.fullmatch(r'/api/jobs/([a-f0-9]{32})/delete',urlparse(self.path).path)
        if match:
            try:
                length=int(self.headers.get('Content-Length','0'))
                if not 1<=length<=4096:raise ValueError('Invalid deletion request')
                body=json.loads(self.rfile.read(length))
                if not isinstance(body,dict) or body.get('confirm') is not True:raise ValueError('Explicit deletion confirmation is required')
                with LOCK:
                    job=next((j for j in self.visible_jobs() if j['id']==match.group(1)),None)
                    if not job:return self.respond(404,{'error':'Project not found'})
                    code,result=storage.delete(self.data,self.visible_jobs(),job,body.get('action'))
                    if code==200:
                        if result['action']=='project':JOBS.remove(job)
                        save()
                    return self.respond(code,result)
            except (ValueError,TypeError) as e:return self.respond(400,{'error':str(e)})
            except OSError:return self.respond(500,{'error':'Some files could not be deleted. Refresh storage and try again.'})
        if self.path!='/api/jobs': return self.respond(404,{'error':'Not found'})
        try:
            size=int(self.headers.get('Content-Length','0'))
            if size<1 or size>16*1024*1024: raise ValueError('Request too large')
            body=json.loads(self.rfile.read(size))
            if not isinstance(body,dict):raise ValueError('Expected a JSON object')
            url=str(body.get('url','')).strip(); parsed=urlparse(url)
            upload=body.get('upload','')
            if upload and (not isinstance(upload,str) or Path(upload).name!=upload or not (self.data/'uploads'/upload).is_file()): raise ValueError('Upload not found')
            if not upload and (parsed.scheme!='https' or parsed.hostname not in ('youtube.com','www.youtube.com','m.youtube.com','youtu.be')): raise ValueError('Enter a valid HTTPS YouTube video URL')
            seconds=float(body['minutes'])*60
            if not math.isfinite(seconds) or not 1<=seconds<=3600: raise ValueError('Clip length must be between 1 second and 60 minutes')
            if not body.get('rights'): raise ValueError('Confirm that you can use this video')
            browser=body.get('browser','')
            if browser not in ('','chrome','firefox','safari','edge','brave'): raise ValueError('Invalid browser selection')
            if browser and (PUBLIC_ACCESS or BIND!='127.0.0.1'): raise ValueError('Browser login is only available in local mode')
            profile=str(body.get('profile','')).strip()
            if profile and (len(profile)>60 or not all(c.isalnum() or c in ' _-' for c in profile)): raise ValueError('Enter a profile folder such as Default or Profile 1')
            cookies=normalize_cookies(body.get('cookies',''))
            layout=body.get('layout','original')
            if layout not in ('original','portrait','square','portrait-fit'): raise ValueError('Invalid output layout')
            start=float(body.get('start',0)); end=float(body.get('end') or 0); max_clips=int(body.get('max_clips',0))
            if not math.isfinite(start) or not math.isfinite(end) or start<0 or end<0 or (end and end<=start): raise ValueError('Enter valid source start and end seconds')
            if not 0<=max_clips<=500: raise ValueError('Choose All clips (0), or a clip limit between 1 and 500')
            selection=body.get('selection','even')
            if selection not in ('even','highlights'):raise ValueError('Choose AI Highlights or Even Cuts')
            job=dict(owner=self.owner,upload=upload,selection=selection,model='tiny' if CLOUD_LITE else 'base',language='',caption_style='bold',captions=False,tracking=False,topic='',layout=layout,start=start,end=end,max_clips=max_clips,profile=profile,browser=browser,id=uuid.uuid4().hex,url=url,seconds=seconds,title=str(body.get('title','Uploaded video'))[:200] if upload else 'New YouTube video',status='queued',clips=[],total=0)
            with LOCK:
                if upload and not (self.data/'uploads'/upload).is_file():raise ValueError('This original was deleted. Choose the file and upload it again.')
                JOBS.insert(0,job);save()
            threading.Thread(target=process,args=(job,cookies),daemon=True).start(); return self.respond(201,public_job(job))
        except (ValueError,KeyError,TypeError,OverflowError) as e: return self.respond(400,{'error':str(e)})
if __name__=='__main__':
    server=ThreadingHTTPServer((BIND,PORT),Handler)
    print('Clipflow: '+PUBLIC_ORIGIN,flush=True)
    server.serve_forever()
