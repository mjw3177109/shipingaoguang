"""Pure normalization/validation for human-labeled basketball bounding boxes."""
import math


def validate_ball_label(time_value, rect, duration, absent=False):
    try:
        moment = float(time_value)
        duration = float(duration)
    except (TypeError, ValueError) as exc:
        raise ValueError("视频时间无效") from exc
    if not all(math.isfinite(z) for z in (moment,duration)) or not 0 <= moment < duration:
        raise ValueError("时间必须位于录像内")
    moment = round(moment,2)
    if absent:
        return moment, (0.,0.,0.,0.)
    if not isinstance(rect,dict):
        raise ValueError("请框选篮球或勾选本帧无球")
    try:
        vals = tuple(float(rect[k]) for k in ("x","y","w","h"))
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("篮球框位置无效") from exc
    if not all(math.isfinite(z) for z in vals):
        raise ValueError("篮球坐标不能是 NaN/Infinity")
    x,y,w,h=vals
    if not (0 <= x < 1 and 0 <= y < 1 and .003 <= w <= .6
            and .003 <= h <= .6 and x+w <= 1.00001 and y+h <= 1.00001):
        raise ValueError("篮球框必须落在画面内且符合最小尺寸")
    return moment, tuple(round(v,5) for v in vals)
