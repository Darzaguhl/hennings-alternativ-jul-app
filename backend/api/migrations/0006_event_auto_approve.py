from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("api", "0005_event_group_invites_notifications"),
    ]

    operations = [
        migrations.AddField(
            model_name="event",
            name="auto_approve",
            field=models.BooleanField(default=False),
        ),
    ]
