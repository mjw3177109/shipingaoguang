from django.contrib import admin
from .models import Venue, Game, Candidate, Export, ShotAnnotation

admin.site.register(Venue)
admin.site.register(Game)
admin.site.register(Candidate)
admin.site.register(Export)

admin.site.register(ShotAnnotation)
