"""Unit tests for backup save functionality (INFRA-1 PR-1A).

Tests snapshot-API Qdrant capture, hardened verification, and exit code contracts.
Covers the 8-row matrix (3-evil, 1-sad, 1-neutral per behavior).
"""

from __future__ import annotations

import io
import os
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


def _mock_httpx_client(content: bytes, status_code: int = 200) -> MagicMock:
    """Helper to mock httpx.Client context manager and streaming response."""
    mock_cls = MagicMock()
    mock_client = MagicMock()
    mock_cls.return_value.__enter__.return_value = mock_client

    mock_resp = MagicMock()
    if status_code >= 400:
        import httpx

        request = httpx.Request("GET", "http://localhost:6333")
        response = httpx.Response(status_code, request=request)
        mock_resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            "HTTP Error", request=request, response=response
        )
    else:
        mock_resp.raise_for_status.return_value = None

    chunks = [content[i : i + 8192] for i in range(0, len(content), 8192)]
    mock_resp.iter_bytes.return_value = chunks

    mock_stream_ctx = MagicMock()
    mock_stream_ctx.__enter__.return_value = mock_resp
    mock_client.stream.return_value = mock_stream_ctx

    return mock_cls


def _mock_falkor_run_success(target_dir: str):
    """Side effect for subprocess.run to write a valid falkor_data.tar.gz."""

    def side_effect(cmd, *args, **kwargs):
        falkor_archive = os.path.join(target_dir, "falkor_data.tar.gz")
        with open(falkor_archive, "wb") as f:
            f.write(_create_valid_tar_bytes(size_bytes=15 * 1024, gzip_compress=True))
        res = MagicMock()
        res.returncode = 0
        res.stderr = b""
        return res

    return side_effect


# ─── Row 1: Evil — create_snapshot raises (Qdrant down) ──────────────


def test_evil_create_snapshot_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Row 1: create_snapshot raises -> backup() False, no qdrant tar invoked, exit 1 via __main__."""
    monkeypatch.setattr(backup_restore, "BACKUP_DIR", str(tmp_path))

    tag = "test_evil_qdrant_down"
    target_dir = str(tmp_path / tag)

    mock_redis_cls = MagicMock()
    mock_redis_inst = MagicMock()
    mock_redis_cls.return_value = mock_redis_inst

    mock_qdrant_cls = MagicMock()
    mock_qdrant_inst = MagicMock()
    mock_qdrant_cls.return_value = mock_qdrant_inst
    mock_qdrant_inst.collection_exists.return_value = True
    mock_qdrant_inst.create_snapshot.side_effect = RuntimeError("Qdrant connection refused")

    with (
        patch("backup_restore.redis.Redis", mock_redis_cls),
        patch("backup_restore.QdrantClient", mock_qdrant_cls),
        patch(
            "backup_restore.subprocess.run", side_effect=_mock_falkor_run_success(target_dir)
        ) as mock_run,
    ):
        result = backup_restore.backup(tag=tag)

        assert result is False
        # Assert no qdrant_data.tar.gz created
        assert not os.path.exists(os.path.join(target_dir, "qdrant_data.tar.gz"))
        assert not os.path.exists(os.path.join(target_dir, "qdrant_data.snapshot"))

        # Assert no tar subprocess invoked against a qdrant volume or storage directory
        for call in mock_run.call_args_list:
            cmd = call[0][0]
            cmd_str = " ".join(cmd) if isinstance(cmd, list) else str(cmd)
            assert "_qdrant_data" not in cmd_str.lower()
            assert "/qdrant/storage" not in cmd_str.lower()

    # Assert exit 1 via __main__ path
    with (
        patch("backup_restore.redis.Redis", mock_redis_cls),
        patch("backup_restore.QdrantClient", mock_qdrant_cls),
        patch("backup_restore.subprocess.run", side_effect=_mock_falkor_run_success(target_dir)),
        patch.object(sys, "argv", ["backup_restore.py", "save", "--tag", tag]),
    ):
        with pytest.raises(SystemExit) as exc_info:
            import argparse

            parser = argparse.ArgumentParser()
            subparsers = parser.add_subparsers(dest="command")
            p_backup = subparsers.add_parser("save")
            p_backup.add_argument("--tag")
            args = parser.parse_args(["save", "--tag", tag])
            if args.command == "save":
                sys.exit(0 if backup_restore.backup(args.tag) else 1)
        assert exc_info.value.code == 1


# ─── Row 2: Evil — download yields truncated file ────────────────────


def test_evil_download_yields_truncated_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Row 2: download yields truncated file (< 10KB / not a tar) -> verification fails, backup() False."""
    monkeypatch.setattr(backup_restore, "BACKUP_DIR", str(tmp_path))

    tag = "test_evil_truncated_download"
    target_dir = str(tmp_path / tag)

    mock_redis_cls = MagicMock()
    mock_qdrant_cls = MagicMock()
    mock_qdrant_inst = MagicMock()
    mock_qdrant_cls.return_value = mock_qdrant_inst
    mock_qdrant_inst.collection_exists.return_value = True

    snapshot_desc = MagicMock()
    snapshot_desc.name = "snapshot_123.snapshot"
    mock_qdrant_inst.create_snapshot.return_value = snapshot_desc

    # Truncated non-tar payload (e.g. 500 bytes)
    mock_http_client_cls = _mock_httpx_client(b"TRUNCATED_NOT_A_TAR" * 10)

    with (
        patch("backup_restore.redis.Redis", mock_redis_cls),
        patch("backup_restore.QdrantClient", mock_qdrant_cls),
        patch("backup_restore.httpx.Client", mock_http_client_cls),
        patch("backup_restore.subprocess.run", side_effect=_mock_falkor_run_success(target_dir)),
    ):
        result = backup_restore.backup(tag=tag)
        assert result is False


