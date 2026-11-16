"""Recursive archive unpacker, directory tree inspector, and entry metadata cataloger."""

import hashlib
import os
import tarfile
import zipfile
from pathlib import Path
from typing import Dict, List, Optional
from pydantic import BaseModel, Field

from backend.app.core.logging import get_logger

logger = get_logger("reproforge.comparison.unpacker")


class ArtifactEntry(BaseModel):
    """Detailed structural metadata and cryptographic hash for an individual artifact entry."""

    path: str
    size_bytes: int
    mtime: int
    mode: int
    uid: int = 0
    gid: int = 0
    uname: str = ""
    gname: str = ""
    sha256: str
    is_dir: bool = False
    is_symlink: bool = False
    link_target: Optional[str] = None


class ArtifactTree(BaseModel):
    """Catalog of all structural entries within an artifact or extracted directory."""

    source_path: str
    entries: Dict[str, ArtifactEntry] = Field(default_factory=dict)
    total_files: int = 0
    total_size_bytes: int = 0
    content_only_digest: str = ""

    def get_entry(self, rel_path: str) -> Optional[ArtifactEntry]:
        """Lookup an entry by normalized relative path."""
        norm = rel_path.replace("\\", "/").strip("/")
        return self.entries.get(norm)


class ArchiveUnpacker:
    """Safely inspects and unpacks TAR, ZIP, Python wheels, and directories into ArtifactTree structures."""

    @classmethod
    def _safe_rel_path(cls, path_str: str) -> str:
        """Normalize relative path and protect against directory traversal attacks."""
        norm = os.path.normpath(path_str).replace("\\", "/").lstrip("/")
        if norm.startswith("../") or norm == "..":
            raise ValueError(f"Path traversal detected in archive entry: {path_str}")
        return norm

    @classmethod
    def inspect_directory(cls, dir_path: Path) -> ArtifactTree:
        """Walk a filesystem directory and catalog all entries and hashes."""
        entries: Dict[str, ArtifactEntry] = {}
        total_size = 0

        for root, dirs, files in os.walk(dir_path):
            for d in dirs:
                full_d = Path(root) / d
                rel_d = cls._safe_rel_path(str(full_d.relative_to(dir_path)))
                stat = full_d.stat()
                entries[rel_d] = ArtifactEntry(
                    path=rel_d,
                    size_bytes=0,
                    mtime=int(stat.st_mtime),
                    mode=stat.st_mode,
                    sha256="",
                    is_dir=True,
                )

            for f in files:
                full_f = Path(root) / f
                rel_f = cls._safe_rel_path(str(full_f.relative_to(dir_path)))
                stat = full_f.stat()
                total_size += stat.st_size

                hasher = hashlib.sha256()
                with open(full_f, "rb") as fp:
                    while chunk := fp.read(65536):
                        hasher.update(chunk)
                file_hash = hasher.hexdigest()

                entries[rel_f] = ArtifactEntry(
                    path=rel_f,
                    size_bytes=stat.st_size,
                    mtime=int(stat.st_mtime),
                    mode=stat.st_mode,
                    uid=getattr(stat, "st_uid", 0),
                    gid=getattr(stat, "st_gid", 0),
                    sha256=file_hash,
                    is_dir=False,
                )

        content_digest = cls._compute_content_only_digest(entries)
        return ArtifactTree(
            source_path=str(dir_path),
            entries=entries,
            total_files=len([e for e in entries.values() if not e.is_dir]),
            total_size_bytes=total_size,
            content_only_digest=content_digest,
        )

    @classmethod
    def unpack_zip(cls, zip_path: Path, target_dir: Path) -> ArtifactTree:
        """Extract and inspect a ZIP or Python wheel archive."""
        target_dir.mkdir(parents=True, exist_ok=True)
        entries: Dict[str, ArtifactEntry] = {}
        total_size = 0

        with zipfile.ZipFile(zip_path, "r") as zf:
            for info in zf.infolist():
                rel_path = cls._safe_rel_path(info.filename)
                is_dir = info.is_dir()
                size = info.file_size
                total_size += size

                # Convert zip date_time tuple (year, month, day, hour, min, sec) to epoch
                import datetime
                try:
                    dt = datetime.datetime(*info.date_time, tzinfo=datetime.timezone.utc)
                    mtime = int(dt.timestamp())
                except Exception:
                    mtime = 0

                file_hash = ""
                if not is_dir:
                    content = zf.read(info)
                    file_hash = hashlib.sha256(content).hexdigest()
                    out_file = target_dir / rel_path
                    out_file.parent.mkdir(parents=True, exist_ok=True)
                    out_file.write_bytes(content)
                else:
                    (target_dir / rel_path).mkdir(parents=True, exist_ok=True)

                entries[rel_path] = ArtifactEntry(
                    path=rel_path,
                    size_bytes=size,
                    mtime=mtime,
                    mode=info.external_attr >> 16 if info.external_attr else 0o644,
                    sha256=file_hash,
                    is_dir=is_dir,
                )

        content_digest = cls._compute_content_only_digest(entries)
        return ArtifactTree(
            source_path=str(zip_path),
            entries=entries,
            total_files=len([e for e in entries.values() if not e.is_dir]),
            total_size_bytes=total_size,
            content_only_digest=content_digest,
        )

    @classmethod
    def unpack_tar(cls, tar_path: Path, target_dir: Path) -> ArtifactTree:
        """Extract and inspect a TAR archive (.tar, .tar.gz, .tgz, .tar.bz2, .tar.xz)."""
        target_dir.mkdir(parents=True, exist_ok=True)
        entries: Dict[str, ArtifactEntry] = {}
        total_size = 0

        with tarfile.open(tar_path, "r:*") as tf:
            for member in tf.getmembers():
                rel_path = cls._safe_rel_path(member.name)
                is_dir = member.isdir()
                size = member.size
                total_size += size
                file_hash = ""

                if member.isfile():
                    f = tf.extractfile(member)
                    if f:
                        content = f.read()
                        file_hash = hashlib.sha256(content).hexdigest()
                        out_file = target_dir / rel_path
                        out_file.parent.mkdir(parents=True, exist_ok=True)
                        out_file.write_bytes(content)
                elif is_dir:
                    (target_dir / rel_path).mkdir(parents=True, exist_ok=True)

                entries[rel_path] = ArtifactEntry(
                    path=rel_path,
                    size_bytes=size,
                    mtime=member.mtime,
                    mode=member.mode,
                    uid=member.uid,
                    gid=member.gid,
                    uname=member.uname,
                    gname=member.gname,
                    sha256=file_hash,
                    is_dir=is_dir,
                    is_symlink=member.issym(),
                    link_target=member.linkname if member.issym() else None,
                )

        content_digest = cls._compute_content_only_digest(entries)
        return ArtifactTree(
            source_path=str(tar_path),
            entries=entries,
            total_files=len([e for e in entries.values() if not e.is_dir]),
            total_size_bytes=total_size,
            content_only_digest=content_digest,
        )

    @classmethod
    def unpack(cls, artifact_path: Path, target_dir: Path) -> ArtifactTree:
        """Polymorphically detect artifact format and unpack/catalog it."""
        name = artifact_path.name.lower()
        if name.endswith((".zip", ".whl", ".jar")):
            return cls.unpack_zip(artifact_path, target_dir)
        elif name.endswith((".tar", ".tar.gz", ".tgz", ".tar.bz2", ".tar.xz")):
            return cls.unpack_tar(artifact_path, target_dir)
        elif artifact_path.is_dir():
            return cls.inspect_directory(artifact_path)
        else:
            # Standalone single file
            target_dir.mkdir(parents=True, exist_ok=True)
            dest = target_dir / artifact_path.name
            import shutil
            shutil.copy2(artifact_path, dest)
            stat = artifact_path.stat()
            hasher = hashlib.sha256()
            with open(artifact_path, "rb") as fp:
                while chunk := fp.read(65536):
                    hasher.update(chunk)
            sha = hasher.hexdigest()
            entry = ArtifactEntry(
                path=artifact_path.name,
                size_bytes=stat.st_size,
                mtime=int(stat.st_mtime),
                mode=stat.st_mode,
                sha256=sha,
                is_dir=False,
            )
            return ArtifactTree(
                source_path=str(artifact_path),
                entries={artifact_path.name: entry},
                total_files=1,
                total_size_bytes=stat.st_size,
                content_only_digest=sha,
            )

    @classmethod
    def _compute_content_only_digest(cls, entries: Dict[str, ArtifactEntry]) -> str:
        """Compute canonical hash over lexicographically sorted file paths and content hashes.

        Ignores file timestamps, permissions, and archive metadata.
        """
        hasher = hashlib.sha256()
        file_entries = sorted(
            [e for e in entries.values() if not e.is_dir],
            key=lambda e: e.path,
        )
        for e in file_entries:
            hasher.update(f"{e.path}:{e.sha256}\n".encode("utf-8"))
        return hasher.hexdigest()
