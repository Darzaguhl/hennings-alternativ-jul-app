import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models

import api.models


def prune_duplicate_qrcodes(apps, schema_editor):
    QRCode = apps.get_model("api", "QRCode")

    seen_users = set()
    for qr in QRCode.objects.order_by("user_id", "id"):
        if qr.user_id is None:
            qr.delete()
            continue

        if qr.user_id in seen_users:
            qr.delete()
            continue

        seen_users.add(qr.user_id)

    for qr in QRCode.objects.all():
        qr.data = uuid.uuid4().hex
        qr.save(update_fields=["data"])


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("api", "0006_alter_eventgroupinvite_id_alter_eventinvite_id_and_more"),
        ("api", "0006_event_auto_approve"),
    ]

    operations = [
        migrations.RunPython(prune_duplicate_qrcodes, reverse_code=noop),
        migrations.AlterField(
            model_name="qrcode",
            name="user",
            field=models.OneToOneField(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="qr_code",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AlterField(
            model_name="qrcode",
            name="data",
            field=models.CharField(
                default=api.models.generate_qr_payload,
                editable=False,
                max_length=64,
                unique=True,
            ),
        ),
        migrations.AddField(
            model_name="qrcode",
            name="updated_at",
            field=models.DateTimeField(auto_now=True),
        ),
    ]
