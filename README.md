# Clipflow Studio — open-source AI video clipping

[Deploy the free cloud profile](https://render.com/deploy?repo=https://github.com/Luckykumawat/ClipFlow) · [Deployment guide](DEPLOY.md)

The desktop profile runs on your computer. The cloud profile runs independently on a server, with smaller models, uploads without an app-imposed size cap, uncapped source duration and all-clip mode, 720p exports and temporary files. See the deployment guide for free-service limitations. A deployment must complete before there is a live URL.

For desktop use, install Python 3.12 and FFmpeg/ffprobe, run `Setup AI.command`, then open `Start.command` and visit http://127.0.0.1:8765/. Setup needs internet for packages and models. Models, user media and environments are intentionally excluded from this GitHub repository.

## Use the studio

1. Paste a YouTube video URL or choose **Upload video** (subject to available server disk space).
2. Choose **AI highlights** or evenly spaced cuts. Optionally describe a topic to prioritize.
3. Set target clip length, maximum clips, and original/portrait/square layout.
4. Enable automatic captions and choose a caption style and speech language. Hindi and automatic language detection are available.
5. Enable **Speaker-aware framing** for portrait or square crop.
6. Generate clips and leave the app running. Download MP4, SRT, transcript JSON, or the complete ZIP from the library.

Videos, transcripts and outputs remain on this computer. YouTube downloads contact YouTube; model downloads contact Hugging Face/GitHub. Transcription and embedding inference run locally without a paid API or API key. There are no subscription credits or added watermarks. CPU, storage and electricity are still needed.

## Implemented AI

- **Whisper transcription:** `faster-whisper`, CPU int8, word timestamps and voice activity detection. Base is installed. Tiny and Small download on first use. Selected language is passed to transcription; Auto detects it. Review the transcript for errors, particularly Hindi, mixed speech, noise and music.
- **Semantic highlights:** local multilingual MiniLM embeddings rank overlapping speech windows against your topic or generic prompts about useful insights, stories and advice. Windows follow word timestamps and prefer sentence endings where possible. Nonoverlapping clips are returned in relevance order. A score is cosine similarity, not virality probability. This is semantic retrieval, not an LLM editorial review or a guarantee of engaging moments. The app may return fewer clips when speech or nonoverlapping windows are limited.
- **Captions:** timed short phrases burned into MP4, with white, yellow or boxed styles. Separate SRT and full timestamped transcript JSON downloads are included. Caption font includes Devanagari and Latin. Some other scripts may need an additional font. Captions are phrase based, not karaoke animation. There is no caption text editor yet.
- **Visual speaker tracking (beta):** YuNet detects faces; lower-face motion helps choose between visible faces. Smooth horizontal reframing follows the selected face. With no detected face, the crop stays centered or holds its last position. This is not audio-visual active-speaker inference, diarization or voice identification. Side profiles, occlusions, camera cuts, multiple moving faces and people moving without speaking can cause incorrect framing. Single front-facing speakers work best.

Other features: source trimming, maximum clip limit, persistent project library, clip progress, original/portrait-fit/portrait-crop/square exports, search and status filters, downloadable ZIP, browser login options for restricted YouTube videos, MP4 byte ranges for seeking, and a publishing cadence calculator.

## Verification

Verified with actual speech audio from Whisper's public `jfk.flac` test fixture and an artificially moving OpenCV face image:

- Local Whisper recognized the speech and produced word timestamps.
- Local multilingual embedding inference ranked highlight windows.
- SRT times were clipped and rebased to the clip; burned captions were visually inspected.
- Moving-face tracking changed the crop position and tolerated a blank frame.
- Export contained playable 1080×1920 H.264 video and AAC audio with expected duration.
- Browser workflow: upload → create AI highlight with captions/tracking → Ready → read transcript.
- HTTP checks: ZIP integrity and expected MP4/SRT/transcript members, byte-range playback responses, and invalid AI-option/range rejection.
- Existing evenly spaced segment/remainder and trimmed portrait-fit regression checks passed.

The library project named `source.mp4` is a synthetic verification fixture combining test speech with a moving still image. It is not a real recorded speaker. Natural multi-speaker conversation accuracy, Hindi recognition quality, real YouTube downloads, Docker builds and automatic social publishing were not validated in this update.

## Limits and operation

This is a local AI studio, not full Opus feature parity. No automatic social posting, account OAuth, scheduler worker, AI B-roll, voice cloning, generative video, or cloud collaboration is implemented. The publishing calculator does not upload or schedule posts.

Whisper processes the source audio before selecting clips; long sources may take time. Analysis and rendering run one project at a time. Close other heavy apps if memory becomes constrained. Files are stored under `data/`; downloaded models under `models/`; dependencies in `.venv/`. Keep the app running until jobs finish. Interrupted jobs are marked failed on restart.

YouTube can require login, age verification or bot checks. Select the signed-in browser/profile only when needed; this allows yt-dlp to read its local cookies for the download. Login does not guarantee a successful download. Local uploads avoid this download dependency.

Keep the server bound to loopback for local use. See `DEPLOY.md` for the existing authenticated deployment configuration. Docker now includes the AI code and dependencies, and mounts a persistent models volume. Run `docker compose exec app python setup_ai.py` once after starting it to prepare face/transcription/embedding models; this path has not been Docker-tested here.

## Model and font sources

- Faster Whisper: https://github.com/SYSTRAN/faster-whisper (MIT)
- Whisper weights: https://huggingface.co/Systran/faster-whisper-base
- Multilingual MiniLM: https://huggingface.co/sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2 (Apache 2.0)
- YuNet: https://github.com/opencv/opencv_zoo/tree/main/models/face_detection_yunet (see model license)
- Potion English (free cloud profile): https://huggingface.co/minishlab/potion-base-8M (MIT)
- Noto Sans Devanagari: https://github.com/google/fonts/tree/main/ofl/notosansdevanagari (SIL OFL; license in `assets/OFL.txt`)
