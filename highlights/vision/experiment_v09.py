"""V0.9 explicit CPU-only research experiment orchestration.

Default is preflight ONLY: no model weights, no benchmark, no training.
--execute trains ONLY approved human-reviewed game-disjoint datasets, then
compares against the COCO model on a held-out test partition. Always research.
"""
import argparse
import hashlib
import json
from pathlib import Path

from .preflight_v09 import inspect_dataset


def _sha(path):
    stream = hashlib.sha256()
    with Path(path).open("rb") as reader:
        for chunk in iter(lambda: reader.read(1024 * 1024), b""):
            stream.update(chunk)
    return stream.hexdigest()


def run_experiment(dataset, output, *, execute=False, epochs=2, max_steps=100):
    root = Path(dataset).resolve()
    destination = Path(output).resolve()
    if destination == root or root in destination.parents or destination in root.parents:
        raise ValueError("Use a separate output directory, outside the dataset")
    if destination.exists() and any(destination.iterdir()):
        raise ValueError("Experiment output must be an empty directory")
    if not 1 <= epochs <= 25 or not 1 <= max_steps <= 1000:
        raise ValueError("Invalid CPU experiment budget")
    preflight = inspect_dataset(root)
    destination.mkdir(parents=True, exist_ok=True)
    (destination / "preflight.json").write_text(
        json.dumps(preflight, ensure_ascii=False, indent=2), encoding="utf-8")
    status = {
        "schema_version": 1, "experiment_kind": "v09_cpu_basketball_box_comparison",
        "dataset_manifest_sha256": preflight["dataset_manifest_sha256"],
        "preflight_passed": preflight["allow_cpu_research_experiment"],
        "experiment_requested": bool(execute),
        "stage": "BLOCKED" if not preflight["allow_cpu_research_experiment"] else
                 ("PREFLIGHT_ONLY" if not execute else "TRAINING"),
        "checkpoint_sha256": None, "baseline_predictions_sha256": None,
        "challenger_predictions_sha256": None,
        "box_metrics": None, "production_release": False,
        "warning": "Research-only basketball bounding boxes; not made shots or a production release.",
    }
    report = destination / "experiment.json"
    def save():
        report.write_text(json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8")
    save()
    if not preflight["allow_cpu_research_experiment"] or not execute:
        return status
    # Train/validation only; training function leaves the test split untouched.
    from .train_specialist import train
    from .predict_boxes_v05 import predict
    from .compare_v05 import compare_dataset
    checkpoint = destination / "basketball_research_only.pt"
    baseline_path = destination / "coco_baseline_test_predictions.json"
    challenger_path = destination / "challenger_test_predictions.json"
    try:
        training = train(root, checkpoint, epochs=epochs, max_steps=max_steps,
                         device="cpu", pretrained=True)
        status["checkpoint_sha256"] = _sha(checkpoint)
        status["training"] = {
            "train_games": training["train_games"],
            "val_games": training["val_games"],
            "sealed_test_games_unread": training["sealed_test_games_unread"],
            "train_images": training["train_images"],
            "val_images": training["val_images"],
            "cpu_steps": training["steps"],
            "last_loss": training["last_loss"],
        }
        status["stage"] = "TEST_INFERENCE"
        save()
        predict(root, baseline_path, model_kind="coco-mobile320", confidence_floor=.05)
        predict(root, challenger_path, model_kind="specialist",
                checkpoint=str(checkpoint), allow_research=True, confidence_floor=.05)
        status["baseline_predictions_sha256"] = _sha(baseline_path)
        status["challenger_predictions_sha256"] = _sha(challenger_path)
        # Single fixed threshold. NEVER sweep sealed test labels to tune a model.
        compare = compare_dataset(root, baseline_path, challenger_path)
        (destination / "box_comparison.json").write_text(
            json.dumps(compare, ensure_ascii=False, indent=2), encoding="utf-8")
        status["stage"] = "RESEARCH_REPORT_COMPLETE"
        status["box_metrics"] = {
            "baseline": compare["baseline"], "challenger": compare["challenger"],
            "f1_gain": compare["f1_gain"],
            "eligible_for_independent_human_review":
                compare["eligible_for_independent_human_review"],
            "production_release": False,
        }
    except Exception as exc:
        status["stage"] = "FAILED"
        status["failure_type"] = type(exc).__name__
        status["failure_detail"] = str(exc)[:500]
        save()
        raise
    save()
    return status


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="V0.9 basketball experiment: default safe preflight; --execute trains CPU research-only")
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--execute", action="store_true",
                        help="Explicit opt-in CPU training and sealed-test comparison")
    parser.add_argument("--epochs", type=int, default=2)
    parser.add_argument("--max-steps", type=int, default=100)
    args = parser.parse_args(argv)
    status = run_experiment(args.dataset, args.out,
                            execute=args.execute, epochs=args.epochs,
                            max_steps=args.max_steps)
    print(json.dumps(status, ensure_ascii=False, indent=2))
    if args.execute and status["stage"] == "BLOCKED":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
