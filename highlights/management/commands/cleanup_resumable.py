"""Remove expired, cancelled, or completed resumable staging files.

Do not delete Games. Run hourly or daily via cron; --dry-run is default.
"""
from datetime import timedelta
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from highlights.models import ResumableUpload
from highlights.resumable import safe_unlink


class Command(BaseCommand):
    help = "Clean expired resumable upload sessions (dry-run without --apply)."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true")
        parser.add_argument("--limit", type=int, default=200)

    def handle(self, *args, **options):
        limit = options["limit"]
        if not 1 <= limit <= 10000:
            raise ValueError("--limit must be 1..10000")
        boundary = timezone.now()
        identifiers = list(
            ResumableUpload.objects.filter(expires_at__lte=boundary)
            .order_by("expires_at").values_list("pk", flat=True)[:limit])
        cleaned = 0
        for identifier in identifiers:
            if not options["apply"]:
                cleaned += 1
                continue
            with transaction.atomic():
                session = ResumableUpload.objects.select_for_update().filter(pk=identifier).first()
                if session is None or session.expires_at > timezone.now():
                    continue
                safe_unlink(session)
                session.delete()
                cleaned += 1
        mode = "deleted" if options["apply"] else "eligible_for_deletion"
        self.stdout.write(f"{mode}={cleaned} (limit={limit})")
