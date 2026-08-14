import argparse
import datetime
import os
import shutil
import subprocess
import sys
import tarfile

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

    # 2. Backup Qdrant via snapshot API (D1/D2/B1)
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

    Per D1/D2/D5/D6 and Behavior B1.
    """
    host = os.getenv("QDRANT_HOST", "localhost")
    port = int(os.getenv("QDRANT_PORT", "6333"))
    collection = os.getenv("QDRANT_COLLECTION", "memory_embeddings")

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


def restore(tag: str, force: bool = False) -> None:
    """Restore a previously saved snapshot, overwriting current database state."""
    target_dir = os.path.join(BACKUP_DIR, tag)
    if not os.path.exists(target_dir):
        print(f"[FAIL] Backup '{tag}' not found in {BACKUP_DIR}")
        return

    print(f"[RESTORE] Restoring Save Point: {tag}")
    print("[WARN] WARNING: This will overwrite current database state.")

    if not force:
        confirm = input("Type 'RESTORE' to confirm: ")
        if confirm != "RESTORE":
            print("Aborted.")
            return
    else:
        print("Force mode enabled. Proceeding immediately.")

    # Stop containers first to avoid corruption
    print("Stopping containers...")
    subprocess.run(
        ["docker-compose", "stop"],  # noqa: S607
        check=False,
    )

    # Restore FalkorDB
    project_name = "claude-memory-mcp"
    falkor_vol = f"{project_name}_falkordb_data"
    qdrant_vol = f"{project_name}_qdrant_data"

    print("Restoring FalkorDB...", end=" ")
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
    subprocess.run(cmd_falkor, check=False)
    print("[OK]")

    # Restore Qdrant
    print("Restoring Qdrant...", end=" ")
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
    subprocess.run(cmd_qdrant, check=False)
    print("[OK]")

    print("Restarting containers...")
    subprocess.run(
        ["docker-compose", "up", "-d"],  # noqa: S607
        check=False,
    )
    print("[DONE] System Restored.")


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
        restore(args.tag, args.force)
    else:
        parser.print_help()
        sys.exit(1)
