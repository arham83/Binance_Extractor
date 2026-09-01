#!/usr/bin/env python3
"""Preprocess Binance daily kline ZIP files into clean, analysis-ready CSV files.

Example:
    python preprocess_binance_klines.py --input data/raw/um/BTCUSDT/5m

Output files are written to ./data/pre_processed/splitted by default. Each output row has:
date, coin, interval, readable UTC open_time and readable UTC close_time.
"""

from __future__ import annotations

import argparse
import csv
import re
import shutil
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path


KLINE_COLUMNS = [
    "open_time", "open", "high", "low", "close", "volume", "close_time",
    "quote_volume", "count", "taker_buy_volume", "taker_buy_quote_volume", "ignore",
]
FILENAME_PATTERN = re.compile(
    r"^(?P<coin>.+)-(?P<interval>[^-]+)-(?P<date>\d{4}-\d{2}-\d{2})$"
)


class BinanceKlinePreprocessor:
    """Unzip, enrich, and save Binance kline CSV files one ZIP at a time."""

    def __init__(self, input_folder: Path, output_folder: Path, overwrite: bool = False):
        self.input_folder = input_folder
        self.output_folder = output_folder
        self.overwrite = overwrite

    @staticmethod
    def _metadata_from_filename(csv_name: str) -> dict[str, str]:
        match = FILENAME_PATTERN.match(Path(csv_name).stem)
        if not match:
            raise ValueError(
                f"Cannot read coin, interval, and date from filename: {csv_name}"
            )
        return match.groupdict()

    @staticmethod
    def _format_time(milliseconds: str) -> str:
        """Convert a Binance millisecond Unix timestamp to an ISO UTC time."""
        timestamp = int(float(milliseconds))
        value = datetime.fromtimestamp(timestamp / 1000, tz=timezone.utc)
        return value.strftime("%H:%M:%S.%f")[:-3] + "Z"

    @staticmethod
    def _read_header(reader: csv.reader) -> tuple[list[str], list[str] | None]:
        first_row = next(reader, None)
        if first_row is None:
            raise ValueError("CSV is empty")
        first_row[0] = first_row[0].lstrip("\ufeff")
        if first_row[0].strip().lower() == "open_time":
            return [cell.strip() for cell in first_row], None
        return KLINE_COLUMNS, first_row

    def _process_csv(self, csv_path: Path, output_path: Path) -> int:
        metadata = self._metadata_from_filename(csv_path.name)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        rows_written = 0

        with csv_path.open("r", newline="", encoding="utf-8-sig") as source, output_path.open(
            "w", newline="", encoding="utf-8"
        ) as destination:
            reader = csv.reader(source)
            source_columns, first_data_row = self._read_header(reader)
            if len(source_columns) != len(KLINE_COLUMNS):
                raise ValueError(
                    f"Expected {len(KLINE_COLUMNS)} kline columns in {csv_path.name}, "
                    f"found {len(source_columns)}"
                )

            fieldnames = ["date", "coin", "interval", *source_columns]
            writer = csv.DictWriter(destination, fieldnames=fieldnames)
            writer.writeheader()

            def write_row(values: list[str]) -> None:
                nonlocal rows_written
                if len(values) != len(source_columns):
                    raise ValueError(f"Invalid row with {len(values)} columns in {csv_path.name}")
                row = dict(zip(source_columns, values))
                row["open_time"] = self._format_time(row["open_time"])
                row["close_time"] = self._format_time(row["close_time"])
                writer.writerow({**metadata, **row})
                rows_written += 1

            if first_data_row is not None:
                write_row(first_data_row)
            for data_row in reader:
                if data_row:
                    write_row(data_row)

        return rows_written

    def process_all(self) -> None:
        zip_files = sorted(self.input_folder.rglob("*.zip"))
        if not zip_files:
            raise FileNotFoundError(f"No ZIP files found in: {self.input_folder.resolve()}")

        processed = skipped = 0
        for index, zip_path in enumerate(zip_files, start=1):
            with zipfile.ZipFile(zip_path) as archive:
                csv_members = [name for name in archive.namelist() if name.lower().endswith(".csv")]
                if len(csv_members) != 1:
                    raise ValueError(f"Expected exactly one CSV in {zip_path.name}, found {len(csv_members)}")

                csv_name = Path(csv_members[0]).name
                output_path = self.output_folder / csv_name
                if output_path.exists() and not self.overwrite:
                    print(f"[{index}/{len(zip_files)}] Skipped existing: {output_path.name}")
                    skipped += 1
                    continue

                # Extract only this ZIP's CSV to a temporary folder, process it, then remove it.
                with tempfile.TemporaryDirectory(prefix="binance_kline_") as temp_directory:
                    temporary_csv = Path(temp_directory) / csv_name
                    with archive.open(csv_members[0]) as zipped_csv, temporary_csv.open("wb") as extracted_csv:
                        shutil.copyfileobj(zipped_csv, extracted_csv)
                    row_count = self._process_csv(temporary_csv, output_path)

            print(f"[{index}/{len(zip_files)}] Processed {zip_path.name}: {row_count} rows")
            processed += 1

        print(f"Finished. Processed: {processed}; skipped: {skipped}")
        print(f"Preprocessed files: {self.output_folder.resolve()}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input",
        type=Path,
        default=Path("data/raw"),
        help="folder containing Binance ZIP files (default: data/raw)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/pre_processed/splitted"),
        help="folder for daily preprocessed CSVs (default: data/pre_processed/splitted)",
    )
    parser.add_argument("--overwrite", action="store_true", help="replace existing output CSV files")
    args = parser.parse_args()

    BinanceKlinePreprocessor(args.input, args.output, args.overwrite).process_all()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
