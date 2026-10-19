"""Box-level IoU metrics; only human-verified heldout boxes qualify as evaluation."""
import math


def iou_xyxy(a,b):
    for rect in (a,b):
        if len(rect)!=4 or not all(math.isfinite(float(v)) for v in rect):
            raise ValueError("bbox coordinates must be finite 4-vectors")
    x1=max(a[0],b[0]);y1=max(a[1],b[1]);x2=min(a[2],b[2]);y2=min(a[3],b[3])
    intersection=max(0,x2-x1)*max(0,y2-y1)
    area1=max(0,a[2]-a[0])*max(0,a[3]-a[1])
    area2=max(0,b[2]-b[0])*max(0,b[3]-b[1])
    denominator=area1+area2-intersection
    return intersection/denominator if denominator>0 else 0


def box_counts(predictions, truth, threshold=.5):
    """1:1 match per image, return TP/FP/FN for basketball objects."""
    if not 0<threshold<=1:raise ValueError("invalid IoU threshold")
    edges=[]
    for i,p in enumerate(predictions):
        for j,g in enumerate(truth):
            overlap=iou_xyxy(p,g)
            if overlap>=threshold:
                edges.append((overlap,i,j))
    edges.sort(reverse=True)
    used_pred=set();used_gt=set()
    for _,i,j in edges:
        if i not in used_pred and j not in used_gt:
            used_pred.add(i);used_gt.add(j)
    return {"tp":len(used_gt),"fp":len(predictions)-len(used_pred),
            "fn":len(truth)-len(used_gt)}


def box_summary(total):
    tp,fp,fn=(total[k] for k in ("tp","fp","fn"))
    return {"precision":round(tp/(tp+fp),4) if tp+fp else None,
            "recall":round(tp/(tp+fn),4) if tp+fn else None,
            "f1":round(2*tp/(2*tp+fp+fn),4) if 2*tp+fp+fn else None,
            "tp":tp,"fp":fp,"fn":fn}
