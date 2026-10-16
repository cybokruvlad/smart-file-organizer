from pathlib import Path

import pytest

from src.organizer import (
    category_for,
    execute_organization,
    find_duplicates,
    main,
    plan_organization,
    save_report,
)


def test_category_is_case_insensitive():
    assert category_for(Path("PHOTO.JPG")) == "Images"
    assert category_for(Path("report.PDF")) == "Documents"
    assert category_for(Path("unknown.xyz")) == "Other"


def test_plan_does_not_move_files(tmp_path):
    (tmp_path / "photo.jpg").write_bytes(b"image")
    actions = plan_organization(tmp_path)
    assert len(actions) == 1
    assert actions[0].status == "planned"
    assert (tmp_path / "photo.jpg").exists()
    assert not (tmp_path / "Images").exists()


def test_execute_moves_file_to_category(tmp_path):
    photo = tmp_path / "photo.jpg"
    photo.write_bytes(b"image")
    report = execute_organization(tmp_path, plan_organization(tmp_path))
    assert not photo.exists()
    assert (tmp_path / "Images" / "photo.jpg").read_bytes() == b"image"
    assert len(report["moved"]) == 1
    assert report["errors"] == []


def test_existing_destination_is_never_overwritten(tmp_path):
    (tmp_path / "photo.jpg").write_bytes(b"original")
    target = tmp_path / "Images"
    target.mkdir()
    (target / "photo.jpg").write_bytes(b"existing")
    report = execute_organization(tmp_path, plan_organization(tmp_path))
    assert (target / "photo.jpg").read_bytes() == b"existing"
    assert (tmp_path / "photo.jpg").read_bytes() == b"original"
    assert report["skipped"]


def test_missing_source_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        plan_organization(tmp_path / "missing")


def test_file_source_raises(tmp_path):
    file = tmp_path / "file.txt"
    file.write_text("x", encoding="utf-8")
    with pytest.raises(NotADirectoryError):
        plan_organization(file)


def test_duplicate_detection_uses_file_content(tmp_path):
    (tmp_path / "one.txt").write_text("same content", encoding="utf-8")
    (tmp_path / "two.txt").write_text("same content", encoding="utf-8")
    (tmp_path / "different.txt").write_text("different", encoding="utf-8")
    duplicates = find_duplicates(tmp_path)
    assert len(duplicates) == 1
    assert len(next(iter(duplicates.values()))) == 2


def test_json_report_is_written(tmp_path):
    output = tmp_path / "reports" / "report.json"
    save_report({"moved": [], "errors": []}, output)
    assert '"moved": []' in output.read_text(encoding="utf-8")


def test_cli_defaults_to_dry_run(tmp_path, capsys):
    photo = tmp_path / "photo.jpg"
    photo.write_bytes(b"image")
    result = main([str(tmp_path)])
    assert result == 0
    assert photo.exists()
    assert "Dry run only" in capsys.readouterr().out


def test_cli_execute_moves_files(tmp_path):
    photo = tmp_path / "photo.jpg"
    photo.write_bytes(b"image")
    result = main([str(tmp_path), "--execute"])
    assert result == 0
    assert not photo.exists()
    assert (tmp_path / "Images" / "photo.jpg").exists()


def test_cli_report_and_duplicate_scan(tmp_path):
    (tmp_path / "one.txt").write_text("same", encoding="utf-8")
    (tmp_path / "two.txt").write_text("same", encoding="utf-8")
    output = tmp_path.parent / "organizer-report-test.json"
    try:
        result = main([str(tmp_path), "--report", str(output), "--duplicates"])
        assert result == 0
        assert output.exists()
        assert "duplicates" in output.read_text(encoding="utf-8")
    finally:
        output.unlink(missing_ok=True)


