"""CPU baseline: audio-energy proposals and deterministic FFmpeg edit list.
No goal/assist/player classification is performed in V0.1.
"""
import math
import subprocess
import sys
import tempfile
import time
from array import array
from pathlib import Path

from django.core.files import File
from django.db import transaction
from django.utils import timezone

from .models import Candidate, Game
from .export_plan import validate_plan
from .worker_leases import WorkerLeaseLost, heartbeat_export, throttled_heartbeat


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


def has_audio_stream(path):
    """True if FFprobe finds an audio track. No-audio recordings are valid."""
    proc = subprocess.run([
        "ffprobe", "-v", "error", "-select_streams", "a:0",
        "-show_entries", "stream=index", "-of", "csv=p=0", str(path)
    ], capture_output=True, text=True, timeout=90, check=False)
    if proc.returncode != 0:
        raise MediaError("无法读取原始视频音轨信息。")
    return bool(proc.stdout.strip())


def audio_peaks(path, duration, max_candidates=24, heartbeat=None):
    """Return sound-spike proposals without buffering a full game's PCM in RAM."""
    command = [
        "ffmpeg", "-nostdin", "-v", "error", "-i", str(path),
        "-map", "0:a:0?", "-vn", "-ac", "1", "-ar", "8000",
        "-f", "s16le", "pipe:1",
    ]
    samples = []
    chunk_size = 8000  # half-second at 8kHz mono, signed 16-bit
    with tempfile.TemporaryFile(mode="w+b") as pcm:
        process = subprocess.Popen(command, stdout=pcm, stderr=subprocess.DEVNULL)
        try:
            while process.poll() is None:
                if heartbeat:
                    heartbeat()
                time.sleep(.25)
            if process.returncode != 0:
                return []
            pcm.seek(0)
            while True:
                if heartbeat:
                    heartbeat()
                raw = pcm.read(chunk_size)
                if not raw:
                    break
                amplitudes = array("h")
                amplitudes.frombytes(raw[: len(raw) - len(raw) % 2])
                if sys.byteorder != "little":
                    amplitudes.byteswap()
                if amplitudes:
                    rms = math.sqrt(sum(x * x for x in amplitudes) / len(amplitudes)) / 32768
                    samples.append(rms)
        except BaseException:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
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


def analyze_game(game, heartbeat=None):
    duration = probe_duration(game.video.path)
    proposals = audio_peaks(game.video.path, duration, heartbeat=heartbeat)
    if heartbeat:
        heartbeat()
    # The final database write is fenced by the lease token. An expired worker
    # may finish reading media but cannot replace newer candidates.
    if game.worker_token:
        with transaction.atomic():
            locked = Game.objects.select_for_update().get(pk=game.pk)
            if locked.state != Game.State.ANALYZING or locked.worker_token != game.worker_token:
                raise WorkerLeaseLost(f"Game {game.pk} analysis lease lost before commit.")
            Candidate.objects.filter(
                game=game, source="audio_peak", review="pending").delete()
            Candidate.objects.bulk_create([
                Candidate(game=game, start_seconds=start, end_seconds=end,
                          source="audio_peak", energy_score=energy)
                for start, end, energy in proposals
            ])
            changed = Game.objects.filter(
                pk=game.pk, state=Game.State.ANALYZING,
                worker_token=game.worker_token).update(
                    duration_seconds=duration, state=Game.State.READY, error="",
                    worker_token="", worker_heartbeat_at=None)
            if changed != 1:
                raise WorkerLeaseLost(f"Game {game.pk} analysis lease lost during commit.")
    else:
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


def _run_ffmpeg(args, *, heartbeat=None, timeout=3600):
    command = ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error",
               "-nostats", "-y", *args]
    started = time.monotonic()
    with tempfile.TemporaryFile(mode="w+b") as error_log:
        proc = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=error_log)
        try:
            while proc.poll() is None:
                if heartbeat:
                    heartbeat()
                if time.monotonic() - started > timeout:
                    proc.terminate()
                    try:
                        proc.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                        proc.wait()
                    raise MediaError(f"FFmpeg 超时（{timeout} 秒），任务已停止。")
                time.sleep(1)
            if proc.returncode:
                error_log.seek(0, 2)
                size = error_log.tell()
                error_log.seek(max(0, size - 4096))
                detail = error_log.read().decode("utf-8", errors="replace")[-650:]
                raise MediaError("FFmpeg 渲染失败：" + detail)
        except BaseException:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()
            raise


def _update_export_under_lease(job, **fields):
    """Update progress only while this exact worker still owns the running job."""
    if job.worker_token:
        fields["worker_heartbeat_at"] = timezone.now()
        changed = job.__class__.objects.filter(
            pk=job.pk, state=job.State.RUNNING, worker_token=job.worker_token
        ).update(**fields)
        if changed != 1:
            raise WorkerLeaseLost(f"Export {job.pk} lease lost; refusing stale progress.")
        for key, value in fields.items():
            setattr(job, key, value)
    else:
        for key, value in fields.items():
            setattr(job, key, value)
        job.save(update_fields=list(fields))


