from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("highlights", "0003_annotations")]

    operations = [
        migrations.AddField(
            model_name="game", name="analysis_engine",
            field=models.CharField(max_length=16, default="audio",
                                   choices=[("audio", "声音峰值"), ("cpu", "CPU 球状物体候选")]),
        ),
    ]
