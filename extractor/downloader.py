#!/usr/bin/env python3
"""Download checksum-verified Binance futures klines and aggregate trades.

Daily archives come from Binance's public data service; AWS credentials are not
used. Aggregate trades have no candle interval and are grouped during processing.
"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone
import hashlib
import http.client
import json
from pathlib import Path
import re
import shutil
import ssl
import sys
import tempfile
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
import zipfile

import certifi

BASE_URL = "https://data.binance.vision/data/futures/{market}/daily/{dataset}/{symbol}"
SSL_CONTEXT = ssl.create_default_context(cafile=certifi.where())
DATASETS = ("klines", "aggTrades")
INTERVALS = ("1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h", "6h", "8h", "12h", "1d", "3d", "1w", "1mo")


class DownloadError(RuntimeError):
    """A required source could not be downloaded or verified."""


class _TransientDownloadError(DownloadError):
    """A transport or server error for which another request may succeed."""


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


def _retry_delay(attempt: int, error: Exception) -> None:
    wait = min(2 ** (attempt - 1), 30)
    print(f"Retrying in {wait}s ({error})", file=sys.stderr, flush=True)
    time.sleep(wait)


def _fetch(url: str, target: Path, retries: int, max_bytes: int | None = None) -> None:
    """Stream into a private temporary file, replacing the destination atomically."""
    request = Request(url, headers={"User-Agent": "binance-market-data-downloader/2.0"})
    for attempt in range(1, retries + 1):
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=target.parent, prefix=f".{target.name}.", suffix=".part", delete=False) as output:
                temporary = Path(output.name)
                with urlopen(request, timeout=60, context=SSL_CONTEXT) as response:
                    length = response.headers.get("Content-Length")
                    received = 0
                    while chunk := response.read(1024 * 1024):
                        received += len(chunk)
                        if max_bytes is not None and received > max_bytes:
                            raise DownloadError(f"Response exceeds expected size: {url}")
                        output.write(chunk)
                    if length is not None and received != int(length):
                        raise http.client.IncompleteRead(b"", int(length) - received)
            temporary.replace(target)
            return
        except HTTPError as exc:
            if exc.code not in (408, 425, 429, 500, 502, 503, 504):
                raise DownloadError(f"Required source returned HTTP {exc.code}: {url}") from exc
            error = exc
        except (URLError, TimeoutError, ConnectionError, http.client.HTTPException) as exc:
            error = exc
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        if attempt == retries:
            raise _TransientDownloadError(f"Failed after {retries} attempts: {url} ({error})") from error
        _retry_delay(attempt, error)


def _read_checksum(path: Path, filename: str) -> str:
    """Require a SHA-256 entry naming the exact archive we requested."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        raise DownloadError(f"Cannot read checksum for {filename}") from exc
    matches = []
    for line in lines:
        match = re.fullmatch(r"([0-9a-fA-F]{64})\s+\*?(.+)", line.strip())
        if match and match.group(2) == filename:
            matches.append(match.group(1).lower())
    if len(matches) != 1:
        raise DownloadError(f"Checksum must contain exactly one SHA-256 entry for {filename}")
    return matches[0]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _validate_zip(path: Path, expected_csv: str) -> None:
    """Only permit the single, flat CSV member used by the official archives."""
    try:
        with zipfile.ZipFile(path) as archive:
            members = archive.infolist()
            if len(members) != 1 or members[0].filename != expected_csv:
                raise DownloadError(f"Unexpected archive members in {path.name}; expected only {expected_csv}")
            member = members[0]
            if member.is_dir() or member.file_size <= 0 or member.flag_bits & 1:
                raise DownloadError(f"Empty, encrypted or invalid CSV in {path.name}")
            # Reject symbolic links; extraction below also never trusts a member path.
            if (member.external_attr >> 16) & 0o170000 == 0o120000:
                raise DownloadError(f"Symbolic-link archive member in {path.name}")
    except (zipfile.BadZipFile, EOFError, OSError) as exc:
        raise DownloadError(f"Invalid ZIP file: {path.name}") from exc


def _verified(path: Path, expected_sha256: str, expected_csv: str) -> bool:
    if not path.is_file() or _sha256(path) != expected_sha256:
        return False
    try:
        _validate_zip(path, expected_csv)
        return True
    except DownloadError:
        return False


