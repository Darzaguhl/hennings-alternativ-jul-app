from django.contrib import admin

from .models import Assignment, Event, EventCheckIn, Shift, ShiftSignup, Skill

admin.site.register(Event)
admin.site.register(Shift)
admin.site.register(ShiftSignup)
admin.site.register(EventCheckIn)
admin.site.register(Assignment)
admin.site.register(Skill)
