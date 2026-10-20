from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("highlights", "0010_reviewed_export_snapshots")]

    operations = [
        migrations.AddField("game", "worker_started_at",
            models.DateTimeField(blank=True, null=True)),
        migrations.AddField("game", "worker_heartbeat_at",
            models.DateTimeField(blank=True, null=True)),
        migrations.AddField("game", "worker_token",
            models.CharField(blank=True, default="", max_length=36)),
        migrations.AddField("game", "worker_recovery_count",
            models.PositiveSmallIntegerField(default=0)),
        migrations.AddField("game", "ball_sampling_started_at",
            models.DateTimeField(blank=True, null=True)),
        migrations.AddField("game", "ball_sampling_heartbeat_at",
            models.DateTimeField(blank=True, null=True)),
        migrations.AddField("game", "ball_sampling_worker_token",
            models.CharField(blank=True, default="", max_length=36)),
        migrations.AddField("game", "ball_sampling_recovery_count",
            models.PositiveSmallIntegerField(default=0)),
        migrations.AddField("export", "worker_heartbeat_at",
            models.DateTimeField(blank=True, null=True)),
        migrations.AddField("export", "worker_token",
            models.CharField(blank=True, default="", max_length=36)),
        migrations.AddField("export", "worker_recovery_count",
            models.PositiveSmallIntegerField(default=0)),
    ]
