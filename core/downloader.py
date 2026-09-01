#!/usr/bin/env python3
"""Download Binance Vision daily 5-minute BTC perpetual-futures klines.

Default market: Coin-Margined BTCUSD_PERP (futures/cm).
Use --market um --symbol BTCUSDT for USDⓈ-Margined data instead.
"""

from __future__ import annotations

import argparse
import ssl
import sys
import time
import zipfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import certifi

BASE_URL = "https://data.binance.vision/data/futures/{market}/daily/klines/{symbol}/{interval}"
SSL_CONTEXT = ssl.create_default_context(cafile=certifi.where())


def parse_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("dates must be YYYY-MM-DD") from exc


def five_years_ago(today: date) -> date:
    try:
        return today.replace(year=today.year - 5)
    except ValueError:  # February 29
        return today.replace(year=today.year - 5, day=28)


def download(url: str, target: Path, retries: int) -> None:
    temporary = target.with_suffix(target.suffix + ".part")
    request = Request(url, headers={"User-Agent": "binance-klines-downloader/1.0"})

    for attempt in range(1, retries + 1):
        try:
            with urlopen(
                request,
                timeout=60,
                context=SSL_CONTEXT,
            ) as response, temporary.open("wb") as output:
                while chunk := response.read(1024 * 1024):
                    output.write(chunk)

            if not zipfile.is_zipfile(temporary):
                raise RuntimeError("download is not a valid ZIP file")

            temporary.replace(target)
            return

        except HTTPError as exc:
            temporary.unlink(missing_ok=True)

            if exc.code == 404:
                print(f"Missing: {url}", file=sys.stderr)
                return

            error = f"HTTP {exc.code}"

        except (URLError, TimeoutError, RuntimeError) as exc:
            temporary.unlink(missing_ok=True)
            error = str(exc)

        if attempt == retries:
            raise RuntimeError(
                f"Failed after {retries} attempts: {url} ({error})"
            )

        wait = min(2 ** (attempt - 1), 30)
        print(f"Retrying in {wait}s ({error})", file=sys.stderr)
        time.sleep(wait)


def main() -> int:
    utc_today = datetime.now(timezone.utc).date()

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--market", choices=("cm", "um"), default="cm")
    parser.add_argument("--symbol", default="BTCUSD_PERP")
    parser.add_argument("--interval", default="5m")
    parser.add_argument(
        "--start",
        type=parse_date,
        default=five_years_ago(utc_today),
    )
    parser.add_argument(
        "--end",
        type=parse_date,
        default=utc_today - timedelta(days=1),
        help="inclusive; defaults to yesterday UTC",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/raw"),
        help="root folder for downloaded ZIPs (default: data/raw)",
    )
    parser.add_argument("--retries", type=int, default=4)
    parser.add_argument(
        "--extract",
        action="store_true",
        help="also extract each CSV beside its ZIP",
    )

    args = parser.parse_args()

    if args.start > args.end:
        parser.error("--start must be on or before --end")

    if args.retries < 1:
        parser.error("--retries must be at least 1")

    destination = args.output / args.market / args.symbol / args.interval
    destination.mkdir(parents=True, exist_ok=True)

    total = (args.end - args.start).days + 1
    print(f"Downloading {total} daily files: {args.market}/{args.symbol}/{args.interval}")

    current = args.start
    completed = skipped = 0

    while current <= args.end:
        filename = f"{args.symbol}-{args.interval}-{current.isoformat()}.zip"
        target = destination / filename

        if target.exists() and zipfile.is_zipfile(target):
            skipped += 1
        else:
            url = BASE_URL.format(
                market=args.market,
                symbol=args.symbol,
                interval=args.interval,
            )

            print(f"[{completed + skipped + 1}/{total}] {filename}")
            download(f"{url}/{filename}", target, args.retries)
            completed += 1

        if args.extract and target.exists() and zipfile.is_zipfile(target):
            with zipfile.ZipFile(target) as archive:
                archive.extractall(destination)

        current += timedelta(days=1)

    print(f"Finished. Downloaded: {completed}; already present: {skipped}")
    print(f"Files are in: {destination.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
