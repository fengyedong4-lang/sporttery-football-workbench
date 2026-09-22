from __future__ import annotations

import argparse
import json
from pathlib import Path

from .config import settings
from .services.backtest import compare_backtests, save_backtest, walk_forward_backtest
from .services.history import build_history_index
from .services.model import GoalModel, export_training_csv, import_football_data_csv, import_training_csv
from .services.datasets import load_dataset_manifest, sync_season_datasets, train_manifest_models


def main() -> int:
    parser = argparse.ArgumentParser(description="足球预测工作台本地CLI")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("index")
    normalize = sub.add_parser("normalize-football-data")
    normalize.add_argument("csv", type=Path)
    normalize.add_argument("--competition", required=True)
    normalize.add_argument("--source-url", required=True)
    normalize.add_argument("--output", type=Path, required=True)
    train = sub.add_parser("train")
    train.add_argument("csv", type=Path)
    train.add_argument("--competition", required=True)
    train.add_argument("--model", choices=("poisson", "dixon_coles"), required=True)
    train.add_argument("--name", required=True)
    train.add_argument("--decay", type=float, default=0.003)
    backtest = sub.add_parser("backtest")
    backtest.add_argument("csv", type=Path)
    backtest.add_argument("--competition", required=True)
    backtest.add_argument("--model", choices=("poisson", "dixon_coles"), required=True)
    backtest.add_argument("--output", type=Path, required=True)
    backtest.add_argument("--min-train", type=int, default=80)
    backtest.add_argument("--refit-every", type=int, default=20)
    compare = sub.add_parser("compare-backtests")
    compare.add_argument("baseline", type=Path)
    compare.add_argument("candidate", type=Path)
    compare.add_argument("--output", type=Path, required=True)
    sub.add_parser("sync-season-datasets")
    sub.add_parser("train-season-models")
    args = parser.parse_args()
    if args.command == "index":
        result = build_history_index(settings.history_path, settings.runtime_dir / "cache" / "history_index.json")
        print(json.dumps({key: value for key, value in result.items() if key != "entries"}, ensure_ascii=False, indent=2))
    elif args.command == "normalize-football-data":
        matches = import_football_data_csv(
            args.csv, competition=args.competition, source_url=args.source_url
        )
        export_training_csv(matches, args.output)
        print(json.dumps({"output": str(args.output), "matches": len(matches)}, ensure_ascii=False))
    elif args.command == "train":
        matches = import_training_csv(args.csv)
        model = GoalModel.fit(matches, competition=args.competition, model_type=args.model, decay=args.decay)
        output = settings.runtime_dir / "models" / f"{args.name}.json"
        if output.exists():
            raise SystemExit("模型版本已存在，拒绝覆盖")
        model.save(output)
        print(output)
    elif args.command == "backtest":
        report = walk_forward_backtest(
            import_training_csv(args.csv), competition=args.competition, model_type=args.model,
            min_train=args.min_train, refit_every=args.refit_every,
        )
        save_backtest(report, args.output)
        print(args.output)
    elif args.command == "compare-backtests":
        save_backtest(compare_backtests(args.baseline, args.candidate), args.output)
        print(args.output)
    elif args.command == "sync-season-datasets":
        print(json.dumps(sync_season_datasets(settings.runtime_dir), ensure_ascii=False, indent=2))
    elif args.command == "train-season-models":
        manifest = load_dataset_manifest(settings.runtime_dir)
        if manifest.get("status") == "not_synced":
            raise SystemExit("训练数据尚未同步，请先运行 sync-season-datasets")
        print(json.dumps(train_manifest_models(settings.runtime_dir, manifest), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
