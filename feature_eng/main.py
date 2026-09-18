"""Build the 128 candle features using settings from a YAML file."""
from __future__ import annotations

import argparse
from pathlib import Path
import sys


PROJECT = Path(__file__).resolve().parents[1]
# Support both `python -m feature_eng.main` and direct invocation by the launcher.
if __package__ in (None, ""):
    sys.path.insert(0, str(PROJECT))
DEFAULT_CONFIG = PROJECT / "config" / "config_feature_eng.yaml"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config", type=Path, default=DEFAULT_CONFIG,
        help="YAML settings file (default: config/config_feature_eng.yaml in the repository)",
    )
    args = parser.parse_args(argv)

    try:
        from feature_eng.configuration import load_config
        config = load_config(args.config)
    except ModuleNotFoundError as error:
        print(f"Missing dependency: {error.name}. Install requirements.txt first.", file=sys.stderr)
        return 1
    except (OSError, ValueError) as error:
        print(f"Configuration error: {error}", file=sys.stderr)
        return 2

    print(f"Input: {config.input_path}", flush=True)
    print(f"Output: {config.output_dir}", flush=True)
    try:
        from feature_eng.build_features import generate_dataset
        summary = generate_dataset(
            config.input_path, config.output_dir,
            symbols=list(config.symbols) if config.symbols else None,
            gap_policy=config.gap_policy,
        )
    except ModuleNotFoundError as error:
        print(f"Missing dependency: {error.name}. Install requirements.txt first.", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Feature generation interrupted.", file=sys.stderr)
        return 130
    except Exception as error:
        print(f"Feature generation failed: {error}", file=sys.stderr)
        return 1

    print(
        f"Complete: {summary['rows']:,} rows, {summary['feature_count']} features, "
        f"{summary['processed_series']} instrument series.", flush=True,
    )
    for output_file in summary["output_files"]:
        print(f"Features: {config.output_dir / output_file}", flush=True)
    print(f"Run details: {config.output_dir / 'summary.json'}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
