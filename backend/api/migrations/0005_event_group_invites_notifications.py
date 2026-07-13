from django.conf import settings
from django.db import migrations, models
import uuid


def populate_codes_and_creators(apps, schema_editor):
    User = apps.get_model('api', 'User')
    Event = apps.get_model('api', 'Event')
    UserGroup = apps.get_model('api', 'UserGroup')

    first_user = User.objects.order_by('id').first()

    for group in UserGroup.objects.all():
        if group.code is None:
            group.code = uuid.uuid4()
        if group.created_by_id is None:
            creator = group.members.order_by('id').first() or first_user
            if creator:
                group.created_by = creator
        group.save(update_fields=['code', 'created_by'])

    for event in Event.objects.all():
        if event.code is None:
            event.code = uuid.uuid4()
        if event.created_by_id is None:
            creator = event.participants.order_by('id').first() or first_user
            if creator:
                event.created_by = creator
        event.save(update_fields=['code', 'created_by'])


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('api', '0004_event_groups_event_participants_alter_event_date_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='usergroup',
            name='code',
            field=models.UUIDField(editable=False, null=True),
        ),
        migrations.AddField(
            model_name='usergroup',
            name='created_by',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=models.deletion.CASCADE,
                related_name='groups_created',
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AlterField(
            model_name='usergroup',
            name='name',
            field=models.CharField(max_length=100),
        ),
        migrations.AlterField(
            model_name='usergroup',
            name='members',
            field=models.ManyToManyField(blank=True, related_name='user_groups', to=settings.AUTH_USER_MODEL),
        ),
        migrations.AddField(
            model_name='event',
            name='code',
            field=models.UUIDField(editable=False, null=True),
        ),
        migrations.AddField(
            model_name='event',
            name='created_by',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=models.deletion.CASCADE,
                related_name='events_created',
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AlterField(
            model_name='event',
            name='title',
            field=models.CharField(max_length=100),
        ),
        migrations.CreateModel(
            name='GroupInvite',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('status', models.CharField(choices=[('pending', 'Pending'), ('accepted', 'Accepted'), ('declined', 'Declined')], default='pending', max_length=10)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('responded_at', models.DateTimeField(blank=True, null=True)),
                ('group', models.ForeignKey(on_delete=models.deletion.CASCADE, related_name='invites', to='api.usergroup')),
                ('invitee', models.ForeignKey(on_delete=models.deletion.CASCADE, related_name='group_invites', to=settings.AUTH_USER_MODEL)),
                ('invited_by', models.ForeignKey(on_delete=models.deletion.CASCADE, related_name='group_invites_sent', to=settings.AUTH_USER_MODEL)),
            ],
        ),
        migrations.CreateModel(
            name='EventInvite',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('status', models.CharField(choices=[('pending', 'Pending'), ('accepted', 'Accepted'), ('declined', 'Declined')], default='pending', max_length=10)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('responded_at', models.DateTimeField(blank=True, null=True)),
                ('event', models.ForeignKey(on_delete=models.deletion.CASCADE, related_name='user_invites', to='api.event')),
                ('invitee', models.ForeignKey(on_delete=models.deletion.CASCADE, related_name='event_invites', to=settings.AUTH_USER_MODEL)),
                ('invited_by', models.ForeignKey(on_delete=models.deletion.CASCADE, related_name='event_invites_sent', to=settings.AUTH_USER_MODEL)),
            ],
        ),
        migrations.CreateModel(
            name='EventGroupInvite',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('status', models.CharField(choices=[('pending', 'Pending'), ('accepted', 'Accepted'), ('declined', 'Declined')], default='pending', max_length=10)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('responded_at', models.DateTimeField(blank=True, null=True)),
                ('event', models.ForeignKey(on_delete=models.deletion.CASCADE, related_name='group_invites', to='api.event')),
                ('group', models.ForeignKey(on_delete=models.deletion.CASCADE, related_name='event_invites', to='api.usergroup')),
                ('invited_by', models.ForeignKey(on_delete=models.deletion.CASCADE, related_name='event_group_invites_sent', to=settings.AUTH_USER_MODEL)),
            ],
        ),
        migrations.CreateModel(
            name='Notification',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('type', models.CharField(choices=[('event_invite', 'Event Invite'), ('group_invite', 'Group Invite'), ('event_group_invite', 'Event Group Invite')], max_length=32)),
                ('message', models.CharField(max_length=255)),
                ('is_read', models.BooleanField(default=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('event_group_invite', models.ForeignKey(blank=True, null=True, on_delete=models.deletion.CASCADE, to='api.eventgroupinvite')),
                ('event_invite', models.ForeignKey(blank=True, null=True, on_delete=models.deletion.CASCADE, to='api.eventinvite')),
                ('group_invite', models.ForeignKey(blank=True, null=True, on_delete=models.deletion.CASCADE, to='api.groupinvite')),
                ('recipient', models.ForeignKey(on_delete=models.deletion.CASCADE, related_name='notifications', to=settings.AUTH_USER_MODEL)),
            ],
            options={'ordering': ['-created_at']},
        ),
        migrations.AlterUniqueTogether(name='groupinvite', unique_together={('group', 'invitee')}),
        migrations.AlterUniqueTogether(name='eventinvite', unique_together={('event', 'invitee')}),
        migrations.AlterUniqueTogether(name='eventgroupinvite', unique_together={('event', 'group')}),
        migrations.RunPython(populate_codes_and_creators, noop),
        migrations.AlterField(
            model_name='usergroup',
            name='created_by',
            field=models.ForeignKey(
                on_delete=models.deletion.CASCADE,
                related_name='groups_created',
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AlterField(
            model_name='event',
            name='created_by',
            field=models.ForeignKey(
                on_delete=models.deletion.CASCADE,
                related_name='events_created',
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AlterField(
            model_name='usergroup',
            name='code',
            field=models.UUIDField(default=uuid.uuid4, editable=False, unique=True),
        ),
        migrations.AlterField(
            model_name='event',
            name='code',
            field=models.UUIDField(default=uuid.uuid4, editable=False, unique=True),
        ),
    ]
