from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies=[("highlights","0008_ball_frame_quality_review")]
    operations=[
        migrations.AddField(
            model_name="game",name="ball_sampling_round",
            field=models.PositiveIntegerField(default=0),
        ),
    ]
