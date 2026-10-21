# BasketCut V1.8 singleton shared-volume admission lock.
from django.db import migrations, models


def seed_lock(apps, schema_editor):
    MediaCapacityLock = apps.get_model("highlights", "MediaCapacityLock")
    MediaCapacityLock.objects.using(schema_editor.connection.alias).get_or_create(pk=1)


def remove_lock(apps, schema_editor):
    MediaCapacityLock = apps.get_model("highlights", "MediaCapacityLock")
    MediaCapacityLock.objects.using(schema_editor.connection.alias).filter(pk=1).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("highlights", "0013_resumable_upload"),
    ]

    operations = [
        migrations.CreateModel(
            name="MediaCapacityLock",
            fields=[
                ("id", models.PositiveSmallIntegerField(
                    default=1, editable=False, primary_key=True, serialize=False))
            ],
        ),
        migrations.RunPython(seed_lock, remove_lock),
    ]
