from django.db import migrations, models

class Migration(migrations.Migration):
    dependencies=[("highlights","0006_ball_annotation_suggestions")]
    operations=[
        migrations.AddField(model_name="ballframeannotation",name="hard_negative_reason",
            field=models.CharField(blank=True,default="",max_length=24)),
    ]
