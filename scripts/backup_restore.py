import argparse
import datetime
import os
import shutil
import subprocess
import sys
import tarfile
import time

import httpx
import redis
from qdrant_client import QdrantClient

# Safety net: ensure UTF-8 mode on Windows where the default codepage may be cp1252
os.environ.setdefault("PYTHONUTF8", "1")

BACKUP_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backups")


def backup(tag: str | None = None) -> bool:
    """Create a snapshot of FalkorDB and Qdrant data volumes.

    Returns True on success, False on any failure.
    """
    if not tag:
        tag = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")

    target_dir = os.path.join(BACKUP_DIR, tag)
    os.makedirs(target_dir, exist_ok=True)

    print(f"[SAVE] Creating Save Point: {tag}")

    # 0. Sync FalkorDB to Disk (load-bearing per D4/B2)
    if not _trigger_persistence():
        print("[FAIL] FalkorDB persistence failed. Aborting backup.")
        return False

    # Check execution environment (Container vs Host)
    falkor_mount = "/mnt/falkor_data"
    in_container = os.path.exists(falkor_mount)

    # 1. Backup FalkorDB
    print("   Backing up FalkorDB...", end=" ", flush=True)
    falkor_archive = os.path.join(target_dir, "falkor_data.tar.gz")

    if in_container:
        # Direct Tar
        cmd_falkor = ["tar", "czf", falkor_archive, "-C", falkor_mount, "."]
    else:
        # Docker Run (Host Mode)
        project_name = "claude-memory-mcp"
        falkor_vol = f"{project_name}_falkordb_data"
        host_target_dir = os.path.abspath(target_dir)

        cmd_falkor = [
            "docker",
            "run",
            "--rm",
            "-v",
            f"{falkor_vol}:/data",
            "-v",
            f"{host_target_dir}:/backup",
            "alpine",
            "tar",
            "czf",
            "/backup/falkor_data.tar.gz",
            "-C",
            "/data",
            ".",
        ]

    res = subprocess.run(cmd_falkor, capture_output=True, check=False)
    if res.returncode == 0:
        print("[OK]")
    else:
        print("[FAIL]")
        if res.stderr:
            print(res.stderr.decode("utf-8", errors="replace"))
        return False

    # 2. Backup Qdrant via snapshot API (D1/D2/B1/B7)
    print("   Backing up Qdrant...", end=" ", flush=True)
    if not _snapshot_qdrant(target_dir):
        print("[FAIL]")
        return False
    print("[OK]")

    # 3. Backup ontology.json
    ontology_src = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "ontology.json")
    if os.path.exists(ontology_src):
        print("   Backing up ontology.json...", end=" ", flush=True)
        shutil.copy2(ontology_src, os.path.join(target_dir, "ontology.json"))
        print("[OK]")
    else:
        print("   [SKIP] ontology.json not found (will use defaults)")

    if not _verify_backup(target_dir):
        print(f"[FAIL] Backup verification failed for {target_dir}")
        return False

    print(f"[DONE] Save Point Created in {target_dir}")
    return True


def _trigger_persistence() -> bool:
    """Forces databases to flush to disk before backup.

    Returns True if FalkorDB SAVE succeeded, False otherwise.
    """
    try:
        host = os.getenv("FALKORDB_HOST", "localhost")
        port = int(os.getenv("FALKORDB_PORT", "6379"))
        r = redis.Redis(host=host, port=port)
        r.save()  # Synchronous save
        print("[SAVE] FalkorDB Saved to Disk.")
        return True
    except Exception as exc:
        print(f"[FAIL] Could not trigger FalkorDB SAVE: {exc}")
        return False


