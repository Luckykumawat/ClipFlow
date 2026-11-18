"""Exercise an authenticated cloud container with its 512 MB memory limit."""
import base64, importlib.util, io, json, os, subprocess, tempfile, time, urllib.request, zipfile
from pathlib import Path
BASE=os.environ.get('BASE_URL','http://127.0.0.1:8765')
AUTH='Basic '+base64.b64encode((os.environ.get('APP_USER','clipflow')+':'+os.environ['APP_PASSWORD']).encode()).decode()
def call(path,data=None,headers=None,auth=True):
    values={'Authorization':AUTH} if auth else {}
    values.update(headers or {})
    with urllib.request.urlopen(urllib.request.Request(BASE+path,data=data,headers=values),timeout=120) as r:return r.status,dict(r.headers),r.read()
assert call('/healthz',auth=False)[0]==200
health=json.loads(call('/api/health')[2]);assert health['cloud_lite'] and not health['ai'],health
with tempfile.TemporaryDirectory() as td:
    root=Path(td)
    subprocess.run(['ffmpeg','-v','error','-y','-f','lavfi','-i','testsrc2=size=640x360:rate=15','-f','lavfi','-i','sine=frequency=440:sample_rate=48000','-t','5','-c:v','libx264','-threads','1','-pix_fmt','yuv420p','-c:a','aac',str(root/'test.mp4')],check=True)
    upload=json.loads(call('/api/upload?name=cloud-test.mp4',(root/'test.mp4').read_bytes())[2])
    body=dict(url='',**upload,minutes=.05,max_clips=0,selection='highlights',layout='portrait-fit',rights=True)
    job=json.loads(call('/api/jobs',json.dumps(body).encode(),{'Content-Type':'application/json'})[2])
    deadline=time.monotonic()+600
    while time.monotonic()<deadline:
        current=next(j for j in json.loads(call('/api/jobs')[2]) if j['id']==job['id'])
        if current['status']=='failed':raise AssertionError(current)
        if current['status']=='ready':break
        time.sleep(2)
    else:raise AssertionError('Clipping job timed out')
    assert current['selection_used']=='even' and current['warnings'],current
    assert [(c['start'],c['duration']) for c in current['clips']]==[(0,3),(3,2)],current
    clip=current['clips'][0]
    status,headers,data=call('/media/'+job['id']+'/'+clip['name'],headers={'Range':'bytes=0-99'});assert status==206 and len(data)==100
    bundle=zipfile.ZipFile(io.BytesIO(call('/api/bundle/'+job['id'])[2]));assert set(bundle.namelist())=={'clip-001.mp4','clip-002.mp4'};assert bundle.testzip() is None
    (root/'clip.mp4').write_bytes(bundle.read('clip-001.mp4'))
    probe=json.loads(subprocess.check_output(['ffprobe','-v','error','-show_streams','-of','json',str(root/'clip.mp4')]))
    video=next(s for s in probe['streams'] if s['codec_type']=='video');assert(video['width'],video['height'])==(720,1280),video
    assert any(s['codec_type']=='audio' for s in probe['streams'])
print('PASS: cloud upload, AI-to-even fallback, full-frame 720p/audio export, ZIP, byte ranges')

# The deployed downloader needs both a JS runtime and the packaged solver.
subprocess.run(['deno','--version'],check=True)
assert importlib.util.find_spec('yt_dlp_ejs') is not None
