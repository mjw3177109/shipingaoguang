"""SYNTHETIC JPEG basketball dataset to test V0.9 preflight, NOT accuracy.

Generated pixels and bounding boxes are fiction. Do not train a commercial
model, remove the synthetic marker, or represent this data as human-labelled.
"""
import argparse
import hashlib
import json
from pathlib import Path

from highlights.vision.split_v05 import three_way_games


def create_synthetic_jpeg_dataset(root):
    import cv2
    import numpy as np
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    ids = list(range(1001, 1009))
    mapping = three_way_games(ids)
    games = []
    samples = []
    for game in ids:
        split = mapping[game]
        games.append({
            "id": game, "split": split, "positive": 20,
            "negative": 10, "sample_count": 30,
            "training_rights": "SYNTHETIC GENERATION, NOT A GRANTED RIGHTS LICENSE",
        })
        for i in range(30):
            absent = i >= 20
            img = np.zeros((64, 96, 3), dtype=np.uint8)
            code = game - 1000
            img[:, :] = [(code * 29) % 256, (code * 41) % 256, (code * 53 + i * 17) % 256]
            if not absent:
                cv2.circle(img, (48, 32), 5, (15, 110, 240), -1)
            good, encoded = cv2.imencode(".jpg", img)
            if not good:
                raise RuntimeError("OpenCV JPEG encoding failed")
            name = f"g{game}_{i:03d}"
            image = f"images/{split}/{name}.jpg"
            label = f"labels/{split}/{name}.txt"
            for p in (root / image, root / label):
                p.parent.mkdir(parents=True, exist_ok=True)
            content = encoded.tobytes()
            (root / image).write_bytes(content)
            (root / label).write_text(
                "" if absent else "0 0.500000 0.500000 0.104167 0.156250\n",
                encoding="utf-8")
            samples.append({
                "game_id": game, "split": split,
                "time_seconds": float(i),
                "image": image, "label": label,
                "ball_absent": absent,
                "quality_verified": True,
                "quality_verified_at": "2000-01-01T00:00:00+00:00",
                "quality_verified_by_id": 999,
                "hard_negative_reason": "jersey" if absent else "",
                "sha256_image": hashlib.sha256(content).hexdigest(),
            })
    manifest = {
        "schema_version": 1, "classes": ["basketball"],
        "synthetic": True,
        "WARNING": "SYNTHETIC JPEG FIXTURE. NO HUMAN LABELS, NO REAL PERFORMANCE.",
        "test_sealed": True, "method": "three_way_sealed_test_v05",
        "label_review_required": True, "games": games, "samples": samples,
    }
    (root / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return root


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--out", required=True)
    args = p.parse_args()
    path = create_synthetic_jpeg_dataset(args.out)
    print(f"Created SYNTHETIC JPEG data for CI integrity checks at {path}")


if __name__ == "__main__":
    main()