def _snapshot_qdrant(target_dir: str) -> bool:
    """Capture Qdrant collection snapshot via sync QdrantClient and HTTP download.

    Per D1/D2/D5/D6 and Behaviors B1/B7.
    """
    host = os.getenv("QDRANT_HOST", "localhost")
    port = int(os.getenv("QDRANT_PORT", "6333"))
    collection = os.getenv("QDRANT_COLLECTION", "memory_embeddings")

    client: QdrantClient | None = None
    snapshot_name: str | None = None

    try:
        client = QdrantClient(host=host, port=port, timeout=120)

        # Fresh-install sad path (D5)
        if not client.collection_exists(collection_name=collection):
            empty_sentinel = os.path.join(target_dir, "qdrant_data.EMPTY")
            with open(empty_sentinel, "wb"):
                pass
            print(
                f"\n[NOTE] Collection '{collection}' does not exist. Created qdrant_data.EMPTY sentinel."
            )
            return True

        snapshot = client.create_snapshot(collection_name=collection, wait=True)
        if not snapshot or not getattr(snapshot, "name", None):
            print(f"\n[FAIL] create_snapshot returned invalid description: {snapshot}")
            return False

        snapshot_name = snapshot.name
        url = f"http://{host}:{port}/collections/{collection}/snapshots/{snapshot_name}"
        snapshot_path = os.path.join(target_dir, "qdrant_data.snapshot")

        with httpx.Client(timeout=120.0) as http_client:
            with http_client.stream("GET", url) as response:
                response.raise_for_status()
                with open(snapshot_path, "wb") as f:
                    for chunk in response.iter_bytes(chunk_size=8192):
                        f.write(chunk)

    except Exception as exc:
        print(f"\n[FAIL] Qdrant snapshot capture failed: {exc}")
        if client is not None and snapshot_name is not None:
            try:
                client.delete_snapshot(
                    collection_name=collection,
                    snapshot_name=snapshot_name,
                    wait=True,
                )
            except Exception as del_exc:
                print(f"[WARN] Failed to delete server-side snapshot {snapshot_name}: {del_exc}")
        return False

    # Server-side snapshot delete (cleanup hygiene)
    try:
        client.delete_snapshot(collection_name=collection, snapshot_name=snapshot_name, wait=True)
    except Exception as exc:
        print(f"\n[WARN] Failed to delete server-side snapshot {snapshot_name}: {exc}")

    return True


def _verify_falkordb_backup(target_dir: str, min_size: int) -> bool:
    """Verify falkor_data.tar.gz archive existence and validity."""
    falkor_path = os.path.join(target_dir, "falkor_data.tar.gz")
    if not os.path.exists(falkor_path):
        print("[FAIL] ERROR: Missing backup file falkor_data.tar.gz")
        return False

    try:
        with tarfile.open(falkor_path, "r:*") as tar:
            if len(tar.getmembers()) < 1:
                print("[FAIL] ERROR: falkor_data.tar.gz contains no files")
                return False
    except Exception as exc:
        print(f"[FAIL] ERROR: falkor_data.tar.gz is corrupted or not a valid tar: {exc}")
        return False

    falkor_size = os.path.getsize(falkor_path)
    if falkor_size < min_size:
        print(
            f"[WARN] WARNING: falkor_data.tar.gz is suspiciously small"
            f" ({falkor_size} bytes). Backup might be empty."
        )
    else:
        print(f"[OK] Verified falkor_data.tar.gz ({falkor_size / 1024:.2f} KB)")
    return True


