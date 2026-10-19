"""V0.8 cross-game label collection metrics, NOT recognition accuracy.

Only stored, explicitly human-entered labels contribute to data totals.
CPU timestamp suggestions are counted as backlog, never as ground truth.
"""
from collections import Counter

INITIAL_TARGETS = {"games":8,"positive":120,"negative":40}


def build_campaign_report(rows, *, targets=None):
    limits=dict(INITIAL_TARGETS if targets is None else targets)
    if any(type(v) is not int or v<1 for v in limits.values()) or set(limits)!=set(INITIAL_TARGETS):
        raise ValueError("Invalid positive campaign targets")
    records=[]
    by_type=Counter()
    for row in rows:
        data=dict(row)
        game_id=data["id"]
        for field in ("positive","negative","verified","pending"):
            if type(data.get(field)) is not int or data[field]<0:
                raise ValueError(f"Invalid {field} count for game {game_id}")
        if data["verified"]>data["positive"]+data["negative"]:
            raise ValueError("Verified frames cannot exceed labeled frames")
        records.append({
            "id":game_id,"title":data["title"],"venue_name":data["venue_name"],
            "positive":data["positive"],"negative":data["negative"],
            "verified":data["verified"],
            "unverified":data["positive"]+data["negative"]-data["verified"],
            "pending":data["pending"],
            "approved":bool(data["approved"]),
            "sampling_state":data["sampling_state"],
            "sampling_rounds":data["sampling_rounds"],
        })
    total_positive=sum(r["positive"] for r in records)
    total_negative=sum(r["negative"] for r in records)
    total_verified=sum(r["verified"] for r in records)
    labeled_games=sum(r["positive"]+r["negative"]>0 for r in records)
    consented_games=sum(r["approved"] and r["positive"]+r["negative"]>0 for r in records)
    eligible_games=sum(r["approved"] and r["positive"]+r["negative"]>0 and
                       r["unverified"]==0 for r in records)
    counts={"games":eligible_games,"positive":total_positive,"negative":total_negative}
    progress={key:{"current":value,"target":limits[key],
                   "remaining":max(0,limits[key]-value),
                   "percent":round(min(100,100*value/limits[key]))}
              for key,value in counts.items()}
    blockers=[]
    if eligible_games<limits["games"]:
        blockers.append("符合授权且已二次复核的比赛不足")
    if total_positive<limits["positive"]:
        blockers.append("人工篮球框正样本不足")
    if total_negative<limits["negative"]:
        blockers.append("人工确认无球的难负样本不足")
    if any(r["unverified"] for r in records):
        blockers.append("存在已标注但尚未二次复核的帧")
    if any(r["positive"]+r["negative"]>0 and not r["approved"] for r in records):
        blockers.append("部分标注比赛尚未重新确认训练素材授权")
    return {
        "schema_version":1,
        "scope":"v08_cross_game_label_collection",
        "targets":limits,
        "total_games":len(records),
        "labeled_games":labeled_games,
        "consented_labeled_games":consented_games,
        "eligible_games":eligible_games,
        "positive_frames":total_positive,
        "explicit_negative_frames":total_negative,
        "secondary_verified_frames":total_verified,
        "unverified_frames":sum(r["unverified"] for r in records),
        "pending_cpu_review_times":sum(r["pending"] for r in records),
        "progress":progress,
        "blockers":blockers,
        "basic_collection_targets_met":not blockers,
        "production_release":False,
        "warning":"Counts and voluntary review attestations are not model precision, video event recall or production permission. Train/val/test split quality is separately checked.",
        "games":sorted(records,key=lambda r:(-(r["unverified"]+r["pending"]),r["id"])),
    }
