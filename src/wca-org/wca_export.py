"""WCA public results database export (TSV inside ZIP): download and parse."""

from __future__ import annotations

import csv
import io
import logging
import zipfile
from pathlib import Path
from typing import Iterator, Optional

import requests

WCA_EXPORT_PUBLIC = "https://www.worldcubeassociation.org/api/v0/export/public"


def get_export_metadata() -> dict:
    r = requests.get(WCA_EXPORT_PUBLIC, timeout=60)
    r.raise_for_status()
    data = r.json()
    if not isinstance(data, dict):
        raise RuntimeError("export/public returned non-object JSON")
    return data


def download_export_zip(url: str, dest: Path) -> None:
    """Stream download (large ZIP) to ``dest``."""
    logging.info("Downloading WCA export ZIP (this may take a while) …")
    with requests.get(url, stream=True, timeout=600) as r:
        r.raise_for_status()
        dest.parent.mkdir(parents=True, exist_ok=True)
        with dest.open("wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    f.write(chunk)
    logging.info("Wrote %s", dest)


def _find_member(zf: zipfile.ZipFile, suffix: str) -> Optional[str]:
    for name in zf.namelist():
        if name.endswith(suffix) and not name.startswith("__"):
            return name
    return None


def collect_person_ids_for_wcaids(
    zip_path: Path,
    watched_wca_ids: set[str],
) -> set[str]:
    """
    Internal WCA ``person_id`` values (as strings) for the given WCA IDs.

    Streams ``WCA_export_Persons.tsv`` (or same suffix) inside the export ZIP.
    """
    watched = {x.strip().upper() for x in watched_wca_ids if x.strip()}
    if not watched:
        return set()

    out: set[str] = set()
    with zipfile.ZipFile(zip_path, "r") as zf:
        member = _find_member(zf, "Persons.tsv")
        if not member:
            logging.warning("No Persons.tsv in export ZIP")
            return set()
        with zf.open(member) as raw:
            text = io.TextIOWrapper(raw, encoding="utf-8", newline="")
            reader = csv.DictReader(text, delimiter="\t")
            fn = reader.fieldnames or []
            wca_col = "wca_id" if "wca_id" in fn else None
            id_col = "id" if "id" in fn else None
            if not wca_col or not id_col:
                logging.warning("Unexpected Persons.tsv columns: %s", fn)
                return set()
            for row in reader:
                wid = (row.get(wca_col) or "").strip().upper()
                if wid in watched:
                    pid = (row.get(id_col) or "").strip()
                    if pid:
                        out.add(pid)
    logging.info("Matched %d person row(s) for %d WCA ID(s)", len(out), len(watched))
    return out


def load_pid_to_wca_map(zip_path: Path) -> dict[str, str]:
    """Export internal ``person_id`` → WCA id (uppercase)."""
    out: dict[str, str] = {}
    with zipfile.ZipFile(zip_path, "r") as zf:
        member = _find_member(zf, "Persons.tsv")
        if not member:
            return out
        with zf.open(member) as raw:
            text = io.TextIOWrapper(raw, encoding="utf-8", newline="")
            reader = csv.DictReader(text, delimiter="\t")
            fn = reader.fieldnames or []
            wca_col = "wca_id" if "wca_id" in fn else None
            id_col = "id" if "id" in fn else None
            if not wca_col or not id_col:
                return out
            for row in reader:
                wca = (row.get(wca_col) or "").strip().upper()
                pid = (row.get(id_col) or "").strip()
                if wca and pid:
                    out[pid] = wca
    return out


def iter_results_rows(zip_path: Path) -> Iterator[dict[str, str]]:
    """Yield each row of Results.tsv from the export ZIP."""
    with zipfile.ZipFile(zip_path, "r") as zf:
        member = _find_member(zf, "Results.tsv")
        if not member:
            logging.warning("No Results.tsv in export ZIP")
            return
        with zf.open(member) as raw:
            text = io.TextIOWrapper(raw, encoding="utf-8", newline="")
            reader = csv.DictReader(text, delimiter="\t")
            for row in reader:
                yield {k: (v if v is not None else "") for k, v in row.items()}
