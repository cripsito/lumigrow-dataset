#!/usr/bin/env python3
"""Build self-contained lot folders without modifying or moving crop images.

The device-wide CSV/JSONL files remain in place for existing consumers. Per-lot
copies are reconstructed from every Git revision so an older lot is not omitted
just because a later device export replaced a global manifest.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import subprocess
from collections import OrderedDict
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath


ROOT = Path(__file__).resolve().parents[1]
DEVICES_DIR = ROOT / "devices"
CSV_FILES = ("observations.csv", "crop_observations.csv")
JSONL_FILES = ("vertex_manifest.jsonl", "vertex_manifest.metadata.jsonl")


def git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=ROOT, check=True, capture_output=True, text=True
    ).stdout


def historical_blobs(relative_path: str):
    commits = [line for line in git("log", "--reverse", "--format=%H", "--", relative_path).splitlines() if line]
    for commit in commits:
        result = subprocess.run(
            ["git", "show", f"{commit}:{relative_path}"],
            cwd=ROOT,
            capture_output=True,
        )
        if result.returncode == 0:
            yield commit, result.stdout.decode("utf-8-sig")


def merge_csv_history(relative_path: str, key_fields: tuple[str, ...]):
    headers: list[str] = []
    rows: OrderedDict[tuple[str, ...], dict[str, str]] = OrderedDict()
    commits: list[str] = []
    for commit, text in historical_blobs(relative_path):
        commits.append(commit)
        reader = csv.DictReader(io.StringIO(text))
        for field in reader.fieldnames or []:
            if field not in headers:
                headers.append(field)
        for row in reader:
            key = tuple(row.get(field, "") for field in key_fields)
            if not any(key):
                continue
            rows[key] = dict(row)
    return headers, list(rows.values()), commits


def merge_jsonl_history(relative_path: str):
    entries: OrderedDict[str, dict] = OrderedDict()
    commits: list[str] = []
    for commit, text in historical_blobs(relative_path):
        commits.append(commit)
        for raw_line in text.splitlines():
            if not raw_line.strip():
                continue
            entry = json.loads(raw_line)
            uri = entry.get("imageGcsUri")
            if uri:
                entries[uri] = entry
    return entries, commits


def lot_from_manifest(entry: dict) -> str | None:
    meta_lot = entry.get("_meta", {}).get("lot_id")
    if meta_lot:
        return meta_lot
    parts = PurePosixPath(entry.get("imageGcsUri", "")).parts
    for part in parts:
        if part.startswith("LOT-"):
            return part
    return None


def portable_path(value: str) -> str:
    return f"crops/{PurePosixPath(value).name}" if value else ""


def write_csv(path: Path, headers: list[str], rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=headers,
            extrasaction="ignore",
            lineterminator="\n",
        )
        writer.writeheader()
        writer.writerows(rows)


def write_jsonl(path: Path, entries: list[dict]) -> None:
    text = "\n".join(json.dumps(entry, ensure_ascii=False, separators=(",", ":")) for entry in entries)
    path.write_text(f"{text}\n" if text else "", encoding="utf-8")


def derive_dataset(device_id: str, lot_id: str, lot_dir: Path, observations: list[dict], crop_rows: list[dict]) -> dict:
    source = (crop_rows or observations or [{}])[0]
    row_values = [int(row["row_index"]) for row in crop_rows if row.get("row_index", "").isdigit()]
    col_values = [int(row["col_index"]) for row in crop_rows if row.get("col_index", "").isdigit()]
    return {
        "schema_version": 3,
        "device_id": device_id,
        "lot_id": lot_id,
        "species": source.get("species") or None,
        "variety": source.get("variety") or None,
        "rows": max(row_values) + 1 if row_values else None,
        "cols": max(col_values) + 1 if col_values else None,
        "started_at": source.get("lot_started_at") or None,
        "status": "unknown",
        "crops_total": len(list((lot_dir / "crops").glob("*.jpg"))),
        "reconstructed_from_repository_history": True,
    }


def read_csv_rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def validate_portable_tree(report: dict) -> dict:
    errors: list[str] = []
    totals = {"lots": 0, "observations": 0, "crop_observations": 0, "manifest_entries": 0}
    required = (
        "LEEME.txt",
        "dataset.json",
        "observations.csv",
        "crop_observations.csv",
        "vertex_manifest.jsonl",
        "vertex_manifest.metadata.jsonl",
    )
    for device_id, device_report in report["devices"].items():
        for lot_id, expected in device_report["lots"].items():
            lot_dir = DEVICES_DIR / device_id / lot_id
            for filename in required:
                if not (lot_dir / filename).is_file():
                    errors.append(f"missing required file: {lot_dir.relative_to(ROOT) / filename}")
            if errors and not lot_dir.is_dir():
                continue

            observations = read_csv_rows(lot_dir / "observations.csv")
            crop_rows = read_csv_rows(lot_dir / "crop_observations.csv")
            manifest = read_jsonl(lot_dir / "vertex_manifest.jsonl")
            metadata_manifest = read_jsonl(lot_dir / "vertex_manifest.metadata.jsonl")
            dataset = json.loads((lot_dir / "dataset.json").read_text(encoding="utf-8"))

            actual = {
                "observations": len(observations),
                "crop_observations": len(crop_rows),
                "manifest_entries": len(manifest),
            }
            for key, count in actual.items():
                if count != expected[key]:
                    errors.append(f"{device_id}/{lot_id}: {key} expected {expected[key]}, got {count}")
            if len(metadata_manifest) != len(manifest):
                errors.append(f"{device_id}/{lot_id}: plain/metadata manifest counts differ")
            if dataset.get("lot_id") != lot_id or dataset.get("device_id") != device_id:
                errors.append(f"{device_id}/{lot_id}: dataset.json identity does not match its folder")

            crop_keys = [(row.get("plant_key"), row.get("capture_group")) for row in crop_rows]
            if len(crop_keys) != len(set(crop_keys)):
                errors.append(f"{device_id}/{lot_id}: duplicate (plant_key, capture_group)")
            manifest_uris = [entry.get("imageGcsUri") for entry in manifest]
            if len(manifest_uris) != len(set(manifest_uris)):
                errors.append(f"{device_id}/{lot_id}: duplicate manifest image URI")

            for row in crop_rows:
                for field in ("ambient_crop_path", "flash_crop_path", "crop_path"):
                    relative = row.get(field, "")
                    if relative and not (lot_dir / relative).is_file():
                        errors.append(f"{device_id}/{lot_id}: missing {field} target {relative}")
            for uri in manifest_uris:
                relative = uri.removeprefix("./") if uri else ""
                if relative and not (lot_dir / relative).is_file():
                    errors.append(f"{device_id}/{lot_id}: missing manifest target {relative}")

            totals["lots"] += 1
            totals["observations"] += len(observations)
            totals["crop_observations"] += len(crop_rows)
            totals["manifest_entries"] += len(manifest)

    if totals != report["totals"]:
        errors.append(f"portable totals differ: expected {report['totals']}, got {totals}")
    return {"passed": not errors, "errors": errors[:100], "totals": totals}


def migrate(apply: bool) -> dict:
    root_index_path = ROOT / "dataset_index.json"
    root_index = json.loads(root_index_path.read_text(encoding="utf-8")) if root_index_path.exists() else {}
    existing_devices = root_index.get("devices", {})
    report = {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mode": "apply" if apply else "check",
        "images_before": len(list(DEVICES_DIR.glob("*/*/crops/*.jpg"))),
        "devices": {},
        "totals": {"lots": 0, "observations": 0, "crop_observations": 0, "manifest_entries": 0},
        "missing_crop_references": [],
    }
    rebuilt_devices: dict[str, dict] = {}

    for device_dir in sorted(path for path in DEVICES_DIR.iterdir() if path.is_dir()):
        device_id = device_dir.name
        prefix = f"devices/{device_id}"
        observation_headers, observations, observation_commits = merge_csv_history(
            f"{prefix}/observations.csv", ("lot_id", "observation_id")
        )
        crop_headers, crop_rows, crop_commits = merge_csv_history(
            f"{prefix}/crop_observations.csv", ("plant_key", "capture_group")
        )
        plain_manifest, plain_commits = merge_jsonl_history(f"{prefix}/vertex_manifest.jsonl")
        metadata_manifest, metadata_commits = merge_jsonl_history(f"{prefix}/vertex_manifest.metadata.jsonl")

        lot_ids = {
            path.name for path in device_dir.iterdir()
            if path.is_dir() and path.name.startswith("LOT-")
        }
        lot_ids.update(row.get("lot_id", "") for row in observations)
        lot_ids.update(row.get("lot_id", "") for row in crop_rows)
        lot_ids.update(filter(None, (lot_from_manifest(entry) for entry in plain_manifest.values())))
        lot_ids.update(filter(None, (lot_from_manifest(entry) for entry in metadata_manifest.values())))
        lot_ids.discard("")

        device_report = {
            "lots": {},
            "history_revisions": {
                "observations_csv": len(observation_commits),
                "crop_observations_csv": len(crop_commits),
                "vertex_manifest": len(plain_commits),
                "vertex_manifest_metadata": len(metadata_commits),
            },
        }

        for lot_id in sorted(lot_ids):
            lot_dir = device_dir / lot_id
            lot_observations = [dict(row) for row in observations if row.get("lot_id") == lot_id]
            lot_crop_rows = [dict(row) for row in crop_rows if row.get("lot_id") == lot_id]
            for row in lot_observations:
                if "crop_path" in row:
                    row["crop_path"] = portable_path(row.get("crop_path", ""))
            for row in lot_crop_rows:
                for field in ("ambient_crop_path", "flash_crop_path", "crop_path"):
                    if field in row:
                        row[field] = portable_path(row.get(field, ""))

            merged_manifest = OrderedDict(plain_manifest)
            merged_manifest.update(metadata_manifest)
            lot_manifest_entries = [entry for entry in merged_manifest.values() if lot_from_manifest(entry) == lot_id]
            lot_metadata_entries = []
            lot_plain_entries = []
            for entry in lot_manifest_entries:
                portable_entry = json.loads(json.dumps(entry))
                portable_entry["imageGcsUri"] = f"./crops/{PurePosixPath(entry['imageGcsUri']).name}"
                lot_metadata_entries.append(portable_entry)
                lot_plain_entries.append({key: value for key, value in portable_entry.items() if key != "_meta"})
                crop_file = lot_dir / "crops" / PurePosixPath(entry["imageGcsUri"]).name
                if not crop_file.exists():
                    report["missing_crop_references"].append(str(crop_file.relative_to(ROOT)))

            crops_count = len(list((lot_dir / "crops").glob("*.jpg")))
            if apply:
                lot_dir.mkdir(parents=True, exist_ok=True)
                write_csv(lot_dir / "observations.csv", observation_headers, lot_observations)
                write_csv(lot_dir / "crop_observations.csv", crop_headers, lot_crop_rows)
                write_jsonl(lot_dir / "vertex_manifest.jsonl", lot_plain_entries)
                write_jsonl(lot_dir / "vertex_manifest.metadata.jsonl", lot_metadata_entries)
                dataset_path = lot_dir / "dataset.json"
                if not dataset_path.exists():
                    dataset_path.write_text(
                        json.dumps(derive_dataset(device_id, lot_id, lot_dir, lot_observations, lot_crop_rows), indent=2, ensure_ascii=False) + "\n",
                        encoding="utf-8",
                    )
                (lot_dir / "LEEME.txt").write_text(
                    "\n".join([
                        f"DATOS DEL LOTE {lot_id}",
                        "",
                        "Esta carpeta es autocontenida y puede copiarse completa a una memoria USB.",
                        "No se necesita Git para consultar los CSV ni las imágenes.",
                        "",
                        "Contenido:",
                        "- dataset.json: identidad y configuración disponible del lote.",
                        "- observations.csv: observaciones ambientales de este lote.",
                        "- crop_observations.csv: registros por planta y captura.",
                        "- crops/: recortes disponibles de las plantas.",
                        "- vertex_manifest*.jsonl: etiquetas disponibles para entrenamiento.",
                        "",
                    ]),
                    encoding="utf-8",
                )

            device_report["lots"][lot_id] = {
                "observations": len(lot_observations),
                "crop_observations": len(lot_crop_rows),
                "manifest_entries": len(lot_plain_entries),
                "crops": crops_count,
            }
            report["totals"]["lots"] += 1
            report["totals"]["observations"] += len(lot_observations)
            report["totals"]["crop_observations"] += len(lot_crop_rows)
            report["totals"]["manifest_entries"] += len(lot_plain_entries)

        report["devices"][device_id] = device_report
        prior = existing_devices.get(device_id, {})
        rebuilt_devices[device_id] = {
            **prior,
            "lots": sorted(lot_ids),
            "observations_csv": f"{prefix}/observations.csv",
            "crop_observations_csv": f"{prefix}/crop_observations.csv",
            "vertex_manifest": f"{prefix}/vertex_manifest.jsonl",
            "per_lot_schema_version": 3,
        }

    report["images_after"] = len(list(DEVICES_DIR.glob("*/*/crops/*.jpg")))
    report["images_unchanged"] = report["images_before"] == report["images_after"]
    report["missing_crop_references"] = sorted(set(report["missing_crop_references"]))

    if apply:
        root_index.update({
            "schema_version": max(3, int(root_index.get("schema_version", 0))),
            "layout": "devices/<deviceId>/<lotId>/{dataset.json,observations.csv,crop_observations.csv,vertex_manifest*.jsonl,crops}",
            "last_migration_at": report["generated_at"],
            "devices": rebuilt_devices,
        })
        root_index_path.write_text(json.dumps(root_index, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        report["validation"] = validate_portable_tree(report)
        (ROOT / "MIGRATION_REPORT.json").write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="write the portable per-lot files")
    args = parser.parse_args()
    report = migrate(args.apply)
    print(json.dumps(report, indent=2, ensure_ascii=False))
    if report["missing_crop_references"]:
        raise SystemExit(2)
    if not report["images_unchanged"]:
        raise SystemExit(3)
    if args.apply and not report.get("validation", {}).get("passed"):
        raise SystemExit(4)


if __name__ == "__main__":
    main()