def _verify_qdrant_backup(target_dir: str, min_size: int) -> bool:
    """Verify Qdrant snapshot archive or empty sentinel existence and validity."""
    snapshot_path = os.path.join(target_dir, "qdrant_data.snapshot")
    empty_path = os.path.join(target_dir, "qdrant_data.EMPTY")

    has_snapshot = os.path.exists(snapshot_path)
    has_empty = os.path.exists(empty_path)

    if (has_snapshot and has_empty) or (not has_snapshot and not has_empty):
        print("[FAIL] ERROR: Expected exactly one of qdrant_data.snapshot or qdrant_data.EMPTY")
        return False

    if has_empty:
        print("[OK] Verified qdrant_data.EMPTY (empty collection sentinel)")
        return True

    snapshot_size = os.path.getsize(snapshot_path)
    if snapshot_size < min_size:
        print(
            f"[FAIL] ERROR: qdrant_data.snapshot is too small"
            f" ({snapshot_size} bytes, minimum {min_size} bytes)"
        )
        return False

    try:
        with tarfile.open(snapshot_path, "r:*") as tar:
            if len(tar.getmembers()) < 1:
                print("[FAIL] ERROR: qdrant_data.snapshot contains no files")
                return False
    except Exception as exc:
        print(f"[FAIL] ERROR: qdrant_data.snapshot is corrupted or not a valid tar: {exc}")
        return False

    print(f"[OK] Verified qdrant_data.snapshot ({snapshot_size / 1024:.2f} KB)")
    return True


def _verify_backup(target_dir: str) -> bool:
    """Checks if backup files are valid (Behavior B3)."""
    min_size = 1024 * 10  # 10KB minimum
    if not _verify_falkordb_backup(target_dir, min_size):
        return False
    return _verify_qdrant_backup(target_dir, min_size)


def _validate_restore_artifacts(target_dir: str) -> tuple[bool, str | None]:
    """Validate backup directory artifacts before touching running containers.

    Returns (is_valid, qdrant_mode) where qdrant_mode in {'snapshot', 'empty', 'legacy'}.
    """
    falkor_path = os.path.join(target_dir, "falkor_data.tar.gz")
    if not os.path.exists(falkor_path):
        print(f"[FAIL] Missing falkor_data.tar.gz in {target_dir}")
        return False, None

    snapshot_path = os.path.join(target_dir, "qdrant_data.snapshot")
    empty_path = os.path.join(target_dir, "qdrant_data.EMPTY")
    legacy_path = os.path.join(target_dir, "qdrant_data.tar.gz")

    has_snapshot = os.path.exists(snapshot_path)
    has_empty = os.path.exists(empty_path)
    has_legacy = os.path.exists(legacy_path)

    if not has_snapshot and not has_empty and not has_legacy:
        print(
            "[FAIL] No Qdrant backup artifact found (expected qdrant_data.snapshot, qdrant_data.EMPTY, or qdrant_data.tar.gz)"
        )
        return False, None

    if has_snapshot and has_empty:
        print("[FAIL] Invalid backup: both qdrant_data.snapshot and qdrant_data.EMPTY exist")
        return False, None

    if (has_snapshot or has_empty) and has_legacy:
        print(
            "[WARN] Both snapshot and legacy qdrant_data.tar.gz present. Preferring snapshot format."
        )

    if has_snapshot:
        return True, "snapshot"
    if has_empty:
        return True, "empty"
    return True, "legacy"


def _restore_falkordb_volume(target_dir: str, falkor_vol: str) -> bool:
    """Restore FalkorDB volume from falkor_data.tar.gz."""
    print("Restoring FalkorDB...", end=" ", flush=True)
    cmd_falkor = [
        "docker",
        "run",
        "--rm",
        "-v",
        f"{falkor_vol}:/data",
        "-v",
        f"{os.path.abspath(target_dir)}:/backup",
        "alpine",
        "sh",
        "-c",
        "rm -rf /data/* && tar xzf /backup/falkor_data.tar.gz -C /data",
    ]
    res_falkor = subprocess.run(cmd_falkor, capture_output=True, check=False)
    if res_falkor.returncode != 0:
        print("[FAIL]")
        if res_falkor.stderr:
            print(res_falkor.stderr.decode("utf-8", errors="replace"))
        return False
    print("[OK]")
    return True


