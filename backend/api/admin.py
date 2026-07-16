from django.contrib import admin

from .models import (
    Assignment,
    Event,
    EventCheckIn,
    Invite,
    Membership,
    OppgaveSlot,
    PasswordSetupToken,
    Shift,
    ShiftSignup,
    Skill,
)

admin.site.register(Event)
admin.site.register(Membership)
admin.site.register(Shift)
admin.site.register(OppgaveSlot)
admin.site.register(ShiftSignup)
admin.site.register(EventCheckIn)
admin.site.register(Assignment)
admin.site.register(Skill)
admin.site.register(Invite)
admin.site.register(PasswordSetupToken)
