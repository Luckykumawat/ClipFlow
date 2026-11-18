"""Pair this Mac with a ClipFlow Render browser workspace."""

import argparse
import html
import json
import os
import re
import secrets
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote

RENDER = 'https://clipflow-mh5t.onrender.com'


def request(token, path, lease=None, source=None, title=None):
    headers=[f'Authorization: Bearer {token}']
    if lease:
        headers.append(f'X-ClipFlow-Lease: {lease}')
    if title:
        headers.append(f'X-ClipFlow-Title: {quote(title[:200], safe="")}')
    config='\n'.join('header = '+json.dumps(header) for header in headers)+'\n'
    command=['curl','--silent','--show-error','--proto','=https','--connect-timeout','15',
             '--max-time','240' if source else '25','--config','-','--request','POST',
             '--write-out','\n%{http_code}',RENDER+path]
    if source:
        command.extend(['--data-binary','@'+str(source)])
    result=subprocess.run(command,input=config.encode(),capture_output=True,check=True)
    raw,status=result.stdout.rsplit(b'\n',1)
    return int(status),json.loads(raw)


def job_loop(token, shared=False):
    paired=False
    while True:
        try:
            status,result=request(token,'/api/mac/next')
            if status==403:
                if paired:
                    print('Workspace pairing expired. Reopen the local pairing page.',flush=True)
                paired=False
            elif status==200:
                if not paired:
                    print('Shared Render downloader connected.' if shared else 'Render workspace paired.',flush=True)
                paired=True
                if result['job']:
                    transfer(token,result['job'],shared)
        except (OSError,ValueError,subprocess.SubprocessError) as error:
            print(f'Could not contact Render ({type(error).__name__}). Retrying.',flush=True)
        time.sleep(4)


def heartbeat(token):
    while True:
        try:
            request(token,'/api/mac/heartbeat')
        except (OSError,ValueError,subprocess.SubprocessError):
            pass
        time.sleep(8)


def transfer(token, job, shared=False):
    video_id=job['video_id']
    if not re.fullmatch(r'[A-Za-z0-9_-]{11}',video_id):
        return
    endpoint='/api/mac/jobs/'+job['id']
    lease=job['lease']
    print(f'Downloading YouTube video {video_id} on this Mac.',flush=True)
    try:
        with tempfile.TemporaryDirectory(prefix='clipflow-mac-') as scratch:
            folder=Path(scratch)
            limits=['--match-filter','duration <= 1800 & !is_live','--max-filesize','256M'] if shared else []
            run=subprocess.run([
                sys.executable,'-m','yt_dlp','--ignore-config','--no-update','--no-playlist',
                '--socket-timeout','20','--retries','2','--js-runtimes','node',
                '--print','after_move:filepath','--print','before_dl:title',
                '-f','bv*[height<=720][vcodec^=avc1]+ba[ext=m4a]/b[height<=720][ext=mp4]/bv*[height<=720]+ba/b[height<=720]',
                '--merge-output-format','mp4','--remux-video','mp4',*limits,
                '-o',str(folder/'source.%(ext)s'),'--','https://www.youtube.com/watch?v='+video_id
            ],capture_output=True,text=True,timeout=600)
            if run.returncode:
                raise RuntimeError('yt-dlp failed: '+run.stderr[-900:])
            source=folder/'source.mp4'
            if not source.is_file():
                raise RuntimeError('The downloader produced no MP4')
            if shared and source.stat().st_size>256*1024**2:
                raise RuntimeError('Public downloads are limited to 256 MB')
            title=run.stdout.splitlines()[0] if run.stdout.splitlines() else ''
            print(f'Sending {source.stat().st_size/1024/1024:.1f} MB to Render.',flush=True)
            status,result=request(token,endpoint+'/source',lease,source,title)
            if status!=202:
                raise RuntimeError(result.get('error','Render declined the transfer'))
            print('Source received. Render is generating Even Cuts.',flush=True)
    except (OSError,ValueError,RuntimeError,subprocess.SubprocessError) as error:
        print(str(error) if isinstance(error,RuntimeError) else type(error).__name__,flush=True)
        request(token,endpoint+'/failed',lease)


def read_or_create_token(file):
    if not file.exists():
        fd=os.open(file,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        with os.fdopen(fd,'w') as output:
            output.write(secrets.token_hex(32))
    token=file.read_text().strip()
    if not re.fullmatch(r'[a-f0-9]{64}',token):
        raise ValueError('Invalid local pairing token')
    return token


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state',type=Path,default=Path(__file__).resolve().parent/'data'/'render-mac.key')
    parser.add_argument('--port',type=int,default=18780)
    parser.add_argument('--shared',action='store_true',help='Serve the public Render download queue')
    args=parser.parse_args()
    args.state.parent.mkdir(parents=True,exist_ok=True)
    if args.shared and not args.state.is_file():
        parser.error('Use --state with the authorized shared worker key')
    token=read_or_create_token(args.state)
    if args.shared:
        threading.Thread(target=heartbeat,args=(token,),daemon=True).start()
        job_loop(token,shared=True)
        return
    link=html.escape(RENDER+'/#mac='+token,quote=True)

    class PairPage(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path!='/' or self.headers.get('Host') not in (f'127.0.0.1:{args.port}',f'localhost:{args.port}'):
                self.send_error(404)
                return
            page=('<!doctype html><meta charset="utf-8"><meta name="referrer" content="no-referrer">'
                  '<meta name="viewport" content="width=device-width,initial-scale=1">'
                  '<title>Connect ClipFlow to this Mac</title><main style="font:18px system-ui;max-width:640px;margin:12vh auto;padding:24px">'
                  '<h1>Connect this Mac to ClipFlow</h1><p>Open Render from this button to pair your browser workspace. '
                  'Keep the Mac helper running when you create clips from a YouTube link.</p>'
                  f'<p><a href="{link}" rel="noreferrer">Connect Mac and open ClipFlow</a></p></main>').encode()
            self.send_response(200)
            self.send_header('Content-Type','text/html; charset=utf-8')
            self.send_header('Content-Length',str(len(page)))
            self.send_header('Cache-Control','no-store')
            self.end_headers()
            self.wfile.write(page)

    with ThreadingHTTPServer(('127.0.0.1',args.port),PairPage) as server:
        threading.Thread(target=job_loop,args=(token,),daemon=True).start()
        print(f'Mac helper running. Open http://127.0.0.1:{args.port}/ to pair.',flush=True)
        server.serve_forever()


if __name__=='__main__':
    main()