def download(url: str, target: Path, retries: int = 4) -> dict:
    """Fetch the official checksum, then verify an existing or new ZIP.

    A changed upstream checksum causes a replacement download. An unverified
    transfer is never installed at the final archive path.
    """
    if retries < 1:
        raise ValueError("retries must be at least 1")
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    checksum_path = target.with_name(target.name + ".CHECKSUM")
    checksum_url = url + ".CHECKSUM"
    _fetch(checksum_url, checksum_path, retries, max_bytes=65536)
    expected = _read_checksum(checksum_path, target.name)
    expected_csv = target.stem + ".csv"
    status = "skipped"
    if not _verified(target, expected, expected_csv):
        status = "downloaded"
        # This staging name keeps the previous file intact until the replacement
        # has passed the published SHA-256 and archive structure checks.
        with tempfile.NamedTemporaryFile(dir=target.parent, prefix=f".{target.name}.", suffix=".verify", delete=False) as staging:
            staged_path = Path(staging.name)
        try:
            for attempt in range(1, retries + 1):
                # One archive request per attempt: transport and verification
                # failures share a budget instead of nesting two retry loops.
                try:
                    _fetch(url, staged_path, retries=1)
                except _TransientDownloadError as exc:
                    error = exc
                else:
                    try:
                        if _sha256(staged_path) != expected:
                            raise DownloadError(f"SHA-256 mismatch: {target.name}")
                        _validate_zip(staged_path, expected_csv)
                    except DownloadError as exc:
                        error = exc
                    else:
                        staged_path.replace(target)
                        break
                if attempt == retries:
                    raise DownloadError(f"Failed after {retries} archive attempts: {target.name} ({error})") from error
                _retry_delay(attempt, error)
        finally:
            staged_path.unlink(missing_ok=True)
    return {
        "path": str(target.resolve()),
        "url": url,
        "checksum_url": checksum_url,
        "checksum_path": str(checksum_path.resolve()),
        "sha256": expected,
        "size_bytes": target.stat().st_size,
        "status": status,
    }


