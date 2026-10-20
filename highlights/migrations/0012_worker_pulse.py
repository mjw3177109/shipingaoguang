from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("highlights", "0011_worker_leases")]

    operations = [
        migrations.CreateModel(
            name="WorkerPulse",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True,
                                           serialize=False, verbose_name="ID")),
                ("key", models.CharField(default="cpu-primary", max_length=32,
                                          unique=True)),
                ("last_seen_at", models.DateTimeField()),
                ("started_at", models.DateTimeField()),
                ("task_count", models.PositiveIntegerField(default=0)),
            ],
        ),
    ]
