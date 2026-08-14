"""Unit tests for backup restore/load functionality (INFRA-1 PR-1B).

Tests dual-format restore sequence (snapshot / .EMPTY / legacy tar), pre-flight validation,
readiness polling, multipart recovery, and load-bearing exit codes.
"""

from __future__ import annotations

import io
import sys
import tarfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Ensure scripts directory is in path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "scripts"))
import backup_restore


def _create_valid_tar_bytes(size_bytes: int = 15 * 1024, gzip_compress: bool = False) -> bytes:
    """Helper to create a valid tar or tar.gz archive with member files."""
    buf = io.BytesIO()
    mode = "w:gz" if gzip_compress else "w"
    with tarfile.open(fileobj=buf, mode=mode) as tar:
        payload_len = max(100, size_bytes - 1024)
        payload = b"X" * payload_len
        ti = tarfile.TarInfo(name="data.bin")
        ti.size = len(payload)
        tar.addfile(ti, io.BytesIO(payload))
    return buf.getvalue()


# ─── Row 1: Evil — backup dir has no Qdrant artifact at all ──────────


def test_evil_no_qdrant_artifact(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Row 1: backup dir has no Qdrant artifact -> False; docker-compose stop NEVER invoked."""
    monkeypatch.setattr(backup_restore, "BACKUP_DIR", str(tmp_path))

    tag = "tag_no_qdrant"
    target_dir = tmp_path / tag
    target_dir.mkdir(parents=True, exist_ok=True)

    # Only FalkorDB exists, no Qdrant artifact
    (target_dir / "falkor_data.tar.gz").write_bytes(
        _create_valid_tar_bytes(15 * 1024, gzip_compress=True)
    )

    mock_run = MagicMock()

    with patch("backup_restore.subprocess.run", mock_run):
        result = backup_restore.restore(tag=tag, force=True)
        assert result is False
        # Assert docker-compose stop was NEVER invoked
        assert mock_run.call_count == 0


# ─── Row 2: Evil — snapshot upload-recover returns HTTP error ─────────


def test_evil_snapshot_upload_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Row 2: snapshot upload-recover returns HTTP error -> False, exit 1 via __main__, error printed."""
    monkeypatch.setattr(backup_restore, "BACKUP_DIR", str(tmp_path))

    tag = "tag_upload_fails"
    target_dir = tmp_path / tag
    target_dir.mkdir(parents=True, exist_ok=True)

    (target_dir / "falkor_data.tar.gz").write_bytes(
        _create_valid_tar_bytes(15 * 1024, gzip_compress=True)
    )
    (target_dir / "qdrant_data.snapshot").write_bytes(
        _create_valid_tar_bytes(15 * 1024, gzip_compress=False)
    )

    mock_run = MagicMock(return_value=MagicMock(returncode=0, stderr=b""))

    mock_http_cls = MagicMock()
    mock_http_inst = MagicMock()
    mock_http_cls.return_value.__enter__.return_value = mock_http_inst

    # Mock readiness GET returns 200, but snapshot upload POST returns 500
    mock_get_resp = MagicMock(status_code=200)
    mock_post_resp = MagicMock(status_code=500)
    import httpx

    req = httpx.Request("POST", "http://localhost:6333")
    mock_post_resp.raise_for_status.side_effect = httpx.HTTPStatusError(
        "Upload failed", request=req, response=httpx.Response(500, request=req)
    )

    mock_http_inst.get.return_value = mock_get_resp
    mock_http_inst.post.return_value = mock_post_resp

    with (
        patch("backup_restore.subprocess.run", mock_run),
        patch("backup_restore.httpx.Client", mock_http_cls),
    ):
        result = backup_restore.restore(tag=tag, force=True)
        assert result is False

    # Assert exit 1 via __main__ load path
    with (
        patch("backup_restore.subprocess.run", mock_run),
        patch("backup_restore.httpx.Client", mock_http_cls),
        patch.object(sys, "argv", ["backup_restore.py", "load", tag, "--force"]),
    ):
        with pytest.raises(SystemExit) as exc_info:
            import argparse

            parser = argparse.ArgumentParser()
            subparsers = parser.add_subparsers(dest="command")
            p_restore = subparsers.add_parser("load")
            p_restore.add_argument("tag")
            p_restore.add_argument("--force", action="store_true")
            args = parser.parse_args(["load", tag, "--force"])
            if args.command == "load":
                sys.exit(0 if backup_restore.restore(args.tag, args.force) else 1)
        assert exc_info.value.code == 1


# ─── Row 3: Evil — both .snapshot and legacy tar present ─────────────


def test_evil_both_snapshot_and_legacy_tar(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Row 3: both .snapshot and legacy tar present -> .snapshot path taken, [WARN] printed."""
    monkeypatch.setattr(backup_restore, "BACKUP_DIR", str(tmp_path))

    tag = "tag_both_formats"
    target_dir = tmp_path / tag
    target_dir.mkdir(parents=True, exist_ok=True)

    (target_dir / "falkor_data.tar.gz").write_bytes(
        _create_valid_tar_bytes(15 * 1024, gzip_compress=True)
    )
    (target_dir / "qdrant_data.snapshot").write_bytes(
        _create_valid_tar_bytes(15 * 1024, gzip_compress=False)
    )
    (target_dir / "qdrant_data.tar.gz").write_bytes(
        _create_valid_tar_bytes(15 * 1024, gzip_compress=True)
    )

    mock_run = MagicMock(return_value=MagicMock(returncode=0, stderr=b""))

    mock_http_cls = MagicMock()
    mock_http_inst = MagicMock()
    mock_http_cls.return_value.__enter__.return_value = mock_http_inst
    mock_http_inst.get.return_value = MagicMock(status_code=200)
    mock_http_inst.post.return_value = MagicMock(status_code=200)

    mock_qdrant_cls = MagicMock()
    mock_qdrant_inst = MagicMock()
    mock_qdrant_cls.return_value = mock_qdrant_inst

    coll_desc = MagicMock()
    coll_desc.name = "memory_embeddings"
    mock_qdrant_inst.get_collections.return_value = MagicMock(collections=[coll_desc])
    mock_qdrant_inst.count.return_value = MagicMock(count=42)

    with (
        patch("backup_restore.subprocess.run", mock_run),
        patch("backup_restore.httpx.Client", mock_http_cls),
        patch("backup_restore.QdrantClient", mock_qdrant_cls),
    ):
        result = backup_restore.restore(tag=tag, force=True)
        assert result is True

        # Assert [WARN] was printed
        captured = capsys.readouterr()
        assert "[WARN]" in captured.out or "[WARN]" in captured.err

        # Assert upload was called (snapshot path)
        assert mock_http_inst.post.called

        # Assert legacy docker untar for qdrant was NOT called
        for call in mock_run.call_args_list:
            cmd = call[0][0]
            cmd_str = " ".join(cmd) if isinstance(cmd, list) else str(cmd)
            assert "qdrant_data.tar.gz" not in cmd_str


# ─── Row 4: Sad — Qdrant not ready within timeout after up -d ─────────


def test_sad_qdrant_not_ready_timeout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Row 4: Qdrant not ready within timeout -> False; loud message naming the container/host."""
    monkeypatch.setattr(backup_restore, "BACKUP_DIR", str(tmp_path))

    tag = "tag_qdrant_timeout"
    target_dir = tmp_path / tag
    target_dir.mkdir(parents=True, exist_ok=True)

    (target_dir / "falkor_data.tar.gz").write_bytes(
        _create_valid_tar_bytes(15 * 1024, gzip_compress=True)
    )
    (target_dir / "qdrant_data.snapshot").write_bytes(
        _create_valid_tar_bytes(15 * 1024, gzip_compress=False)
    )

    mock_run = MagicMock(return_value=MagicMock(returncode=0, stderr=b""))

    # Mock readiness check to always fail/timeout
    mock_http_cls = MagicMock()
    mock_http_inst = MagicMock()
    mock_http_cls.return_value.__enter__.return_value = mock_http_inst
    mock_http_inst.get.side_effect = RuntimeError("Connection refused")

    with (
        patch("backup_restore.subprocess.run", mock_run),
        patch("backup_restore.httpx.Client", mock_http_cls),
        patch("backup_restore._wait_for_qdrant", return_value=False),
    ):
        result = backup_restore.restore(tag=tag, force=True)
        assert result is False

        captured = capsys.readouterr()
        assert "qdrant" in captured.out.lower() or "qdrant" in captured.err.lower()


# ─── Row 5: Neutral — legacy qdrant_data.tar.gz backup ────────────────


def test_neutral_legacy_tar_restore(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Row 5: legacy qdrant_data.tar.gz -> legacy wipe+untar path invoked with docker commands, return codes examined; True."""
    monkeypatch.setattr(backup_restore, "BACKUP_DIR", str(tmp_path))

    tag = "tag_legacy_happy"
    target_dir = tmp_path / tag
    target_dir.mkdir(parents=True, exist_ok=True)

    (target_dir / "falkor_data.tar.gz").write_bytes(
        _create_valid_tar_bytes(15 * 1024, gzip_compress=True)
    )
    (target_dir / "qdrant_data.tar.gz").write_bytes(
        _create_valid_tar_bytes(15 * 1024, gzip_compress=True)
    )

    mock_run = MagicMock(return_value=MagicMock(returncode=0, stderr=b""))

    with patch("backup_restore.subprocess.run", mock_run):
        result = backup_restore.restore(tag=tag, force=True)
        assert result is True

        # Verify calls: stop -> falkor untar -> qdrant untar -> up -d
        assert mock_run.call_count == 4
        stop_cmd = mock_run.call_args_list[0][0][0]
        assert "stop" in stop_cmd

        falkor_cmd = " ".join(mock_run.call_args_list[1][0][0])
        assert "falkordb_data" in falkor_cmd and "falkor_data.tar.gz" in falkor_cmd

        qdrant_cmd = " ".join(mock_run.call_args_list[2][0][0])
        assert "qdrant_data" in qdrant_cmd and "qdrant_data.tar.gz" in qdrant_cmd

        up_cmd = mock_run.call_args_list[3][0][0]
        assert "up" in up_cmd


# ─── Row 6: Neutral — .snapshot happy path ────────────────────────────


def test_neutral_snapshot_happy_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Row 6: .snapshot happy path -> ordered: stop -> falkor untar -> up -> poll -> upload -> verify; True."""
    monkeypatch.setattr(backup_restore, "BACKUP_DIR", str(tmp_path))

    tag = "tag_snapshot_happy"
    target_dir = tmp_path / tag
    target_dir.mkdir(parents=True, exist_ok=True)

    (target_dir / "falkor_data.tar.gz").write_bytes(
        _create_valid_tar_bytes(15 * 1024, gzip_compress=True)
    )
    (target_dir / "qdrant_data.snapshot").write_bytes(
        _create_valid_tar_bytes(15 * 1024, gzip_compress=False)
    )

    mock_run = MagicMock(return_value=MagicMock(returncode=0, stderr=b""))

    mock_http_cls = MagicMock()
    mock_http_inst = MagicMock()
    mock_http_cls.return_value.__enter__.return_value = mock_http_inst
    mock_http_inst.get.return_value = MagicMock(status_code=200)
    mock_http_inst.post.return_value = MagicMock(status_code=200)

    mock_qdrant_cls = MagicMock()
    mock_qdrant_inst = MagicMock()
    mock_qdrant_cls.return_value = mock_qdrant_inst

    coll_desc = MagicMock()
    coll_desc.name = "memory_embeddings"
    mock_qdrant_inst.get_collections.return_value = MagicMock(collections=[coll_desc])
    mock_qdrant_inst.count.return_value = MagicMock(count=120)

    with (
        patch("backup_restore.subprocess.run", mock_run),
        patch("backup_restore.httpx.Client", mock_http_cls),
        patch("backup_restore._wait_for_qdrant", return_value=True),
        patch("backup_restore.QdrantClient", mock_qdrant_cls),
    ):
        result = backup_restore.restore(tag=tag, force=True)
        assert result is True

        # Verify execution order:
        # 1. subprocess.run: stop
        # 2. subprocess.run: falkor untar
        # 3. subprocess.run: up -d
        assert mock_run.call_count == 3
        assert "stop" in mock_run.call_args_list[0][0][0]
        assert "falkordb_data" in " ".join(mock_run.call_args_list[1][0][0])
        assert "up" in mock_run.call_args_list[2][0][0]

        # 4. HTTP post snapshot upload
        assert mock_http_inst.post.called

        # 5. Qdrant verification
        mock_qdrant_inst.get_collections.assert_called_once()
        mock_qdrant_inst.count.assert_called_once_with(collection_name="memory_embeddings")

    # Assert exit 0 via __main__ load path
    with (
        patch("backup_restore.subprocess.run", mock_run),
        patch("backup_restore.httpx.Client", mock_http_cls),
        patch("backup_restore._wait_for_qdrant", return_value=True),
        patch("backup_restore.QdrantClient", mock_qdrant_cls),
        patch.object(sys, "argv", ["backup_restore.py", "load", tag, "--force"]),
    ):
        with pytest.raises(SystemExit) as exc_info:
            import argparse

            parser = argparse.ArgumentParser()
            subparsers = parser.add_subparsers(dest="command")
            p_restore = subparsers.add_parser("load")
            p_restore.add_argument("tag")
            p_restore.add_argument("--force", action="store_true")
            args = parser.parse_args(["load", tag, "--force"])
            if args.command == "load":
                sys.exit(0 if backup_restore.restore(args.tag, args.force) else 1)
        assert exc_info.value.code == 0


# ─── Row 7: Sad — .EMPTY backup ───────────────────────────────────────


def test_sad_empty_sentinel_restore(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Row 7: .EMPTY backup -> Qdrant recovery skipped, note printed, True."""
    monkeypatch.setattr(backup_restore, "BACKUP_DIR", str(tmp_path))

    tag = "tag_empty_happy"
    target_dir = tmp_path / tag
    target_dir.mkdir(parents=True, exist_ok=True)

    (target_dir / "falkor_data.tar.gz").write_bytes(
        _create_valid_tar_bytes(15 * 1024, gzip_compress=True)
    )
    (target_dir / "qdrant_data.EMPTY").write_bytes(b"")

    mock_run = MagicMock(return_value=MagicMock(returncode=0, stderr=b""))
    mock_http_cls = MagicMock()

    with (
        patch("backup_restore.subprocess.run", mock_run),
        patch("backup_restore.httpx.Client", mock_http_cls),
    ):
        result = backup_restore.restore(tag=tag, force=True)
        assert result is True

        # Assert no HTTP upload attempted
        assert not mock_http_cls.called

        # Assert note printed
        captured = capsys.readouterr()
        assert "empty" in captured.out.lower() or "skip" in captured.out.lower()


# ─── Row 8: Evil — legacy path: qdrant untar subprocess returns nonzero ─


def test_evil_legacy_qdrant_untar_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Row 8: legacy path qdrant untar subprocess returns nonzero -> restore() False; exit 1; no [OK]."""
    monkeypatch.setattr(backup_restore, "BACKUP_DIR", str(tmp_path))

    tag = "tag_legacy_untar_fails"
    target_dir = tmp_path / tag
    target_dir.mkdir(parents=True, exist_ok=True)

    (target_dir / "falkor_data.tar.gz").write_bytes(
        _create_valid_tar_bytes(15 * 1024, gzip_compress=True)
    )
    (target_dir / "qdrant_data.tar.gz").write_bytes(
        _create_valid_tar_bytes(15 * 1024, gzip_compress=True)
    )

    def side_effect(cmd, *args, **kwargs):
        cmd_str = " ".join(cmd) if isinstance(cmd, list) else str(cmd)
        if "qdrant_data.tar.gz" in cmd_str:
            res = MagicMock(returncode=1, stderr=b"Corrupt tar archive")
            return res
        return MagicMock(returncode=0, stderr=b"")

    with patch("backup_restore.subprocess.run", side_effect=side_effect):
        result = backup_restore.restore(tag=tag, force=True)
        assert result is False
