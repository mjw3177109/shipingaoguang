# Generated manually for the initial BasketCut MVP schema.
import django.db.models.deletion
import highlights.models
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True
    dependencies = [migrations.swappable_dependency(settings.AUTH_USER_MODEL)]
    operations = [
        migrations.CreateModel(
            name="Venue",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=120)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("owner", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="venues", to=settings.AUTH_USER_MODEL)),
            ],
        ),
        migrations.CreateModel(
            name="Game",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("title", models.CharField(max_length=180)),
                ("video", models.FileField(upload_to=highlights.models.video_path)),
                ("duration_seconds", models.FloatField(default=0)),
                ("state", models.CharField(choices=[("uploaded", "待分析"), ("queued", "分析排队中"), ("analyzing", "分析中"), ("ready", "待审核"), ("failed", "失败")], default="uploaded", max_length=16)),
                ("error", models.TextField(blank=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("venue", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="games", to="highlights.venue")),
            ],
        ),
        migrations.CreateModel(
            name="Candidate",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("start_seconds", models.FloatField()),
                ("end_seconds", models.FloatField()),
                ("source", models.CharField(default="manual", max_length=32)),
                ("energy_score", models.FloatField(default=0)),
                ("review", models.CharField(choices=[("pending", "待审"), ("approved", "通过"), ("rejected", "剔除")], default="pending", max_length=16)),
                ("player_label", models.CharField(blank=True, max_length=100)),
                ("note", models.CharField(blank=True, max_length=240)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("game", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="candidates", to="highlights.game")),
            ],
            options={"ordering": ["start_seconds", "id"]},
        ),
        migrations.CreateModel(
            name="Export",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("player_label", models.CharField(blank=True, max_length=100)),
                ("state", models.CharField(choices=[("queued", "排队中"), ("running", "渲染中"), ("done", "已完成"), ("failed", "失败")], default="queued", max_length=16)),
                ("video", models.FileField(blank=True, upload_to=highlights.models.export_path)),
                ("error", models.TextField(blank=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("game", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="exports", to="highlights.game")),
            ],
        ),
    ]
