"""Reject obvious oversized multipart bodies BEFORE CSRF or Django upload parsing.

A header-only soft guard. Proxy request limits and temp-volume quotas remain necessary.
"""
from django.conf import settings
from django.http import HttpResponse


class UploadBodyLimitMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.method == "POST" and (
                request.path_info == "/games/upload/" or
                request.path_info.startswith("/games/resumable/")):
            raw = request.META.get("CONTENT_LENGTH", "")
            if not raw or not raw.isdecimal():
                return HttpResponse("Content-Length required", status=411)
            length = int(raw)
            if request.path_info.startswith("/games/resumable/"):
                is_chunk = request.path_info.endswith("/chunk/")
                ceiling = (getattr(settings, "RESUMABLE_CHUNK_MAX_BYTES", 8 * 1024**2)
                           if is_chunk else 8192)
                if length > ceiling:
                    result = HttpResponse("Resumable request too large", status=413)
                    result["Cache-Control"] = "no-store"
                    return result
                return self.get_response(request)
            # Account for reasonably sized multipart headers, not a second full file.
            if length > settings.MAX_UPLOAD_BYTES + 1024 * 1024:
                result = HttpResponse("Upload request too large", status=413)
                result["Cache-Control"] = "no-store"
                return result
        return self.get_response(request)
