# Run Clipflow from anywhere

## Free cloud profile on Render

[Deploy to Render](https://render.com/deploy?repo=https://github.com/Luckykumawat/ClipFlow)

The `render.yaml` Blueprint requests only a **free** web service and a provider-managed HTTPS subdomain. No purchased domain, Supabase project, Firebase project or Cloudflare account is needed. `Dockerfile.cloud` builds a constrained profile with cached AI models. A cloud account and authorized deployment are still required; having this repository alone does not make the application live.

1. Open the deployment link and sign in to Render.
2. Review the Blueprint: `plan: free`, one web service, no paid disks or databases. If free creation is unavailable, stop rather than choosing a paid plan.
3. Deploy and wait for the build and health check.
4. Open the exact `onrender.com` URL returned by Render.
5. Sign in with username **clipflow** and the generated `APP_PASSWORD` from the service's Environment settings. Keep this password private.

The app validates same-origin requests using `RENDER_EXTERNAL_URL`; its health check is public but projects and media require authentication. Browser-cookie YouTube login is disabled on network deployments. Uploading your own video avoids most hosted YouTube downloader blocks.

### Free profile limits

- 95 MB uploads, source recordings up to 5 minutes, at most 3 clips per job, at most 60 seconds per clip.
- Whisper Tiny rather than Base/Small, multilingual static Potion embeddings in memory-mapped mode, and single-threaded encoding.
- Portrait exports at 720×1280, square at 720×720. The desktop profile remains higher resolution.
- The free service has limited CPU and RAM. Expect slow generation and cold starts. Long recordings and production throughput need more resources.
- Render free services sleep when idle and may restart. **Uploads, transcripts, clip files and the project library are ephemeral. Download results promptly.** The repository and model image remain available independently of your Mac.
- The app is a password-protected single-owner workspace. It does not implement multiple user accounts or shared projects.
- No paid resources are requested by the Blueprint. Provider quotas, eligibility and billing policies can change. Do not enable paid upgrades to bypass a failed free deployment.

The GitHub workflow builds this image and exercises an actual speech → highlight → caption → tracked crop → ZIP workflow with a 512 MB container memory cap. Check the latest run; this is a validation step, not a deployment to Render. It uses one CPU for test turnaround, so it does not predict free-server processing speed.

Render documentation: https://render.com/docs/free and https://render.com/docs/blueprint-spec.

## Full server profile

For a VPS/container host with at least 2 CPU cores, 4 GB RAM and adequate disk, use `Dockerfile` and `compose.yaml`. Models can be initialized with `docker compose exec app python setup_ai.py`. Copy `.env.example` to `.env`; set `DOMAIN`, `APP_USER`, and a unique `APP_PASSWORD` of at least 20 characters. Point your domain to that server, then run `docker compose up -d --build`. Caddy handles HTTPS. This option needs your own server and domain and is not provisioned by the free Blueprint.

Persistent videos and models use Docker volumes. Keep the app's raw port private behind the HTTPS proxy. Do not commit `.env`, cookies, account credentials, user media or downloaded AI models.

## Why the original app cannot run on static hosting

GitHub Pages, Firebase Hosting and Cloudflare Pages serve frontend files; the existing Python/FFmpeg/Whisper processing backend needs compute. Supabase Edge Functions and Cloudflare Workers have short CPU/memory limits that do not fit this native backend. A static frontend with a stopped local backend would not satisfy use while the Mac is off. This package runs the frontend and processing together on the chosen cloud container.
