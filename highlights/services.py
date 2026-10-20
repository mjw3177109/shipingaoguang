"""CPU baseline: audio-energy proposals and deterministic FFmpeg edit list.
No goal/assist/player classification is performed in V0.1.
"""
import math
import subprocess
import sys
import tempfile
from array import array
from pathlib import Path

from django.core.files import File

from .models import Candidate
from .export_plan import validate_plan


class MediaError(RuntimeError):
    pass


def probe_duration(path):
    proc = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        capture_output=True, text=True, timeout=90, check=False,
    )
    if proc.returncode:
        raise MediaError("FFprobe 无法读取该视频，请检查格式或文件完整性。")
    try:
        duration = float(proc.stdout.strip())
    except ValueError as exc:
        raise MediaError("视频时长无效。") from exc
    if not (math.isfinite(duration) and 1 <= duration <= 6 * 3600):
        raise MediaError("当前支持 1 秒至 6 小时的视频。")
    return duration


def audio_peaks(path, duration, max_candidates=24):
    """Return (start, end, loudness) candidates from sound spikes, NOT baskets."""
    command = [
        "ffmpeg", "-nostdin", "-v", "error", "-i", str(path),
        "-map", "0:a:0?", "-vn", "-ac", "1", "-ar", "8000",
        "-f", "s16le", "pipe:1",
    ]
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    samples = []
    try:
        chunk_size = 8000  # half-second at 8kHz mono, signed 16-bit
        while True:
            raw = process.stdout.read(chunk_size)
            if not raw:
                break
            amplitudes = array("h")
            amplitudes.frombytes(raw[: len(raw) - len(raw) % 2])
            if sys.byteorder != "little":
                amplitudes.byteswap()
            if amplitudes:
                rms = math.sqrt(sum(x * x for x in amplitudes) / len(amplitudes)) / 32768
                samples.append(rms)
        process.wait(timeout=30)
    except Exception:
        process.kill()
        process.wait()
        raise
    if not samples:
        return []
    ordered = sorted(samples)
    median = ordered[len(ordered) // 2]
    p85 = ordered[int((len(ordered) - 1) * .85)]
    threshold = max(.02, median * 2.0, p85 * .8)
    peaks = []
    for i, level in enumerate(samples):
        if level < threshold:
            continue
        before = samples[i - 1] if i else -1
        after = samples[i + 1] if i + 1 < len(samples) else -1
        if level >= before and level >= after:
            peaks.append((i * .5, level))
    peaks.sort(key=lambda p: p[1], reverse=True)
    picked = []
    for t, level in peaks:
        if all(abs(t - previous) >= 14 for previous, _ in picked):
            picked.append((t, level))
        if len(picked) >= max_candidates:
            break
    result = []
    for t, level in sorted(picked):
        start = max(0, t - 5)
        end = min(duration, t + 8)
        if end - start >= 1:
            result.append((round(start, 2), round(end, 2), round(level, 4)))
    return result


def analyze_game(game):
    duration = probe_duration(game.video.path)
    proposals = audio_peaks(game.video.path, duration)
    Candidate.objects.filter(game=game, source="audio_peak", review="pending").delete()
    Candidate.objects.bulk_create([
        Candidate(game=game, start_seconds=start, end_seconds=end,
                  source="audio_peak", energy_score=energy)
        for start, end, energy in proposals
    ])
    game.duration_seconds = duration
    game.state = "ready"
    game.error = ""
    game.save(update_fields=["duration_seconds", "state", "error"])
    return len(proposals)


def _run_ffmpeg(args):
    proc = subprocess.run(["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error",
                           "-y", *args], capture_output=True, text=True, timeout=3600)
    if proc.returncode:
        raise MediaError("FFmpeg 渲染失败：" + proc.stderr[-650:])


def render_export(job):
    if job.plan:
        # Clip approval/time/source must match the frozen human review snapshot.
        clips = validate_plan(job)
    else:
        # Compatibility for exports queued BEFORE upgrading to V1.0.
        qs = Candidate.objects.filter(game=job.game, review="approved")
        if job.player_label:
            qs = qs.filter(player_label=job.player_label)
        clips = list(qs.order_by("start_seconds", "pk"))
        if not clips:
            raise MediaError("没有对应的已通过片段。")
        if len(clips) > 150:
            raise MediaError("一次最多渲染 150 个片段。")
    if job.clips_total != len(clips):
        job.clips_total = len(clips)
        job.save(update_fields=["clips_total"])
    with tempfile.TemporaryDirectory(prefix="basketcut-") as folder:
        root = Path(folder)
        parts = []
        for index, clip in enumerate(clips):
            if not (0 <= clip.start_seconds < clip.end_seconds <= job.game.duration_seconds + .1):
                raise MediaError(f"片段 {clip.pk} 的时间范围不合法。")
            file = root / f"part_{index:04d}.mp4"
            # Uniform 720p 30fps, no sound in MVP; eliminates heterogeneous audio tracks.
            _run_ffmpeg([
                "-ss", f"{clip.start_seconds:.3f}", "-i", job.game.video.path,
                "-t", f"{clip.end_seconds - clip.start_seconds:.3f}",
                "-vf", "scale=1280:720:force_original_aspect_ratio=decrease,"
                       "pad=1280:720:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=30",
                "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "24",
                "-pix_fmt", "yuv420p", str(file),
            ])
            parts.append(file)
            job.clips_done = index + 1
            job.save(update_fields=["clips_done"])
        concat = root / "concat.txt"
        concat.write_text("".join(f"file '{item.name}'\n" for item in parts), encoding="utf-8")
        result = root / "output.mp4"
        _run_ffmpeg([
            "-f", "concat", "-safe", "0", "-i", str(concat), "-c", "copy",
            "-movflags", "+faststart", str(result),
        ])
        with result.open("rb") as stream:
            job.video.save("highlight.mp4", File(stream), save=False)
        job.state = "done"
        job.error = ""
        job.save(update_fields=["video", "state", "error"])
