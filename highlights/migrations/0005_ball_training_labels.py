from django.db import migrations, models
import django.db.models.deletion

class Migration(migrations.Migration):
    dependencies = [("highlights", "0004_cpu_analysis_engine")]
    operations = [
        migrations.AddField(model_name="game", name="ball_training_approved",
                            field=models.BooleanField(default=False)),
        migrations.AddField(model_name="game", name="ball_training_rights",
                            field=models.CharField(blank=True, max_length=240)),
        migrations.CreateModel(name="BallFrameAnnotation", fields=[
            ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
            ("time_seconds", models.FloatField()),
            ("ball_absent", models.BooleanField(default=False)),
            ("x", models.FloatField(default=0)),
            ("y", models.FloatField(default=0)),
            ("w", models.FloatField(default=0)),
            ("h", models.FloatField(default=0)),
            ("updated_at", models.DateTimeField(auto_now=True)),
            ("game", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE,
                                       related_name="ball_frames", to="highlights.game")),
        ], options={"ordering": ["time_seconds","id"],
                    "constraints": [models.UniqueConstraint(fields=["game","time_seconds"], name="unique_ball_frame_time")]}),
    ]
