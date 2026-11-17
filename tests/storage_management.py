"""Storage accounting, deletion boundaries, shared sources and HTTP protection."""
import base64,http.client,json,os,sys,tempfile,threading,uuid
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
with tempfile.TemporaryDirectory() as td:
    os.environ['DATA_DIR']=td
    import server as app
    root=Path(td);(root/'uploads').mkdir()
    def project(upload='',status='ready',youtube=False):
        job={'id':uuid.uuid4().hex,'title':'Disposable storage regression','status':status,'clips':[{'name':'clip-001.mp4','duration':1,'start':0}],'upload':upload}
        folder=root/job['id'];folder.mkdir();(folder/'clip-001.mp4').write_bytes(b'clip'*100);(folder/'clip-001.srt').write_text('captions');(folder/'transcript.json').write_text('{}')
        if youtube:(folder/'source.mp4').write_bytes(b'original'*1000)
        app.JOBS.append(job);app.save();return job
    def upload(name):(root/'uploads'/name).write_bytes(b'original'*1000)
    app.JOBS=[];upload('one.mp4');first=project('one.mp4')
    http=app.ThreadingHTTPServer(('127.0.0.1',0),app.Handler);threading.Thread(target=http.serve_forever,daemon=True).start()
    auth='Basic '+base64.b64encode((app.APP_USER+':'+app.APP_PASSWORD).encode()).decode()
    def request(path,body=None,origin=None,authenticated=True):
        conn=httpclient.HTTPConnection('127.0.0.1',http.server_address[1],timeout=10)
        headers={'Content-Type':'application/json'}
        if authenticated:headers['Authorization']=auth
        if origin:headers['Origin']=origin
        conn.request('POST' if body is not None else 'GET',path,json.dumps(body).encode() if body is not None else None,headers)
        r=conn.getresponse();code=r.status;raw=r.read();conn.close();return code,json.loads(raw) if raw else None
    # Alias avoids shadowing the imported module with the server instance.
    import http.client as httpclient
    def delete(job,action,confirm=True):return request('/api/jobs/'+job['id']+'/delete',{'action':action,'confirm':confirm})
    try:
        code,before=request('/api/storage');assert code==200 and before['original_bytes']==8000 and before['clip_bytes']==400,before
        assert delete(first,'original',False)[0]==400
        assert request('/api/jobs/'+first['id']+'/delete',{'action':'project','confirm':True},origin='https://invalid.example')[0]==403
        code,result=delete(first,'original');assert code==200 and result['freed_bytes']==8000,result
        assert (root/first['id']/'clip-001.mp4').exists() and (root/first['id']/'clip-001.srt').exists()
        assert not (root/'uploads'/'one.mp4').exists()
        after=request('/api/storage')[1];assert after['original_bytes']==0 and after['clip_bytes']==before['clip_bytes'] and after['used_bytes']<before['used_bytes']
        assert delete(first,'original')[0]==200 # Idempotent; clips remain.
        assert delete(first,'project')[0]==200 and not (root/first['id']).exists()
        assert delete(first,'project')[0]==404
        upload('shared.mp4');one=project('shared.mp4');two=project('shared.mp4','cutting')
        assert delete(one,'original')[0]==409 and delete(two,'project')[0]==409
        assert delete(one,'project')[0]==200 and (root/'uploads'/'shared.mp4').exists()
        two['status']='ready';assert delete(two,'project')[0]==200 and not (root/'uploads'/'shared.mp4').exists()
        upload('shared-again.mp4');one=project('shared-again.mp4');two=project('shared-again.mp4')
        assert request('/api/storage')[1]['original_bytes']==8000
        assert delete(one,'original')[0]==200 and one['original_deleted'] and two['original_deleted']
        assert (root/one['id']/'clip-001.mp4').exists() and (root/two['id']/'clip-001.mp4').exists()
        youtube=project(youtube=True);assert delete(youtube,'original')[0]==200
        assert not (root/youtube['id']/'source.mp4').exists() and (root/youtube['id']/'clip-001.mp4').exists()
        assert delete(youtube,'project')[0]==200
        assert request('/api/jobs/../delete',{'action':'project','confirm':True})[0]==404
        saved=json.loads((root/'jobs.json').read_text());assert all(j['id']!=youtube['id'] for j in saved)
        if app.APP_PASSWORD:assert request('/api/storage',authenticated=False)[0]==401
        print('PASS: storage accounting, original-only/project deletion, shared-source safety, active-job rejection, confirmation/origin/auth checks and persisted metadata')
    finally:http.shutdown();http.server_close()