def test_copy_failure_preserves_original_and_cleans_partial_destination(
    tmp_path, monkeypatch
):
    import src.organizer as organizer

    photo = tmp_path / "photo.jpg"
    photo.write_bytes(b"original")
    actions = plan_organization(tmp_path)
    real_copyfileobj = organizer.shutil.copyfileobj
    calls = 0

    def failing_copyfileobj(source, destination, *args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            destination.write(b"partial")
            raise OSError("simulated copy failure")
        return real_copyfileobj(source, destination, *args, **kwargs)

    monkeypatch.setattr(organizer.shutil, "copyfileobj", failing_copyfileobj)

    report = execute_organization(tmp_path, actions)

    assert photo.read_bytes() == b"original"
    assert not (tmp_path / "Images" / "photo.jpg").exists()
    assert len(report["errors"]) == 1
    assert not list((tmp_path / "Images").glob(".organizer-*"))


def test_source_unlink_failure_keeps_source_and_reports_error(tmp_path, monkeypatch):

    photo = tmp_path / "photo.jpg"
    photo.write_bytes(b"original")
    actions = plan_organization(tmp_path)
    real_unlink = Path.unlink

    def failing_unlink(path, *args, **kwargs):
        if path == photo:
            raise OSError("simulated unlink failure")
        return real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", failing_unlink)

    report = execute_organization(tmp_path, actions)

    assert photo.read_bytes() == b"original"
    assert (tmp_path / "Images" / "photo.jpg").read_bytes() == b"original"
    assert len(report["errors"]) == 1


def test_changed_source_is_skipped(tmp_path):
    photo = tmp_path / "photo.jpg"
    photo.write_bytes(b"original")
    actions = plan_organization(tmp_path)

    photo.unlink()

    report = execute_organization(tmp_path, actions)

    assert report["skipped"] == [{"source": str(photo), "reason": "source_changed"}]
    assert report["moved"] == []
    assert report["errors"] == []


def test_destination_outside_source_is_rejected(tmp_path):
    photo = tmp_path / "photo.jpg"
    photo.write_bytes(b"original")
    action = plan_organization(tmp_path)[0]
    outside = tmp_path.parent / "outside" / "photo.jpg"

    from src.organizer import FileAction

    malicious_action = FileAction(
        source=action.source,
        destination=str(outside),
        category=action.category,
        status="planned",
    )

    report = execute_organization(tmp_path, [malicious_action])

    assert photo.read_bytes() == b"original"
    assert not outside.exists()
    assert report["skipped"] == [
        {"source": str(photo), "reason": "invalid_destination"}
    ]
    assert report["errors"] == []


def test_save_report_propagates_write_error(tmp_path, monkeypatch):
    import src.organizer as organizer

    output = tmp_path / "report.json"

    def fail_write(*args, **kwargs):
        raise OSError("simulated write failure")

    monkeypatch.setattr(Path, "write_text", fail_write)

    with pytest.raises(OSError, match="simulated write failure"):
        organizer.save_report({"moved": []}, output)


def test_cli_returns_error_for_missing_directory(tmp_path, capsys):
    missing = tmp_path / "does-not-exist"

    result = main([str(missing)])

    captured = capsys.readouterr()
    assert result == 1
    assert "Error:" in captured.out
    assert "does not exist" in captured.out


def test_cleanup_failure_is_reported(tmp_path, monkeypatch):
    import src.organizer as organizer

    photo = tmp_path / "photo.jpg"
    photo.write_bytes(b"original")
    actions = plan_organization(tmp_path)
    real_copyfileobj = organizer.shutil.copyfileobj
    real_unlink = Path.unlink
    calls = 0

    def failing_copyfileobj(source, destination, *args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            destination.write(b"partial")
            raise OSError("simulated copy failure")
        return real_copyfileobj(source, destination, *args, **kwargs)

    def failing_destination_unlink(path, *args, **kwargs):
        if path == tmp_path / "Images" / "photo.jpg":
            raise OSError("simulated cleanup failure")
        return real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(organizer.shutil, "copyfileobj", failing_copyfileobj)
    monkeypatch.setattr(Path, "unlink", failing_destination_unlink)

    report = execute_organization(tmp_path, actions)

    assert photo.read_bytes() == b"original"
    assert (tmp_path / "Images" / "photo.jpg").read_bytes() == b"partial"
    assert len(report["errors"]) == 1
    assert "simulated cleanup failure" in report["errors"][0]["error"]


def test_temporary_file_cleanup_failure_is_reported(tmp_path, monkeypatch):
    import src.organizer as organizer

    photo = tmp_path / "photo.jpg"
    photo.write_bytes(b"original")
    actions = plan_organization(tmp_path)
    real_unlink = Path.unlink
    temporary_paths = []

    real_named_temporary_file = organizer.tempfile.NamedTemporaryFile

    def tracking_temp_file(*args, **kwargs):
        temp_file = real_named_temporary_file(*args, **kwargs)
        temporary_paths.append(Path(temp_file.name))
        return temp_file

    def failing_temp_unlink(path, *args, **kwargs):
        if path.name.startswith(".organizer-"):
            raise OSError("simulated temporary cleanup failure")
        return real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(organizer.tempfile, "NamedTemporaryFile", tracking_temp_file)
    monkeypatch.setattr(Path, "unlink", failing_temp_unlink)

    report = execute_organization(tmp_path, actions)

    destination = tmp_path / "Images" / "photo.jpg"
    assert not photo.exists()
    assert destination.read_bytes() == b"original"
    assert any(
        "simulated temporary cleanup failure" in item["error"]
        for item in report["errors"]
    )
    assert temporary_paths
    assert temporary_paths[0].exists()
    real_unlink(temporary_paths[0])


def test_source_unlink_failure_reports_retained_source_state(tmp_path, monkeypatch):
    photo = tmp_path / "photo.jpg"
    photo.write_bytes(b"original")
    actions = plan_organization(tmp_path)
    real_unlink = Path.unlink

    def failing_source_unlink(path, *args, **kwargs):
        if path == photo:
            raise OSError("simulated source unlink failure")
        return real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", failing_source_unlink)

    report = execute_organization(tmp_path, actions)

    destination = tmp_path / "Images" / "photo.jpg"
    assert photo.read_bytes() == b"original"
    assert destination.read_bytes() == b"original"
    assert report["moved"] == []
    assert len(report["errors"]) == 1
    assert report["errors"][0]["state"] == "destination_created_source_retained"
    assert report["errors"][0]["destination"] == str(destination)


def test_modified_source_during_copy_is_not_deleted(tmp_path, monkeypatch):
    import src.organizer as organizer

    photo = tmp_path / "photo.jpg"
    photo.write_bytes(b"original")
    actions = plan_organization(tmp_path)
    real_copyfileobj = organizer.shutil.copyfileobj
    calls = 0

    def change_source_during_copy(source, destination, *args, **kwargs):
        nonlocal calls
        calls += 1
        result = real_copyfileobj(source, destination, *args, **kwargs)
        if calls == 1:
            photo.write_bytes(b"changed while organizing")
        return result

    monkeypatch.setattr(organizer.shutil, "copyfileobj", change_source_during_copy)

    report = execute_organization(tmp_path, actions)

    assert photo.exists(), "Changed source must not be deleted"
    assert photo.read_bytes() == b"changed while organizing"
    assert report["moved"] == []


def test_modified_source_after_destination_copy_is_not_deleted(tmp_path, monkeypatch):
    import src.organizer as organizer

    photo = tmp_path / "photo.jpg"
    photo.write_bytes(b"original")
    actions = plan_organization(tmp_path)
    real_copyfileobj = organizer.shutil.copyfileobj
    calls = 0

    def change_source_after_destination_copy(source, destination, *args, **kwargs):
        nonlocal calls
        calls += 1
        result = real_copyfileobj(source, destination, *args, **kwargs)
        if calls == 2:
            photo.write_bytes(b"changed before deletion")
        return result

    monkeypatch.setattr(
        organizer.shutil,
        "copyfileobj",
        change_source_after_destination_copy,
    )

    report = execute_organization(tmp_path, actions)
    destination = tmp_path / "Images" / "photo.jpg"

    assert photo.exists()
    assert photo.read_bytes() == b"changed before deletion"
    assert destination.read_bytes() == b"original"
    assert report["moved"] == []
    assert any(
        error.get("state") == "source_changed_destination_retained"
        for error in report["errors"]
    )
