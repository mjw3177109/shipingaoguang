from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("highlights", "0007_hard_negative_reason"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="ballframeannotation", name="quality_verified",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="ballframeannotation", name="quality_verified_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="ballframeannotation", name="quality_verified_by",
            field=models.ForeignKey(
                blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                related_name="verified_ball_frames", to=settings.AUTH_USER_MODEL,
            ),
        ),
    ]
