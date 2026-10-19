from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("highlights", "0001_initial")]
    operations = [
        migrations.AddField(
            model_name="game",
            name="hoops",
            field=models.JSONField(blank=True, default=list),
        ),
    ]
