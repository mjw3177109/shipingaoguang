from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [("highlights", "0009_ball_sampling_round")]
    operations = [
        migrations.AlterField(
            model_name="export", name="state",
            field=models.CharField(max_length=16,default="queued",choices=[
                ("queued","排队中"),("running","渲染中"),("done","已完成"),
                ("failed","失败"),("cancelled","已取消")]),
        ),
        migrations.AddField(model_name="export",name="plan",
                            field=models.JSONField(default=dict,blank=True)),
        migrations.AddField(model_name="export",name="plan_sha256",
                            field=models.CharField(max_length=64,blank=True)),
        migrations.AddField(model_name="export",name="attempt",
                            field=models.PositiveSmallIntegerField(default=1)),
        migrations.AddField(model_name="export",name="worker_started_at",
                            field=models.DateTimeField(null=True,blank=True)),
        migrations.AddField(model_name="export",name="clips_done",
                            field=models.PositiveSmallIntegerField(default=0)),
        migrations.AddField(model_name="export",name="clips_total",
                            field=models.PositiveSmallIntegerField(default=0)),
        migrations.AddField(model_name="export",name="retry_of",
                            field=models.ForeignKey(to="highlights.export",
                                blank=True,null=True,
                                on_delete=django.db.models.deletion.SET_NULL,
                                related_name="retry_attempts")),
    ]
