# Generated manually alongside 0022_oppgaveslot / 0024

from django.db import migrations


def clear_signups_and_assignments(apps, schema_editor):
    # Every existing ShiftSignup/Assignment was created before OppgaveSlot
    # existed, so it only references a shift, never an oppgave -- there is
    # no correct slot to backfill onto the next migration's new required
    # oppgave_slot field. Real volunteer signups haven't opened yet for the
    # event these rows belong to, so this only clears test/admin-created
    # data, not anything a real volunteer would lose.
    ShiftSignup = apps.get_model("api", "ShiftSignup")
    Assignment = apps.get_model("api", "Assignment")
    Assignment.objects.all().delete()
    ShiftSignup.objects.all().delete()


class Migration(migrations.Migration):

    dependencies = [
        ('api', '0022_oppgaveslot'),
    ]

    operations = [
        migrations.RunPython(clear_signups_and_assignments, migrations.RunPython.noop),
    ]