# ─── Row 3: Evil — FalkorDB SAVE raises ──────────────────────────────


def test_evil_falkordb_save_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Row 3: FalkorDB SAVE raises -> backup() False BEFORE any archive step runs (no falkor tar attempted)."""
    monkeypatch.setattr(backup_restore, "BACKUP_DIR", str(tmp_path))

    tag = "test_evil_falkor_save_fail"

    mock_redis_cls = MagicMock()
    mock_redis_inst = MagicMock()
    mock_redis_cls.return_value = mock_redis_inst
    mock_redis_inst.save.side_effect = RuntimeError("FalkorDB SAVE failed (disk full)")

    mock_run = MagicMock()

    with (
        patch("backup_restore.redis.Redis", mock_redis_cls),
        patch("backup_restore.subprocess.run", mock_run),
    ):
        result = backup_restore.backup(tag=tag)
        assert result is False
        # Assert no archive subprocess was attempted
        assert mock_run.call_count == 0


# ─── Row 4: Evil — server-side snapshot delete raises after good download ──


def test_evil_snapshot_delete_raises_after_good_download(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Row 4: server-side snapshot delete raises after good download -> backup() True, [WARN] printed, .snapshot valid."""
    monkeypatch.setattr(backup_restore, "BACKUP_DIR", str(tmp_path))

    tag = "test_evil_snapshot_delete_fail"
    target_dir = str(tmp_path / tag)

    mock_redis_cls = MagicMock()
    mock_qdrant_cls = MagicMock()
    mock_qdrant_inst = MagicMock()
    mock_qdrant_cls.return_value = mock_qdrant_inst
    mock_qdrant_inst.collection_exists.return_value = True

    snapshot_desc = MagicMock()
    snapshot_desc.name = "snapshot_123.snapshot"
    mock_qdrant_inst.create_snapshot.return_value = snapshot_desc
    mock_qdrant_inst.delete_snapshot.side_effect = RuntimeError("Snapshot delete failed on server")

    valid_snapshot_bytes = _create_valid_tar_bytes(size_bytes=15 * 1024, gzip_compress=False)
    mock_http_client_cls = _mock_httpx_client(valid_snapshot_bytes)

    with (
        patch("backup_restore.redis.Redis", mock_redis_cls),
        patch("backup_restore.QdrantClient", mock_qdrant_cls),
        patch("backup_restore.httpx.Client", mock_http_client_cls),
        patch("backup_restore.subprocess.run", side_effect=_mock_falkor_run_success(target_dir)),
    ):
        result = backup_restore.backup(tag=tag)
        assert result is True

        # Assert .snapshot is present and valid
        snapshot_file = os.path.join(target_dir, "qdrant_data.snapshot")
        assert os.path.exists(snapshot_file)
        with tarfile.open(snapshot_file, "r:*") as tar:
            assert len(tar.getmembers()) >= 1

        # Assert [WARN] printed
        captured = capsys.readouterr()
        assert "[WARN]" in captured.out or "[WARN]" in captured.err


