"""Public anonymous sessions: no login, separate files and owner-only deletion."""
import http.cookiejar,json,os,subprocess,sys,tempfile,threading,time,urllib.request,urllib.error,uuid
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
with tempfile.TemporaryDirectory() as td:
    os.environ.update(DATA_DIR=td,PUBLIC_ACCESS='1')
    import server as app
    app.JOBS=[]
    legacy={'id':uuid.uuid4().hex,'status':'ready','title':'Private legacy project','clips':[]}
    app.JOBS.append(legacy);folder=Path(td)/legacy['id'];folder.mkdir();(folder/'clip-001.mp4').write_bytes(b'private')
    server=app.ThreadingHTTPServer(('127.0.0.1',0),app.Handler);threading.Thread(target=server.serve_forever,daemon=True).start();base='http://127.0.0.1:'+str(server.server_address[1])
    def client():return urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
    a,b=client(),client()
    def request(client,path,body=None,headers=None):
        try:
            with client.open(urllib.request.Request(base+path,data=body,headers=headers or {}),timeout=30) as r:return r.status,dict(r.headers),r.read()
        except urllib.error.HTTPError as e:return e.code,dict(e.headers),e.read()
    def api(client,path,body=None):
        status,headers,raw=request(client,path,json.dumps(body).encode() if body is not None else None,{'Content-Type':'application/json'});return status,json.loads(raw)
    try:
        status,headers,_=request(a,'/');assert status==200 and 'WWW-Authenticate' not in headers
        assert 'HttpOnly' in headers['Set-Cookie'] and 'SameSite=Lax' in headers['Set-Cookie']
        assert api(a,'/api/jobs')[1]==[] and api(b,'/api/jobs')[1]==[]
        assert request(a,'/media/'+legacy['id']+'/clip-001.mp4')[0]==404
        source=Path(td)/'public-test.mp4'
        subprocess.run(['ffmpeg','-v','error','-y','-f','lavfi','-i','color=c=blue:s=160x90:r=25','-t','2','-c:v','libx264','-threads','1','-pix_fmt','yuv420p',str(source)],check=True)
        status,_,raw=request(a,'/api/upload?name=public-test.mp4',source.read_bytes());assert status==201,raw
        upload=json.loads(raw)
        body=dict(url='',**upload,minutes=1/60,max_clips=0,model='tiny',selection='even',captions=False,tracking=False,layout='original',rights=True)
        assert api(b,'/api/jobs',body)[0]==400
        status,job=api(a,'/api/jobs',body);assert status==201,job
        assert 'owner' not in job
        for _ in range(60):
            current=next(j for j in api(a,'/api/jobs')[1] if j['id']==job['id'])
            if current['status']=='failed':raise AssertionError(current)
            if current['status']=='ready':break
            time.sleep(.2)
        assert current['status']=='ready',current
        assert api(b,'/api/jobs')[1]==[]
        usage=api(b,'/api/storage')[1];assert usage['projects']=={} and usage['used_bytes']==0,usage
        media='/media/'+job['id']+'/clip-001.mp4';deletion='/api/jobs/'+job['id']+'/delete'
        assert request(b,media)[0]==404
        assert request(b,'/api/bundle/'+job['id'])[0]==404
        assert api(b,deletion,{'action':'project','confirm':True})[0]==404
        assert request(a,media,headers={'Range':'bytes=0-99'})[0]==206
        # A changed signature must never grant access to an existing workspace.
        cookie=headers['Set-Cookie'].split(';')[0];key,value=cookie.split('=',1);bad=value[:-1]+('0' if value[-1]!='0' else '1')
        assert request(client(),media,headers={'Cookie':key+'='+bad})[0]==404
        assert request(a,deletion,json.dumps({'action':'project','confirm':True}).encode(),{'Origin':'https://untrusted.example'})[0]==403
        assert api(a,deletion,{'action':'original','confirm':True})[0]==200
        assert request(a,media)[0]==200
        assert api(a,deletion,{'action':'project','confirm':True})[0]==200
        assert api(a,'/api/jobs')[1]==[]
        assert (folder/'clip-001.mp4').read_bytes()==b'private'
        assert api(client(),'/api/health')[1]['public_access'] is True
        print('PASS: password-free public access; cookie persistence/tamper checks; isolated jobs/uploads/storage/media/ZIP/deletion; private legacy files stay private')
    finally:server.shutdown();server.server_close()
