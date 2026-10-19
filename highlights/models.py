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

    game = models.ForeignKey(Game, on_delete=models.CASCADE, related_name="exports")
    player_label = models.CharField(max_length=100, blank=True)
    state = models.CharField(max_length=16, choices=State.choices, default=State.QUEUED)
    video = models.FileField(upload_to=export_path, blank=True)
    error = models.TextField(blank=True)
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
