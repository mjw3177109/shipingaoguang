from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies=[("highlights","0005_ball_training_labels")]
    operations=[
        migrations.AddField(model_name="game",name="ball_sampling_state",
            field=models.CharField(max_length=16,default="idle",
                                   choices=[("idle","未采样"),("queued","等待CPU采样"),
                                            ("running","CPU采样中"),("ready","待人工标注"),
                                            ("failed","采样失败")])),
        migrations.AddField(model_name="game",name="ball_sampling_error",
            field=models.CharField(max_length=600,blank=True)),
        migrations.CreateModel(name="BallFrameSuggestion",fields=[
            ("id",models.BigAutoField(auto_created=True,primary_key=True,serialize=False,verbose_name="ID")),
            ("time_seconds",models.FloatField()),
            ("reason",models.CharField(max_length=24)),
            ("score",models.FloatField(default=0)),
            ("created_at",models.DateTimeField(auto_now_add=True)),
            ("game",models.ForeignKey(to="highlights.game",
                      related_name="ball_suggestions",on_delete=django.db.models.deletion.CASCADE)),
        ],options={"ordering":["time_seconds","id"],"constraints":[
            models.UniqueConstraint(fields=["game","time_seconds"],name="unique_suggested_ball_frame")
        ]}),
    ]
