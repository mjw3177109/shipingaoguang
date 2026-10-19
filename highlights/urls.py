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
    path("games/<int:pk>/import/", views.import_proposals, name="import_proposals"),
    path("games/<int:pk>/hoops/", views.save_hoops, name="save_hoops"),
    path("games/<int:pk>/vision-config/", views.vision_config, name="vision_config"),
    path("games/<int:pk>/labels/new/", views.add_shot_annotation, name="add_shot_annotation"),
    path("games/<int:pk>/labels/<int:annotation_id>/delete/", views.delete_shot_annotation, name="delete_shot_annotation"),
    path("games/<int:pk>/labels/complete/", views.confirm_ground_truth, name="confirm_ground_truth"),
    path("games/<int:pk>/labels/truth.json", views.download_ground_truth, name="download_ground_truth"),
    path("games/<int:pk>/delete/", views.delete_game, name="delete_game"),
    path("games/<int:pk>/video/", views.source_video, name="source_video"),
    path("games/<int:pk>/exports/<int:export_id>/download/", views.export_video, name="export_video"),
]
