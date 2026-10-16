from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import tempfile
from collections import defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path

CATEGORIES = {
    "Images": {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg"},
    "Videos": {".mp4", ".mkv", ".mov", ".avi", ".webm"},
    "Documents": {".pdf", ".doc", ".docx", ".txt", ".rtf", ".odt"},
    "Spreadsheets": {".csv", ".xls", ".xlsx", ".ods"},
    "Audio": {".mp3", ".wav", ".flac", ".aac", ".ogg"},
    "Archives": {".zip", ".rar", ".7z", ".tar", ".gz"},
    "Code": {".py", ".js", ".ts", ".html", ".css", ".json", ".yml", ".yaml"},
}


@dataclass(frozen=True)
class FileAction:
    source: str
    destination: str
    category: str
    status: str


def category_for(path: Path) -> str:
    for category, extensions in CATEGORIES.items():
        if path.suffix.lower() in extensions:
            return category
    return "Other"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def plan_organization(source: Path) -> list[FileAction]:
    source = source.resolve()

    if not source.exists():
        raise FileNotFoundError(f"Source directory does not exist: {source}")
    if not source.is_dir():
        raise NotADirectoryError(f"Source is not a directory: {source}")

    actions = []
    destinations = set()

    for path in sorted(source.iterdir(), key=lambda item: item.name.lower()):
        if not path.is_file():
            continue

        category = category_for(path)
        destination = source / category / path.name
        if destination.exists() or destination in destinations:
            status = "skipped_destination_exists"
        else:
            status = "planned"
            destinations.add(destination)

        actions.append(FileAction(str(path), str(destination), category, status))

    return actions


def execute_organization(
    source: Path,
    actions: list[FileAction],
) -> dict[str, object]:
    source = source.resolve()
    report: dict[str, object] = {
        "source": str(source),
        "moved": [],
        "skipped": [],
        "errors": [],
    }

    for action in actions:
        original = Path(action.source)
        destination = Path(action.destination)
        temporary: Path | None = None
        destination_created = False

        try:
            if action.status != "planned":
                report["skipped"].append(
                    {"source": action.source, "reason": action.status}
                )
                continue

            if original.parent != source or not original.is_file():
                report["skipped"].append(
                    {"source": action.source, "reason": "source_changed"}
                )
                continue

            if destination.parent.parent != source:
                report["skipped"].append(
                    {"source": action.source, "reason": "invalid_destination"}
                )
                continue

            destination.parent.mkdir(parents=True, exist_ok=True)

            with tempfile.NamedTemporaryFile(
                mode="wb",
                dir=destination.parent,
                prefix=".organizer-",
                delete=False,
            ) as temp_file:
                temporary = Path(temp_file.name)
                with original.open("rb") as src:
                    shutil.copyfileobj(src, temp_file)
                temp_file.flush()
                os.fsync(temp_file.fileno())

            source_digest = sha256_file(original)
            temporary_digest = sha256_file(temporary)

            if source_digest != temporary_digest:
                report["skipped"].append(
                    {
                        "source": str(original),
                        "reason": "source_changed_during_copy",
                    }
                )
                continue

            try:
                with destination.open("xb") as dst:
                    destination_created = True
                    with temporary.open("rb") as src:
                        shutil.copyfileobj(src, dst)
                    dst.flush()
                    os.fsync(dst.fileno())
            except FileExistsError:
                report["skipped"].append(
                    {"source": action.source, "reason": "destination_exists"}
                )
                continue
            except OSError as copy_error:
                if destination_created:
                    try:
                        destination.unlink()
                    except OSError as cleanup_error:
                        raise OSError(
                            f"{copy_error}; additionally failed to remove "
                            f"partial destination {destination}: {cleanup_error}"
                        ) from copy_error
                raise

            if sha256_file(original) != source_digest:
                report["errors"].append(
                    {
                        "source": str(original),
                        "destination": str(destination),
                        "error": "Source content changed before deletion.",
                        "state": "source_changed_destination_retained",
                    }
                )
                continue

            try:
                original.unlink()
            except OSError as exc:
                report["errors"].append(
                    {
                        "source": str(original),
                        "destination": str(destination),
                        "error": str(exc),
                        "state": "destination_created_source_retained",
                    }
                )
                continue

            report["moved"].append(
                {"source": str(original), "destination": str(destination)}
            )

        except OSError as exc:
            report["errors"].append({"source": action.source, "error": str(exc)})
        finally:
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError as exc:
                    report["errors"].append(
                        {
                            "source": action.source,
                            "temporary": str(temporary),
                            "error": f"Failed to remove temporary file: {exc}",
                        }
                    )

    return report


def find_duplicates(source: Path) -> dict[str, list[str]]:
    source = source.resolve()
    if not source.is_dir():
        raise NotADirectoryError(f"Source is not a directory: {source}")

    by_hash: dict[str, list[str]] = defaultdict(list)
    for path in source.rglob("*"):
        if not path.is_file():
            continue
        try:
            by_hash[sha256_file(path)].append(str(path))
        except OSError:
            continue

    return {digest: paths for digest, paths in by_hash.items() if len(paths) > 1}


def save_report(report: dict[str, object], output: Path) -> None:
    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Preview and organize files safely by file type."
    )
    parser.add_argument("source", type=Path, help="Directory to organize")
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Actually move files; without this flag, only preview the plan",
    )
    parser.add_argument(
        "--report",
        type=Path,
        help="Optional path to write a JSON report",
    )
    parser.add_argument(
        "--duplicates",
        action="store_true",
        help="Include duplicate-content groups in the report",
    )
    args = parser.parse_args(argv)

    try:
        actions = plan_organization(args.source)
        for action in actions:
            print(f"[{action.status}] {action.source} -> {action.destination}")

        if args.execute:
            report = execute_organization(args.source, actions)
        else:
            report = {
                "source": str(args.source.resolve()),
                "dry_run": True,
                "planned": [asdict(item) for item in actions],
                "moved": [],
                "skipped": [],
                "errors": [],
            }
            print("Dry run only. No files were moved. Use --execute to apply.")

        if args.duplicates:
            report["duplicates"] = find_duplicates(args.source)

        if args.report:
            save_report(report, args.report)
            print(f"Report saved: {args.report}")

        return 1 if report["errors"] else 0
    except (OSError, ValueError) as exc:
        print(f"Error: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