def _restore_qdrant_legacy(target_dir: str, qdrant_vol: str) -> bool:
    """Restore legacy qdrant_data.tar.gz format."""
    print("Restoring Qdrant...", end=" ", flush=True)
    cmd_qdrant = [
        "docker",
        "run",
        "--rm",
        "-v",
        f"{qdrant_vol}:/qdrant/storage",
        "-v",
        f"{os.path.abspath(target_dir)}:/backup",
        "alpine",
        "sh",
        "-c",
        "rm -rf /qdrant/storage/* && tar xzf /backup/qdrant_data.tar.gz -C /qdrant/storage",
    ]
    res_qdrant = subprocess.run(cmd_qdrant, capture_output=True, check=False)
    if res_qdrant.returncode != 0:
        print("[FAIL]")
        if res_qdrant.stderr:
            print(res_qdrant.stderr.decode("utf-8", errors="replace"))
        return False
    print("[OK]")
    return True


def _wait_for_qdrant(host: str, port: int, timeout: float = 60.0) -> bool:
    """Polls Qdrant readiness until 200 OK or timeout expires."""
    deadline = time.time() + timeout
    url = f"http://{host}:{port}/readyz"
    with httpx.Client(timeout=2.0) as client:
        while time.time() < deadline:
            try:
                resp = client.get(url)
                if resp.status_code == 200:
                    return True
            except Exception:  # noqa: S110
                pass
            time.sleep(0.5)
    return False


def _recover_qdrant_snapshot(target_dir: str, host: str, port: int, collection: str) -> bool:
    """Upload and recover snapshot via Qdrant HTTP API."""
    snapshot_path = os.path.join(target_dir, "qdrant_data.snapshot")
    upload_url = f"http://{host}:{port}/collections/{collection}/snapshots/upload?priority=snapshot"
    try:
        with open(snapshot_path, "rb") as f:
            files = {"snapshot": (os.path.basename(snapshot_path), f, "application/octet-stream")}
            with httpx.Client(timeout=120.0) as http_client:
                response = http_client.post(upload_url, files=files)
                response.raise_for_status()
        return True
    except Exception as exc:
        print(f"\n[FAIL] Qdrant snapshot upload-recovery failed: {exc}")
        return False


def _verify_qdrant_restored(host: str, port: int, collection: str) -> bool:
    """Verify collection exists and print point count."""
    try:
        client = QdrantClient(host=host, port=port, timeout=120)
        collections_resp = client.get_collections()
        colls = [c.name for c in collections_resp.collections]
        if collection not in colls:
            print(
                f"\n[FAIL] Post-restore verification failed: collection '{collection}' not found."
            )
            return False
        count_info = client.count(collection_name=collection)
        points_count = getattr(count_info, "count", count_info)
        print(f"[OK] Post-restore verified: collection '{collection}' has {points_count} points.")
        return True
    except Exception as exc:
        print(f"\n[FAIL] Post-restore verification failed: {exc}")
        return False


def _stop_containers() -> bool:
    """Stop running compose containers."""
    print("Stopping containers...")
    res_stop = subprocess.run(
        ["docker-compose", "stop"],  # noqa: S607
        capture_output=True,
        check=False,
    )
    if res_stop.returncode != 0:
        print(
            f"[FAIL] docker-compose stop failed: {res_stop.stderr.decode('utf-8', errors='replace')}"
        )
        return False
    return True


def _start_containers() -> bool:
    """Restart compose containers."""
    print("Restarting containers...")
    res_up = subprocess.run(
        ["docker-compose", "up", "-d"],  # noqa: S607
        capture_output=True,
        check=False,
    )
    if res_up.returncode != 0:
        print(
            f"[FAIL] docker-compose up -d failed: {res_up.stderr.decode('utf-8', errors='replace')}"
        )
        return False
    return True


