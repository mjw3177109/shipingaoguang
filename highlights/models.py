import uuid
from pathlib import Path
from django.conf import settings
from django.db import models


def video_path(instance, filename):
    suffix = Path(filename).suffix.lower()
    return f"raw/{instance.venue_id}/{uuid.uuid4().hex}{suffix}"


def export_path(instance, filename):
    return f"exports/{instance.game.venue_id}/{uuid.uuid4().hex}.mp4"


class Venue(models.Model):
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="venues")
    name = models.CharField(max_length=120)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name


class Game(models.Model):
    class State(models.TextChoices):
        UPLOADED = "uploaded", "待分析"
        QUEUED = "queued", "分析排队中"
        ANALYZING = "analyzing", "分析中"
        READY = "ready", "待审核"
        FAILED = "failed", "失败"

    venue = models.ForeignKey(Venue, on_delete=models.CASCADE, related_name="games")
    title = models.CharField(max_length=180)
    video = models.FileField(upload_to=video_path)
    duration_seconds = models.FloatField(default=0)
    hoops = models.JSONField(default=list, blank=True)  # V0.2 calibrated hoop regions
    annotations_complete = models.BooleanField(default=False)
    ball_training_approved = models.BooleanField(default=False)
    ball_training_rights = models.CharField(max_length=240, blank=True)
    ball_sampling_state = models.CharField(max_length=16, default="idle", choices=[("idle","未采样"),("queued","等待CPU采样"),("running","CPU采样中"),("ready","待人工标注"),("failed","采样失败")])
    ball_sampling_error = models.CharField(max_length=600, blank=True)
    ball_sampling_round = models.PositiveIntegerField(default=0)
    worker_started_at = models.DateTimeField(null=True, blank=True)
    worker_heartbeat_at = models.DateTimeField(null=True, blank=True)
    worker_token = models.CharField(max_length=36, blank=True, default="")
    worker_recovery_count = models.PositiveSmallIntegerField(default=0)
    ball_sampling_started_at = models.DateTimeField(null=True, blank=True)
    ball_sampling_heartbeat_at = models.DateTimeField(null=True, blank=True)
    ball_sampling_worker_token = models.CharField(max_length=36, blank=True, default="")
    ball_sampling_recovery_count = models.PositiveSmallIntegerField(default=0)
    analysis_engine = models.CharField(max_length=16, default="audio", choices=[("audio", "声音峰值"), ("cpu", "CPU 球状物体候选")])
    state = models.CharField(max_length=16, choices=State.choices, default=State.UPLOADED)
    error = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.title


class Candidate(models.Model):
    class Review(models.TextChoices):
        PENDING = "pending", "待审"
        APPROVED = "approved", "通过"
        REJECTED = "rejected", "剔除"

    game = models.ForeignKey(Game, on_delete=models.CASCADE, related_name="candidates")
    start_seconds = models.FloatField()
    end_seconds = models.FloatField()
    source = models.CharField(max_length=32, default="manual")  # manual / audio_peak
    energy_score = models.FloatField(default=0)
    review = models.CharField(max_length=16, choices=Review.choices, default=Review.PENDING)
    player_label = models.CharField(max_length=100, blank=True)
    note = models.CharField(max_length=240, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["start_seconds", "id"]


class Export(models.Model):
    class State(models.TextChoices):
        QUEUED = "queued", "排队中"
        RUNNING = "running", "渲染中"
        DONE = "done", "已完成"
        FAILED = "failed", "失败"
        CANCELLED = "cancelled", "已取消"

    game = models.ForeignKey(Game, on_delete=models.CASCADE, related_name="exports")
    player_label = models.CharField(max_length=100, blank=True)
    audio_mode = models.CharField(max_length=16, default="silent",
                                  choices=[("silent","无声"),("original","保留原声")])
    state = models.CharField(max_length=16, choices=State.choices, default=State.QUEUED)
    video = models.FileField(upload_to=export_path, blank=True)
    error = models.TextField(blank=True)
    plan = models.JSONField(default=dict, blank=True)
    plan_sha256 = models.CharField(max_length=64, blank=True)
    retry_of = models.ForeignKey("self", null=True, blank=True,
                                 on_delete=models.SET_NULL, related_name="retry_attempts")
    attempt = models.PositiveSmallIntegerField(default=1)
    worker_started_at = models.DateTimeField(null=True, blank=True)
    worker_heartbeat_at = models.DateTimeField(null=True, blank=True)
    worker_token = models.CharField(max_length=36, blank=True, default="")
    worker_recovery_count = models.PositiveSmallIntegerField(default=0)
    clips_done = models.PositiveSmallIntegerField(default=0)
    clips_total = models.PositiveSmallIntegerField(default=0)
    audio_fallback_used = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.game.title} - {self.player_label or '全场'}"


