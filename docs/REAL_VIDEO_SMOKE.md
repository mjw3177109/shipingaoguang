# 免费 CPU + 真实篮球视频验收（非进球准确率测试）

## 视频及授权
- Source: [Basketpass.webm](https://commons.wikimedia.org/wiki/File:Basketpass.webm)
- Creator: Benoît Prieur; Wikimedia Commons derivative by Mariusz Swornóg
- Licensing: [CC0 1.0](https://creativecommons.org/publicdomain/zero/1.0/)
- Runtime: about 5.7s, 1280×720 VP8 WebM; actual basketball match/ball passing footage.
- Repo **does not commit the original media**. GitHub Actions downloads it on demand.
- This is **a passing clip**, not verified made basket. It tests video processing and honest zero-candidate fallback, **not shot recall**.

## How the automated real-footage test works
1. GitHub Actions downloads CC0 real basketball video, validates bytes and FFprobe metadata.
2. Django migration; test user and court created in temporary CI workspace.
3. POST upload via actual Django view and private storage.
4. POST `engine=cpu` queue, run actual CPU analysis worker (OpenCV HSV circular contour and movement heuristic).
5. If a candidate exists, simulated test reviewer approves one. If none exist, add explicit manually selected candidate, avoiding dishonest false claims.
6. POST export task and invoke the real FFmpeg worker.
7. Verify readable output MP4, nonzero bytes, duration; write report and JPEG preview.
8. GitHub Actions artifact includes `highlight.mp4`, `preview.jpg`, `report.json`.

The report explicitly distinguishes `cpu_candidate_count` and `manual_fallback`.
Successful workflow execution means **engineering integration works**, not that the model accurately detects shots.

## Run
In GitHub Actions choose **Real Basketball CPU Smoke (CC0)** → Run workflow.
If network download is blocked, GitHub will report failure rather than silently use synthetic footage.
Free Actions minutes are subject to your GitHub plan quotas; this is NOT guaranteed free unlimited cloud CPU.

To run locally:
```bash
python -m pip install -r requirements.txt -r requirements-cpu.txt
python manage.py migrate
python scripts/real_video_smoke.py --video Basketpass.webm --out real-video-results
```

For meaningful shot recall, annotate independent longer match clips from authorized data and evaluate with `highlights.vision.evaluate`. It would be incorrect to calculate shot detection accuracy from a 5.7-second passing example alone.
