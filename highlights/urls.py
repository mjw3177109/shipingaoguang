from django.urls import path
from . import views

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("venues/new/", views.create_venue, name="create_venue"),
    path("games/upload/", views.upload_game, name="upload_game"),
    path("games/<int:pk>/", views.game_detail, name="game"),
    path("games/<int:pk>/analyze/", views.queue_analysis, name="queue_analysis"),
    path("games/<int:pk>/clips/new/", views.add_candidate, name="add_candidate"),
    path("games/<int:pk>/clips/<int:candidate_id>/review/", views.review_candidate, name="review_candidate"),
    path("games/<int:pk>/exports/new/", views.queue_export, name="queue_export"),
    path("games/<int:pk>/delete/", views.delete_game, name="delete_game"),
    path("games/<int:pk>/video/", views.source_video, name="source_video"),
    path("games/<int:pk>/exports/<int:export_id>/download/", views.export_video, name="export_video"),
]
