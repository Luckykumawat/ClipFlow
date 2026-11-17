"""One-time open-source models setup; no tokens or paid APIs needed."""
from pathlib import Path
from urllib.request import urlopen
import ai_engine
from faster_whisper import WhisperModel
from fastembed import TextEmbedding

def download(url, target):
    if target.is_file(): return
    target.parent.mkdir(exist_ok=True, parents=True)
    temporary=target.with_suffix('.download')
    with urlopen(url, timeout=120) as response, temporary.open('wb') as output:
        while chunk:=response.read(1024*1024): output.write(chunk)
    temporary.replace(target)

print('Downloading face detection model and caption font…',flush=True)
download('https://huggingface.co/opencv/face_detection_yunet/resolve/main/face_detection_yunet_2023mar.onnx',ai_engine.MODELS/'face_detection_yunet_2023mar.onnx')
download('https://raw.githubusercontent.com/google/fonts/main/ofl/notosansdevanagari/NotoSansDevanagari%5Bwdth,wght%5D.ttf',ai_engine.ROOT/'assets'/'NotoSansDevanagari.ttf')
download('https://raw.githubusercontent.com/opencv/opencv_zoo/main/models/face_detection_yunet/LICENSE',ai_engine.MODELS/'YUNET-LICENSE')
print('Preparing Whisper base…',flush=True)
WhisperModel('tiny' if ai_engine.CLOUD_LITE else 'base',device='cpu',compute_type='int8',download_root=str(ai_engine.MODELS/'whisper'))
print('Preparing multilingual semantic model…',flush=True)
model=TextEmbedding(ai_engine.EMBED_MODEL,cache_dir=str(ai_engine.MODELS/'embeddings'),threads=1,**({'mmap':True} if ai_engine.CLOUD_LITE else {}))
next(model.embed(['A practical lesson']))
print('All local AI models ready.',flush=True)
