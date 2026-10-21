# Generated for BasketCut V1.7; initial resumable upload session model.
import uuid
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("highlights", "0012_worker_pulse"),
    ]

    operations = [
        migrations.CreateModel(
            name="ResumableUpload",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("title", models.CharField(max_length=180)),
                ("filename", models.CharField(max_length=180)),
                ("byte_size", models.PositiveBigIntegerField()),
                ("expected_sha256", models.CharField(max_length=64)),
                ("received_bytes", models.PositiveBigIntegerField(default=0)),
                ("state", models.CharField(choices=[
                    ("receiving", "接收中"), ("complete", "已完成"),
                    ("cancelled", "已取消"),
                ], default="receiving", max_length=16)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("expires_at", models.DateTimeField()),
                ("game", models.ForeignKey(blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name="resumable_upload_records", to="highlights.game")),
                ("owner", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE,
                    related_name="basketcut_uploads", to=settings.AUTH_USER_MODEL)),
                ("venue", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE,
                    related_name="resumable_uploads", to="highlights.venue")),
            ],
            options={"indexes": [
                models.Index(fields=["state", "expires_at"], name="hl_upload_expiry_idx"),
            ]},
        )
    ]
