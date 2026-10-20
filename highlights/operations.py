"""Local-media resource guards; do not mistake these for host-level disk quotas."""
import shutil
from pathlib import Path

from django.conf import settings


class InsufficientStorage(RuntimeError):
    pass


def media_disk_free_bytes(path=None):
    """Probe the filesystem backing MEDIA_ROOT, even before its first mkdir."""
    root = Path(path or settings.MEDIA_ROOT).resolve()
    while not root.exists():
        if root == root.parent:
            raise InsufficientStorage("MEDIA_ROOT filesystem path does not exist.")
        root = root.parent
    try:
        return int(shutil.disk_usage(root).free)
    except OSError as exc:
        raise InsufficientStorage("无法检查媒体目录所在磁盘的可用空间。") from exc


def require_media_space(additional_bytes, *, reserve_bytes=None):
    """Fail before writing user data if available space is below required budget.

    This check is advisory against concurrent processes; monitor and enforce
    volumes at infrastructure level too. It never claims filesystem reservation.
    """
    if type(additional_bytes) is not int or additional_bytes < 0:
        raise ValueError("Storage demand must be a non-negative integer")
    reserve = (settings.MEDIA_MIN_FREE_BYTES if reserve_bytes is None
               else reserve_bytes)
    if type(reserve) is not int or reserve < 0:
        raise ValueError("Storage reserve must be non-negative")
    if media_disk_free_bytes() < additional_bytes + reserve:
        raise InsufficientStorage(
            "磁盘空间不足：请清理媒体文件或扩容后重试，现有录像和片段未作修改。")


def export_temp_bytes(total_clip_seconds):
    """Budget output + temporary clips + audio/fallback re-encoding overhead."""
    import math
    if not math.isfinite(total_clip_seconds) or total_clip_seconds <= 0:
        raise ValueError("Invalid total clipped seconds")
    return max(64 * 1024 * 1024,
               math.ceil(total_clip_seconds * settings.EXPORT_TEMP_BYTES_PER_SECOND))


def owner_raw_media_bytes(owner):
    """Count tenant raw uploads via storage API; missing media blocks new uploads."""
    from .models import Game

    amount = 0
    # Model FileField storage may vary by deployment. Fail closed on missing media.
    for game in Game.objects.filter(venue__owner=owner).only("video"):
        if game.video:
            try:
                amount += game.video.size
            except (OSError, ValueError) as exc:
                raise InsufficientStorage(
                    "已有录像无法统计存储额度，请联系管理员检查媒体文件。") from exc
    return amount