def _write_json_atomic(path: Path, value: dict) -> None:
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.", suffix=".part", delete=False) as stream:
        temporary = Path(stream.name)
        try:
            json.dump(value, stream, indent=2)
            stream.write("\n")
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
    try:
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def download_range(
    market: str,
    symbol: str,
    interval: str,
    start: date,
    end: date,
    output: Path,
    retries: int = 4,
    datasets=("klines", "aggTrades"),
    workers: int = 4,
) -> dict:
    """Download all selected daily archives or raise on incomplete coverage.

    Both dates are inclusive UTC dates. Returns the persisted manifest plus its
    path. Each object has dataset/date/path/url/sha256/size_bytes/status fields.
    """
    datasets = tuple(datasets)
    if market not in ("cm", "um"):
        raise ValueError("market must be cm or um")
    if not re.fullmatch(r"[A-Z0-9_]+", symbol):
        raise ValueError("symbol must contain only uppercase letters, digits and underscores")
    if interval not in INTERVALS:
        raise ValueError(f"Unsupported interval: {interval}")
    if start > end:
        raise ValueError("start must be on or before end")
    if retries < 1 or workers < 1 or workers > 4:
        raise ValueError("retries must be at least 1; workers must be between 1 and 4")
    if not datasets or len(set(datasets)) != len(datasets) or any(item not in DATASETS for item in datasets):
        raise ValueError("datasets must select unique values from klines and aggTrades")
    destination = Path(output) / market / symbol
    destination.mkdir(parents=True, exist_ok=True)
    dataset_label = "-".join(item for item in DATASETS if item in datasets)
    manifest_path = destination / f"download-manifest-{interval}-{start.isoformat()}-{end.isoformat()}-{dataset_label}.json"
    requests = []
    for offset in range((end - start).days + 1):
        current = start + timedelta(days=offset)
        for dataset in datasets:
            suffix = interval if dataset == "klines" else "aggTrades"
            filename = f"{symbol}-{suffix}-{current.isoformat()}.zip"
            base = BASE_URL.format(market=market, dataset=dataset, symbol=symbol)
            if dataset == "klines":
                base += f"/{interval}"
            requests.append((dataset, current.isoformat(), f"{base}/{filename}", destination / suffix / filename))
    manifest = {
        "source": "Binance Public Data",
        "market": market,
        "symbol": symbol,
        "interval": interval,
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "datasets": list(datasets),
        "status": "incomplete",
        "expected_objects": len(requests),
        "objects": [],
        "errors": [],
        "manifest_path": str(manifest_path.resolve()),
    }
    _write_json_atomic(manifest_path, manifest)
    print(f"Downloading/verifying {len(requests)} daily archives: {market}/{symbol}, datasets={', '.join(datasets)}, candle interval={interval}", flush=True)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(download, url, path, retries): (dataset, day, url)
            for dataset, day, url, path in requests
        }
        for count, future in enumerate(as_completed(futures), start=1):
            dataset, day, url = futures[future]
            try:
                result = future.result()
                result.update(dataset=dataset, date=day)
                manifest["objects"].append(result)
                print(f"[{count}/{len(requests)}] {result['status']}: {Path(result['path']).name}", flush=True)
            except Exception as exc:
                manifest["errors"].append({"dataset": dataset, "date": day, "url": url, "error": str(exc)})
                print(f"[{count}/{len(requests)}] ERROR: {dataset}/{day}: {exc}", file=sys.stderr, flush=True)
            # Persist progress so an interrupted run cannot leave an old manifest
            # suggesting the newly requested range is already complete.
            _write_json_atomic(manifest_path, manifest)
    manifest["objects"].sort(key=lambda item: (item["date"], item["dataset"]))
    manifest["downloaded"] = sum(item["status"] == "downloaded" for item in manifest["objects"])
    manifest["skipped"] = sum(item["status"] == "skipped" for item in manifest["objects"])
    manifest["total_bytes"] = sum(item["size_bytes"] for item in manifest["objects"])
    manifest["checked_at_utc"] = datetime.now(timezone.utc).isoformat()
    manifest["status"] = "incomplete" if manifest["errors"] else "complete"
    _write_json_atomic(manifest_path, manifest)
    if manifest["errors"]:
        raise DownloadError(f"{len(manifest['errors'])} required archive(s) failed. See {manifest_path}; rerun to retry missing/corrupt files.")
    print(f"Finished. Downloaded: {manifest['downloaded']}; verified existing: {manifest['skipped']}. Manifest: {manifest_path}", flush=True)
    return manifest


def _extract_csv(path: Path) -> None:
    expected_csv = path.stem + ".csv"
    _validate_zip(path, expected_csv)
    target = path.with_suffix(".csv")
    with tempfile.NamedTemporaryFile(dir=target.parent, prefix=f".{target.name}.", suffix=".part", delete=False) as output:
        temporary = Path(output.name)
        try:
            with zipfile.ZipFile(path) as archive, archive.open(expected_csv) as source:
                shutil.copyfileobj(source, output, length=1024 * 1024)
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
    try:
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)


def main() -> int:
    utc_today = datetime.now(timezone.utc).date()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--market", choices=("cm", "um"), default="um")
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--interval", choices=INTERVALS, default="1h")
    parser.add_argument("--datasets", nargs="+", choices=DATASETS, default=list(DATASETS))
    parser.add_argument("--start", type=parse_date, default=five_years_ago(utc_today))
    parser.add_argument("--end", type=parse_date, default=utc_today - timedelta(days=1), help="inclusive; defaults to yesterday UTC")
    parser.add_argument("--output", type=Path, default=Path("data/raw"))
    parser.add_argument("--retries", type=int, default=4)
    parser.add_argument("--workers", type=int, default=4, help="parallel downloads, between 1 and 4")
    parser.add_argument("--extract", action="store_true", help="also write each CSV beside its verified ZIP")
    args = parser.parse_args()
    try:
        manifest = download_range(args.market, args.symbol, args.interval, args.start, args.end, args.output, args.retries, args.datasets, args.workers)
        if args.extract:
            for item in manifest["objects"]:
                _extract_csv(Path(item["path"]))
    except (ValueError, DownloadError) as exc:
        parser.exit(1, f"Error: {exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
