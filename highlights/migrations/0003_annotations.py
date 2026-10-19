from django.db import migrations, models
import django.db.models.deletion

class Migration(migrations.Migration):
    dependencies = [("highlights", "0002_game_hoops")]
    operations = [
        migrations.AddField(
            model_name="game",
            name="annotations_complete",
            field=models.BooleanField(default=False),
        ),
        migrations.CreateModel(
            name="ShotAnnotation",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("time_seconds", models.FloatField()),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("game", models.ForeignKey(to="highlights.game", related_name="shot_annotations",
                                           on_delete=django.db.models.deletion.CASCADE)),
            ],
            options={"ordering": ["time_seconds", "id"]},
        ),
    ]