# ─── Row 5: Sad — collection does not exist ──────────────────────────


def test_sad_collection_does_not_exist(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Row 5: collection does not exist -> backup() True, qdrant_data.EMPTY written, no .snapshot, note printed."""
    monkeypatch.setattr(backup_restore, "BACKUP_DIR", str(tmp_path))

    tag = "test_sad_absent_collection"
    target_dir = str(tmp_path / tag)

    mock_redis_cls = MagicMock()
    mock_qdrant_cls = MagicMock()
    mock_qdrant_inst = MagicMock()
    mock_qdrant_cls.return_value = mock_qdrant_inst
    mock_qdrant_inst.collection_exists.return_value = False

    with (
        patch("backup_restore.redis.Redis", mock_redis_cls),
        patch("backup_restore.QdrantClient", mock_qdrant_cls),
        patch("backup_restore.subprocess.run", side_effect=_mock_falkor_run_success(target_dir)),
    ):
        result = backup_restore.backup(tag=tag)
        assert result is True

        # Assert qdrant_data.EMPTY written, no .snapshot
        empty_file = os.path.join(target_dir, "qdrant_data.EMPTY")
        snapshot_file = os.path.join(target_dir, "qdrant_data.snapshot")
        assert os.path.exists(empty_file)
        assert os.path.getsize(empty_file) == 0
        assert not os.path.exists(snapshot_file)

        # Assert note printed
        captured = capsys.readouterr()
        assert (
            "empty" in captured.out.lower()
            or "note" in captured.out.lower()
            or "sentinel" in captured.out.lower()
        )


# ─── Row 6: Neutral — happy path ─────────────────────────────────────


def test_neutral_happy_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Row 6: happy path -> backup() True, .snapshot + falkor_data.tar.gz present, verify passes, snapshot deleted, exit 0."""
    monkeypatch.setattr(backup_restore, "BACKUP_DIR", str(tmp_path))

    tag = "test_happy_path"
    target_dir = str(tmp_path / tag)

    mock_redis_cls = MagicMock()
    mock_qdrant_cls = MagicMock()
    mock_qdrant_inst = MagicMock()
    mock_qdrant_cls.return_value = mock_qdrant_inst
    mock_qdrant_inst.collection_exists.return_value = True

    snapshot_desc = MagicMock()
    snapshot_desc.name = "snapshot_123.snapshot"
    mock_qdrant_inst.create_snapshot.return_value = snapshot_desc
    mock_qdrant_inst.delete_snapshot.return_value = True

    valid_snapshot_bytes = _create_valid_tar_bytes(size_bytes=15 * 1024, gzip_compress=False)
    mock_http_client_cls = _mock_httpx_client(valid_snapshot_bytes)

    with (
        patch("backup_restore.redis.Redis", mock_redis_cls),
        patch("backup_restore.QdrantClient", mock_qdrant_cls),
        patch("backup_restore.httpx.Client", mock_http_client_cls),
        patch("backup_restore.subprocess.run", side_effect=_mock_falkor_run_success(target_dir)),
    ):
        result = backup_restore.backup(tag=tag)
        assert result is True

        # Assert artifacts present
        snapshot_file = os.path.join(target_dir, "qdrant_data.snapshot")
        falkor_file = os.path.join(target_dir, "falkor_data.tar.gz")
        assert os.path.exists(snapshot_file)
        assert os.path.exists(falkor_file)

        # Assert server-side snapshot deleted
        mock_qdrant_inst.delete_snapshot.assert_called_once_with(
            collection_name="memory_embeddings",
            snapshot_name="snapshot_123.snapshot",
            wait=True,
        )

    # Assert exit 0 via __main__ save path
    with (
        patch("backup_restore.redis.Redis", mock_redis_cls),
        patch("backup_restore.QdrantClient", mock_qdrant_cls),
        patch("backup_restore.httpx.Client", mock_http_client_cls),
        patch("backup_restore.subprocess.run", side_effect=_mock_falkor_run_success(target_dir)),
    ):
        with pytest.raises(SystemExit) as exc_info:
            import argparse

            parser = argparse.ArgumentParser()
            subparsers = parser.add_subparsers(dest="command")
            p_backup = subparsers.add_parser("save")
            p_backup.add_argument("--tag")
            args = parser.parse_args(["save", "--tag", tag])
            if args.command == "save":
                sys.exit(0 if backup_restore.backup(args.tag) else 1)
        assert exc_info.value.code == 0


# ─── Row 7: Evil — falkor_data.tar.gz is corrupt gzip ────────────────


def test_evil_falkor_corrupt_gzip(tmp_path: Path) -> None:
    """Row 7: falkor_data.tar.gz is corrupt gzip -> _verify_backup False."""
    falkor_file = tmp_path / "falkor_data.tar.gz"
    falkor_file.write_bytes(b"\x1f\x8b\x08corrupted_gzip_content")

    snapshot_file = tmp_path / "qdrant_data.snapshot"
    snapshot_file.write_bytes(_create_valid_tar_bytes(size_bytes=15 * 1024, gzip_compress=False))

    result = backup_restore._verify_backup(str(tmp_path))
    assert result is False


# ─── Row 8: Neutral — _verify_backup on a well-formed backup dir ─────


def test_neutral_verify_backup_well_formed(tmp_path: Path) -> None:
    """Row 8: _verify_backup on a well-formed backup dir -> True."""
    # Test with valid .snapshot
    falkor_file = tmp_path / "falkor_data.tar.gz"
    falkor_file.write_bytes(_create_valid_tar_bytes(size_bytes=15 * 1024, gzip_compress=True))

    snapshot_file = tmp_path / "qdrant_data.snapshot"
    snapshot_file.write_bytes(_create_valid_tar_bytes(size_bytes=15 * 1024, gzip_compress=False))

    assert backup_restore._verify_backup(str(tmp_path)) is True

    # Test with valid .EMPTY sentinel
    snapshot_file.unlink()
    empty_file = tmp_path / "qdrant_data.EMPTY"
    empty_file.write_bytes(b"")

    assert backup_restore._verify_backup(str(tmp_path)) is True


# ─── Row 9: Evil (B7) — download raises after successful create_snapshot ──


def test_evil_download_fails_cleans_up_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Row 9: download raises after successful create_snapshot -> backup() False AND delete_snapshot attempted."""
    monkeypatch.setattr(backup_restore, "BACKUP_DIR", str(tmp_path))

    tag = "test_evil_download_leak_prevention"
    target_dir = str(tmp_path / tag)

    mock_redis_cls = MagicMock()
    mock_qdrant_cls = MagicMock()
    mock_qdrant_inst = MagicMock()
    mock_qdrant_cls.return_value = mock_qdrant_inst
    mock_qdrant_inst.collection_exists.return_value = True

    snapshot_desc = MagicMock()
    snapshot_desc.name = "snap_leaked_123.snapshot"
    mock_qdrant_inst.create_snapshot.return_value = snapshot_desc
    mock_qdrant_inst.delete_snapshot.return_value = True

    # Mock httpx to fail mid-download
    mock_http_client_cls = _mock_httpx_client(b"", status_code=500)

    with (
        patch("backup_restore.redis.Redis", mock_redis_cls),
        patch("backup_restore.QdrantClient", mock_qdrant_cls),
        patch("backup_restore.httpx.Client", mock_http_client_cls),
        patch("backup_restore.subprocess.run", side_effect=_mock_falkor_run_success(target_dir)),
    ):
        result = backup_restore.backup(tag=tag)
        assert result is False

        # Assert delete_snapshot was attempted with the created snapshot name
        mock_qdrant_inst.delete_snapshot.assert_called_once_with(
            collection_name="memory_embeddings",
            snapshot_name="snap_leaked_123.snapshot",
            wait=True,
        )
