"""Exercise an authenticated cloud container with its 512 MB memory limit."""
import base64, io, json, os, subprocess, tempfile, time, urllib.request, zipfile
from pathlib import Path
BASE=os.environ.get('BASE_URL','http://127.0.0.1:8765')
AUTH='Basic '+base64.b64encode((os.environ.get('APP_USER','clipflow')+':'+os.environ['APP_PASSWORD']).encode()).decode()
def call(path,data=None,headers=None,auth=True):
    values={'Authorization':AUTH} if auth else {}
    values.update(headers or {})
    with urllib.request.urlopen(urllib.request.Request(BASE+path,data=data,headers=values),timeout=120) as r:return r.status,dict(r.headers),r.read()
assert call('/healthz',auth=False)[0]==200
health=json.loads(call('/api/health')[2]);assert health['ai'] and health['cloud_lite'] and health['face_model'],health
with tempfile.TemporaryDirectory() as td:
    root=Path(td)
    urllib.request.urlretrieve('https://raw.githubusercontent.com/openai/whisper/main/tests/jfk.flac',root/'voice.flac')
    urllib.request.urlretrieve('https://raw.githubusercontent.com/opencv/opencv/master/samples/data/lena.jpg',root/'face.jpg')
    subprocess.run(['ffmpeg','-v','error','-y','-loop','1','-i',str(root/'face.jpg'),'-i',str(root/'voice.flac'),'-t','11','-vf','scale=640:360','-r','15','-c:v','libx264','-threads','1','-pix_fmt','yuv420p','-c:a','aac','-shortest',str(root/'test.mp4')],check=True)
    upload=json.loads(call('/api/upload?name=cloud-test.mp4',(root/'test.mp4').read_bytes())[2])
    body=dict(url='',**upload,minutes=.1,max_clips=1,model='tiny',language='en',selection='highlights',captions=True,tracking=True,layout='portrait',rights=True,caption_style='yellow')
    job=json.loads(call('/api/jobs',json.dumps(body).encode(),{'Content-Type':'application/json'})[2])
    deadline=time.monotonic()+600
    while time.monotonic()<deadline:
        current=next(j for j in json.loads(call('/api/jobs')[2]) if j['id']==job['id'])
        if current['status']=='failed':raise AssertionError(current)
        if current['status']=='ready':break
        time.sleep(2)
    else:raise AssertionError('AI job timed out')
    clip=current['clips'][0];assert clip['srt'] and clip['relevance'] is not None,current
    status,headers,data=call('/media/'+job['id']+'/'+clip['name'],headers={'Range':'bytes=0-99'});assert status==206 and len(data)==100
    bundle=zipfile.ZipFile(io.BytesIO(call('/api/bundle/'+job['id'])[2]));assert {'clip-001.mp4','clip-001.srt','transcript.json'}<=set(bundle.namelist());assert bundle.testzip() is None
    (root/'clip.mp4').write_bytes(bundle.read('clip-001.mp4'))
    probe=json.loads(subprocess.check_output(['ffprobe','-v','error','-show_streams','-of','json',str(root/'clip.mp4')]))
    video=next(s for s in probe['streams'] if s['codec_type']=='video');assert(video['width'],video['height'])==(720,1280),video
    assert any(s['codec_type']=='audio' for s in probe['streams'])
print('PASS: cloud AI generation, captions, tracking, 720p/audio export, SRT/ZIP, byte ranges')