def _restore_snapshot_pipeline(target_dir: str, host: str, port: int, collection: str) -> bool:
    """Run Qdrant snapshot recovery pipeline after container restart."""
    print("Waiting for Qdrant readiness...", end=" ", flush=True)
    if not _wait_for_qdrant(host, port, timeout=60.0):
        print(f"\n[FAIL] Qdrant container on {host}:{port} was not ready within 60s timeout.")
        return False
    print("[OK]")

    print("Recovering Qdrant snapshot...", end=" ", flush=True)
    if not _recover_qdrant_snapshot(target_dir, host, port, collection):
        return False
    print("[OK]")

    return _verify_qdrant_restored(host, port, collection)


def _confirm_restore(tag: str, force: bool) -> bool:
    """Prompt user confirmation or proceed in force mode."""
    print(f"[RESTORE] Restoring Save Point: {tag}")
    print("[WARN] WARNING: This will overwrite current database state.")
    if not force:
        confirm = input("Type 'RESTORE' to confirm: ")
        if confirm != "RESTORE":
            print("Aborted.")
            return False
    else:
        print("Force mode enabled. Proceeding immediately.")
    return True


def _dispatch_qdrant_restore(qdrant_mode: str, target_dir: str, qdrant_vol: str) -> bool:
    """Execute Qdrant restoration steps based on detected backup format."""
    if qdrant_mode == "legacy":
        if not _restore_qdrant_legacy(target_dir, qdrant_vol):
            return False
        return _start_containers()

    if not _start_containers():
        return False

    if qdrant_mode == "empty":
        print("[NOTE] Empty collection sentinel detected. Skipping Qdrant recovery.")
        return True

    host = os.getenv("QDRANT_HOST", "localhost")
    port = int(os.getenv("QDRANT_PORT", "6333"))
    collection = os.getenv("QDRANT_COLLECTION", "memory_embeddings")
    return _restore_snapshot_pipeline(target_dir, host, port, collection)


def _prepare_restore(tag: str) -> tuple[bool, str, str | None]:
    """Check existence of backup directory and validate artifacts."""
    target_dir = os.path.join(BACKUP_DIR, tag)
    if not os.path.exists(target_dir):
        print(f"[FAIL] Backup '{tag}' not found in {BACKUP_DIR}")
        return False, target_dir, None
    valid, qdrant_mode = _validate_restore_artifacts(target_dir)
    return valid, target_dir, qdrant_mode


def restore(tag: str, force: bool = False) -> bool:
    """Restore a previously saved snapshot, overwriting current database state.

    Per D3/D4/D5/D6 and Behaviors B5/B6.
    """
    valid, target_dir, qdrant_mode = _prepare_restore(tag)
    if not valid or qdrant_mode is None:
        return False

    if not _confirm_restore(tag, force):
        return False

    # Stop containers first to avoid corruption
    if not _stop_containers():
        return False

    project_name = "claude-memory-mcp"
    falkor_vol = f"{project_name}_falkordb_data"
    qdrant_vol = f"{project_name}_qdrant_data"

    # Restore FalkorDB volume
    if not _restore_falkordb_volume(target_dir, falkor_vol):
        return False

    if not _dispatch_qdrant_restore(qdrant_mode, target_dir, qdrant_vol):
        return False

    print("[DONE] System Restored.")
    return True


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Backup/Restore Exocortex Memory")
    subparsers = parser.add_subparsers(dest="command")

    p_backup = subparsers.add_parser("save", help="Create a backup snapshot")
    p_backup.add_argument("--tag", help="Custom name for the backup")

    p_restore = subparsers.add_parser("load", help="Restore a backup snapshot")
    p_restore.add_argument("tag", help="Name of the backup to restore")
    p_restore.add_argument("--force", action="store_true", help="Skip confirmation prompt")

    args = parser.parse_args()

    if args.command == "save":
        success = backup(args.tag)
        sys.exit(0 if success else 1)
    elif args.command == "load":
        success = restore(args.tag, args.force)
        sys.exit(0 if success else 1)
    else:
        parser.print_help()
        sys.exit(1)