class ShotAnnotation(models.Model):
    """Independent human annotation of attempted shots; not AI output or score verdict."""
    game = models.ForeignKey(Game, on_delete=models.CASCADE, related_name="shot_annotations")
    time_seconds = models.FloatField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["time_seconds", "id"]


class BallFrameAnnotation(models.Model):
    """Independent human frame-level ball bbox (or explicit ball-absent negative)."""
    game = models.ForeignKey(Game, on_delete=models.CASCADE, related_name="ball_frames")
    time_seconds = models.FloatField()
    ball_absent = models.BooleanField(default=False)
    hard_negative_reason = models.CharField(max_length=24, blank=True, default="")
    quality_verified = models.BooleanField(default=False)
    quality_verified_at = models.DateTimeField(null=True, blank=True)
    quality_verified_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="verified_ball_frames")

    x = models.FloatField(default=0)
    y = models.FloatField(default=0)
    w = models.FloatField(default=0)
    h = models.FloatField(default=0)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["time_seconds", "id"]
        constraints = [
            models.UniqueConstraint(fields=["game", "time_seconds"],
                                    name="unique_ball_frame_time"),
        ]


class BallFrameSuggestion(models.Model):
    """A CPU-recommended timestamp only. Not verified positive or negative."""
    game = models.ForeignKey(Game, on_delete=models.CASCADE, related_name="ball_suggestions")
    time_seconds = models.FloatField()
    reason = models.CharField(max_length=24)
    score = models.FloatField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["time_seconds", "id"]
        constraints = [models.UniqueConstraint(fields=["game","time_seconds"],
                          name="unique_suggested_ball_frame")]


class WorkerPulse(models.Model):
    """Single CPU worker liveness record, never containing personal media data."""
    key = models.CharField(max_length=32, unique=True, default="cpu-primary")
    last_seen_at = models.DateTimeField()
    started_at = models.DateTimeField()
    task_count = models.PositiveIntegerField(default=0)

    def __str__(self):
        return self.key


class ResumableUpload(models.Model):
    """Private, short-lived staging session; never a completed Game."""
    class State(models.TextChoices):
        RECEIVING = "receiving", "接收中"
        COMPLETE = "complete", "已完成"
        CANCELLED = "cancelled", "已取消"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                              related_name="basketcut_uploads")
    venue = models.ForeignKey(Venue, on_delete=models.CASCADE, related_name="resumable_uploads")
    title = models.CharField(max_length=180)
    filename = models.CharField(max_length=180)
    byte_size = models.PositiveBigIntegerField()
    expected_sha256 = models.CharField(max_length=64)
    received_bytes = models.PositiveBigIntegerField(default=0)
    state = models.CharField(max_length=16, choices=State.choices, default=State.RECEIVING)
    game = models.ForeignKey(Game, null=True, blank=True, on_delete=models.SET_NULL,
                             related_name="resumable_upload_records")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    expires_at = models.DateTimeField()

    class Meta:
        indexes = [models.Index(fields=["state", "expires_at"],
                                name="hl_upload_expiry_idx")]


class MediaCapacityLock(models.Model):
    """Single PG row lock serializes cross-tenant media admission and pending-byte updates."""
    id = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)

    def __str__(self):
        return "global-media-capacity-lock"
