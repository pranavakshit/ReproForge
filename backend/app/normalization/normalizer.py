"""Dual-Mode Artifact Normalization Engine.

Applies deterministic normalization transforms (timestamp epoching, permission clamping,
UID/GID zeroing, lexicographical sorting, and gzip header zeroing) to generate
normalized artifacts and compute normalized equivalence hashes (Mode B) without modifying
original artifacts.
"""

import gzip
import io
import os
import tarfile
import zipfile
from pathlib import Path
from typing import List, Optional
from pydantic import BaseModel, Field

from backend.app.comparison.hasher import ArtifactHasher
from backend.app.core.logging import get_logger

logger = get_logger("reproforge.normalization")

# Fixed default epoch: 2024-01-01T00:00:00Z
DEFAULT_NORMALIZED_EPOCH = 1704067200


class NormalizationResult(BaseModel):
    """Telemetry and hashes produced by artifact normalization."""

    source_path: str
    normalized_path: str
    original_sha256: str
    normalized_sha256: str
    applied_rules: List[str] = Field(default_factory=list)
    entries_normalized_count: int = 0


class ArtifactNormalizer:
    """Transforms raw artifacts into canonical normalized representations."""

    def __init__(self, epoch_timestamp: int = DEFAULT_NORMALIZED_EPOCH) -> None:
        self.epoch = epoch_timestamp

    def normalize_zip(self, source_zip: Path, target_zip: Path) -> NormalizationResult:
        """Normalize a ZIP or Python wheel archive with sorted entries and fixed timestamps."""
        orig_sha = ArtifactHasher.compute_sha256(source_zip)
        applied = ["FIXED_DATE_TIME_EPOCH", "LEXICOGRAPHICAL_ENTRY_ORDER", "ATTR_NORMALIZATION"]

        target_zip.parent.mkdir(parents=True, exist_ok=True)
        entries_count = 0

        with zipfile.ZipFile(source_zip, "r") as src_zf:
            # Sort members lexicographically by filename
            sorted_infos = sorted(src_zf.infolist(), key=lambda x: x.filename)
            entries_count = len(sorted_infos)

            with zipfile.ZipFile(target_zip, "w", compression=zipfile.ZIP_DEFLATED) as dst_zf:
                for info in sorted_infos:
                    content = src_zf.read(info)
                    # Create normalized ZipInfo
                    new_info = zipfile.ZipInfo(filename=info.filename)
                    # 2024-01-01 00:00:00
                    new_info.date_time = (2024, 1, 1, 0, 0, 0)
                    new_info.compress_type = zipfile.ZIP_DEFLATED

                    # Standardize permissions (0644 for files, 0755 for dirs/exec)
                    is_dir = info.is_dir() or info.filename.endswith("/")
                    if is_dir:
                        new_info.external_attr = 0o755 << 16 | 0x10  # directory flag
                    else:
                        is_exec = bool(info.external_attr and (info.external_attr >> 16) & 0o111)
                        mode = 0o755 if is_exec else 0o644
                        new_info.external_attr = mode << 16

                    dst_zf.writestr(new_info, content)

        norm_sha = ArtifactHasher.compute_sha256(target_zip)
        return NormalizationResult(
            source_path=str(source_zip),
            normalized_path=str(target_zip),
            original_sha256=orig_sha,
            normalized_sha256=norm_sha,
            applied_rules=applied,
            entries_normalized_count=entries_count,
        )

    def normalize_tar(self, source_tar: Path, target_tar: Path) -> NormalizationResult:
        """Normalize a TAR archive with fixed timestamps, root ownership, and sorted entries."""
        orig_sha = ArtifactHasher.compute_sha256(source_tar)
        applied = [
            "SOURCE_DATE_EPOCH_MTIME",
            "ROOT_OWNERSHIP_ZEROED",
            "LEXICOGRAPHICAL_MEMBER_ORDER",
            "PERMISSION_MODE_CLAMPED",
        ]

        target_tar.parent.mkdir(parents=True, exist_ok=True)
        entries_count = 0

        # Unpack member contents in memory
        members_data = []
        with tarfile.open(source_tar, "r:*") as src_tf:
            for member in src_tf.getmembers():
                content = None
                if member.isfile():
                    f = src_tf.extractfile(member)
                    content = f.read() if f else b""
                members_data.append((member, content))

        # Sort members lexicographically by name
        members_data.sort(key=lambda item: item[0].name)
        entries_count = len(members_data)

        # Write out uncompressed normalized tar first
        uncompressed_tar = target_tar.parent / f"{target_tar.stem}_raw.tar"
        with tarfile.open(uncompressed_tar, "w") as dst_tf:
            for member, content in members_data:
                norm_member = tarfile.TarInfo(name=member.name)
                norm_member.size = member.size if member.isfile() else 0
                norm_member.mtime = self.epoch
                norm_member.uid = 0
                norm_member.gid = 0
                norm_member.uname = "root"
                norm_member.gname = "root"
                norm_member.type = member.type
                norm_member.linkname = member.linkname

                if member.isdir():
                    norm_member.mode = 0o755
                elif member.mode & 0o111:
                    norm_member.mode = 0o755
                else:
                    norm_member.mode = 0o644

                if member.isfile() and content is not None:
                    dst_tf.addfile(norm_member, io.BytesIO(content))
                else:
                    dst_tf.addfile(norm_member)

        # If original was gzipped, compress with normalized gzip header (mtime=0)
        is_gzip = source_tar.name.endswith((".tar.gz", ".tgz"))
        if is_gzip:
            applied.append("GZIP_HEADER_MTIME_ZEROED")
            raw_bytes = uncompressed_tar.read_bytes()
            with open(target_tar, "wb") as out_fp:
                # gzip.GzipFile with mtime=0.0 produces deterministic header bytes 4-7
                with gzip.GzipFile(filename="", mode="wb", fileobj=out_fp, mtime=0.0) as gz:
                    gz.write(raw_bytes)
            if uncompressed_tar.exists():
                uncompressed_tar.unlink()
        else:
            if target_tar.exists():
                target_tar.unlink()
            uncompressed_tar.rename(target_tar)

        norm_sha = ArtifactHasher.compute_sha256(target_tar)
        return NormalizationResult(
            source_path=str(source_tar),
            normalized_path=str(target_tar),
            original_sha256=orig_sha,
            normalized_sha256=norm_sha,
            applied_rules=applied,
            entries_normalized_count=entries_count,
        )

    def normalize(self, artifact_path: Path, output_dir: Path) -> NormalizationResult:
        """Polymorphically normalize an artifact based on format."""
        output_dir.mkdir(parents=True, exist_ok=True)
        name = artifact_path.name.lower()
        target_file = output_dir / f"normalized_{artifact_path.name}"

        if name.endswith((".zip", ".whl", ".jar")):
            return self.normalize_zip(artifact_path, target_file)
        elif name.endswith((".tar", ".tar.gz", ".tgz", ".tar.bz2", ".tar.xz")):
            return self.normalize_tar(artifact_path, target_file)
        else:
            # Standalone binary/file - normalization is identity with original hash
            import shutil
            shutil.copy2(artifact_path, target_file)
            orig_sha = ArtifactHasher.compute_sha256(artifact_path)
            return NormalizationResult(
                source_path=str(artifact_path),
                normalized_path=str(target_file),
                original_sha256=orig_sha,
                normalized_sha256=orig_sha,
                applied_rules=["STANDALONE_PASSTHROUGH"],
                entries_normalized_count=1,
            )