def render_export(job, heartbeat=None):
    if job.worker_token and heartbeat is None:
        heartbeat = throttled_heartbeat(
            lambda: heartbeat_export(job.pk, job.worker_token))
        heartbeat(force=True)
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
        _update_export_under_lease(job, clips_total=len(clips))
    audio_mode = job.audio_mode if job.plan else "silent"
    if audio_mode not in ("silent", "original"):
        raise MediaError("音频输出配置无效。")
    input_has_audio = has_audio_stream(job.game.video.path) if audio_mode == "original" else False
    if audio_mode == "original" and not input_has_audio:
        _update_export_under_lease(job, audio_fallback_used=True)
    with tempfile.TemporaryDirectory(prefix="basketcut-") as folder:
        root = Path(folder)
        parts = []
        for index, clip in enumerate(clips):
            if not (0 <= clip.start_seconds < clip.end_seconds <= job.game.duration_seconds + .1):
                raise MediaError(f"片段 {clip.pk} 的时间范围不合法。")
            file = root / f"part_{index:04d}.mp4"
            command = [
                "-ss", f"{clip.start_seconds:.3f}", "-i", job.game.video.path,
            ]
            if audio_mode == "original" and not input_has_audio:
                # Deterministically supply an AAC silent track, so concat uses
                # the same stream layout for every source clip.
                command += ["-f", "lavfi", "-i",
                            "anullsrc=channel_layout=stereo:sample_rate=48000"]
            command += [
                "-t", f"{clip.end_seconds - clip.start_seconds:.3f}",
                "-map", "0:v:0",
            ]
            if audio_mode == "original":
                command += ["-map", "0:a:0" if input_has_audio else "1:a:0"]
            command += [
                "-vf", "scale=1280:720:force_original_aspect_ratio=decrease,"
                       "pad=1280:720:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=30",
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "24",
                "-pix_fmt", "yuv420p",
            ]
            if audio_mode == "original":
                command += ["-c:a", "aac", "-ar", "48000", "-ac", "2",
                            "-b:a", "128k"]
            else:
                command += ["-an"]
            _run_ffmpeg([*command, str(file)], heartbeat=heartbeat)
            if audio_mode == "original" and not has_audio_stream(file):
                # Odd recordings sometimes advertise an audio stream but yield
                # no packets inside the selected timespan. Re-encode that part
                # using an explicit silent source before concatenating.
                silence_cmd = [
                    "-ss", f"{clip.start_seconds:.3f}", "-i", job.game.video.path,
                    "-f", "lavfi", "-i",
                    "anullsrc=channel_layout=stereo:sample_rate=48000",
                    "-t", f"{clip.end_seconds - clip.start_seconds:.3f}",
                    "-map", "0:v:0", "-map", "1:a:0",
                    "-vf", "scale=1280:720:force_original_aspect_ratio=decrease,"
                           "pad=1280:720:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=30",
                    "-c:v", "libx264", "-preset", "veryfast", "-crf", "24",
                    "-pix_fmt", "yuv420p", "-c:a", "aac", "-ar", "48000",
                    "-ac", "2", "-b:a", "128k", str(file),
                ]
                _run_ffmpeg(silence_cmd, heartbeat=heartbeat)
                _update_export_under_lease(job, audio_fallback_used=True)
                if not has_audio_stream(file):
                    raise MediaError("片段缺失音轨且静音回退失败。")
            parts.append(file)
            _update_export_under_lease(job, clips_done=index + 1)
        concat = root / "concat.txt"
        concat.write_text("".join(f"file '{item.name}'\n" for item in parts), encoding="utf-8")
        result = root / "output.mp4"
        _run_ffmpeg([
            "-f", "concat", "-safe", "0", "-i", str(concat), "-c", "copy",
            "-movflags", "+faststart", str(result),
        ], heartbeat=heartbeat)
        if audio_mode == "original" and not has_audio_stream(result):
            # Last-resort guard if the concat muxer dropped all audio streams.
            # This is SILENCE, explicitly flagged; never claim source retained.
            repaired = root / "output_with_silence.mp4"
            _run_ffmpeg([
                "-i", str(result), "-f", "lavfi", "-i",
                "anullsrc=channel_layout=stereo:sample_rate=48000",
                "-map", "0:v:0", "-map", "1:a:0",
                "-c:v", "copy", "-c:a", "aac", "-ar", "48000",
                "-ac", "2", "-b:a", "128k", "-shortest",
                "-movflags", "+faststart", str(repaired),
            ], heartbeat=heartbeat)
            result = repaired
            _update_export_under_lease(job, audio_fallback_used=True)
            if not has_audio_stream(result):
                raise MediaError("导出结果音轨回退失败。")
        with result.open("rb") as stream:
            job.video.save("highlight.mp4", File(stream), save=False)
        if job.worker_token:
            stored_name = job.video.name
            try:
                changed = job.__class__.objects.filter(
                    pk=job.pk, state=job.State.RUNNING, worker_token=job.worker_token
                ).update(video=stored_name, state=job.State.DONE, error="",
                         worker_token="", worker_heartbeat_at=None)
            except BaseException:
                job.video.storage.delete(stored_name)
                raise
            if changed != 1:
                job.video.storage.delete(stored_name)
                raise WorkerLeaseLost(
                    f"Export {job.pk} lease lost before final commit; stale output discarded.")
            job.refresh_from_db()
        else:
            # Direct service invocation remains supported for tests and one-off scripts.
            job.state = "done"
            job.error = ""
            job.save(update_fields=["video", "state", "error"])
