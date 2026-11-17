"""Verify an upload above the old 95 MiB cap without buffering the file."""
import base64,http.client,json,os,struct,subprocess,sys,tempfile,threading
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
with tempfile.TemporaryDirectory() as td:
    os.environ['DATA_DIR']=td;os.environ['MAX_UPLOAD_BYTES']='0'
    import server as app
    source=Path(td)/'large.mp4'
    subprocess.run(['ffmpeg','-v','error','-y','-f','lavfi','-i','color=c=blue:s=160x90:r=25','-t','2','-c:v','libx264','-threads','1','-pix_fmt','yuv420p',str(source)],check=True)
    size=96*1024**2; padding=size-source.stat().st_size
    with source.open('r+b') as f:
        f.seek(0,2);f.write(struct.pack('>I4s',padding,b'free'));f.seek(size-1);f.write(b'\0')
    assert source.stat().st_size==size
    server=app.ThreadingHTTPServer(('127.0.0.1',0),app.Handler)
    threading.Thread(target=server.serve_forever,daemon=True).start()
    headers={'Content-Length':str(size)}
    if app.APP_PASSWORD:headers['Authorization']='Basic '+base64.b64encode((app.APP_USER+':'+app.APP_PASSWORD).encode()).decode()
    connection=http.client.HTTPConnection('127.0.0.1',server.server_address[1],timeout=60)
    try:
        with source.open('rb') as body:connection.request('POST','/api/upload?name=96MiB-test.mp4',body,headers)
        response=connection.getresponse();data=json.loads(response.read());assert response.status==201,data
        assert (Path(td)/'uploads'/data['upload']).stat().st_size==size
        job=dict(id='large-upload',upload=data['upload'],seconds=1,max_clips=0,selection='even',captions=False,tracking=False,layout='original',clips=[],status='queued')
        app.JOBS=[job];app.process(job);assert job['status']=='ready' and len(job['clips'])==2,job
        print('PASS: streamed 96 MiB valid MP4 accepted; all clips generated')
        connection.close();connection=http.client.HTTPConnection('127.0.0.1',server.server_address[1],timeout=10)
        app.MAX_UPLOAD=1;headers['Content-Length']='2'
        connection.request('POST','/api/upload?name=over-limit.mp4',b'12',headers)
        response=connection.getresponse();response.read();assert response.status==400
        print('PASS: optional administrator limit still enforced')
    finally:connection.close();server.shutdown();server.server_close()
