"""Regression: >5-minute sources, >3 exports, uncapped mode and bounded ASR."""
import importlib.util,json,os,subprocess,sys,tempfile
from pathlib import Path
from types import SimpleNamespace
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
with tempfile.TemporaryDirectory() as td:
    os.environ['DATA_DIR']=td;os.environ['CLOUD_LITE']='1'
    import server as app
    import ai_engine as ai
    data=Path(td);uploads=data/'uploads';uploads.mkdir()
    source=uploads/'long.mp4'
    subprocess.run(['ffmpeg','-v','error','-y','-f','lavfi','-i','color=c=blue:s=160x90:r=1','-f','lavfi','-i','anullsrc=r=16000:cl=mono','-t','301','-c:v','libx264','-threads','1','-pix_fmt','yuv420p','-c:a','aac',str(source)],check=True)
    job=dict(id='long-test',upload='long.mp4',seconds=60,max_clips=0,selection='even',captions=False,tracking=False,layout='original',clips=[],status='queued')
    app.JOBS=[job];app.process(job)
    assert job['status']=='ready',job
    assert len(job['clips'])==6,job
    assert job['clips'][-1]['start']==300 and abs(job['clips'][-1]['duration']-1)<.1
    for clip in job['clips']:
        duration=float(subprocess.check_output(['ffprobe','-v','error','-show_entries','format=duration','-of','default=noprint_wrappers=1:nokey=1',str(data/job['id']/clip['name'])]))
        assert abs(duration-clip['duration'])<.15,(clip,duration)
    class Model:
        lengths=[]
        def transcribe(self,audio,**kwargs):
            self.lengths.append(len(audio))
            word=SimpleNamespace(start=0,end=.5,word='speech')
            segment=SimpleNamespace(start=0,end=.5,text='speech',words=[word],no_speech_prob=0)
            return iter([segment]),SimpleNamespace(language='en')
    model=Model();ai.ASR['tiny']=model
    transcript=ai._transcribe_local(source,data,'tiny','en')
    assert len(model.lengths)==6 and max(model.lengths)<=60*16000,model.lengths
    assert [s['start'] for s in transcript['segments']]==[0,60,120,180,240,300]
    assert transcript['segments'][-1]['words'][0]['start']==300
    print('PASS: 301-second source -> all 6 playable clips; final remainder; ASR bounded to 60 seconds with global timestamps')
