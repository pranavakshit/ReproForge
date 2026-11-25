"""Dual-Mode Artifact Normalization Engine.

Applies deterministic normalization transforms (timestamp epoching, permission clamping,
UID/GID zeroing, lexicographical sorting, and gzip header zeroing) to generate
normalized artifacts and compute normalized equivalence hashes (Mode B) without modifying
original artifacts.
"""

from datetime import datetime, timezone
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


class NormalizationRuleRecord(BaseModel):
    """Master Prompt §23.6 audit record for a single normalization rule application."""

    rule_id: str
    version: str = "1.0.0"
    artifact_format: str
    metadata_field: str
    original_value: str
    normalized_value: str
    rationale: str
    affects_runtime_semantics: bool = False
    enabled: bool = True


class NormalizationResult(BaseModel):
    """Telemetry, hashes, and per-rule audit metadata produced by artifact normalization."""

    source_path: str
    normalized_path: str
    original_sha256: str
    normalized_sha256: str
    applied_rules: List[str] = Field(default_factory=list)
    rule_records: List[NormalizationRuleRecord] = Field(default_factory=list)
    entries_normalized_count: int = 0


class ArtifactNormalizer:
    """Transforms raw artifacts into canonical normalized representations."""

    def __init__(self, epoch_timestamp: int = DEFAULT_NORMALIZED_EPOCH) -> None:
        self.epoch = epoch_timestamp

    def _zip_epoch_tuple(self) -> tuple[int, int, int, int, int, int]:
        """Convert POSIX epoch timestamp into DOS-compatible ZipInfo date_time tuple (>=1980)."""
        dt = datetime.fromtimestamp(self.epoch, tz=timezone.utc)
        if dt.year < 1980:
            return (1980, 1, 1, 0, 0, 0)
        return (dt.year, dt.month, dt.day, dt.hour, dt.minute, dt.second)

    def normalize_zip(self, source_zip: Path, target_zip: Path) -> NormalizationResult:
        """Normalize a ZIP or Python wheel archive with sorted entries and fixed timestamps."""
        orig_sha = ArtifactHasher.compute_sha256(source_zip)
        applied = ["FIXED_DATE_TIME_EPOCH", "LEXICOGRAPHICAL_ENTRY_ORDER", "ATTR_NORMALIZATION"]
        epoch_tuple = self._zip_epoch_tuple()
        orig_mtimes: set[str] = set()
        orig_modes: set[str] = set()

        target_zip.parent.mkdir(parents=True, exist_ok=True)
        entries_count = 0

        with zipfile.ZipFile(source_zip, "r") as src_zf:
            # Sort members lexicographically by filename
            sorted_infos = sorted(src_zf.infolist(), key=lambda x: x.filename)
            entries_count = len(sorted_infos)

            with zipfile.ZipFile(target_zip, "w", compression=zipfile.ZIP_DEFLATED) as dst_zf:
                for info in sorted_infos:
                    content = src_zf.read(info)
                    orig_mtimes.add(str(info.date_time))
                    orig_modes.add(oct((info.external_attr >> 16) & 0o777))

                    new_info = zipfile.ZipInfo(filename=info.filename)
                    new_info.date_time = epoch_tuple
                    new_info.compress_type = zipfile.ZIP_DEFLATED

                    # Standardize permissions (0644 for files, 0755 for dirs/exec)
                    is_dir = info.is_dir() or info.filename.endswith("/")
                    if is_dir:
                        new_info.external_attr = 0o755 << 16 | 0x10
                    else:
                        is_exec = bool(info.external_attr and (info.external_attr >> 16) & 0o111)
                        mode = 0o755 if is_exec else 0o644
                        new_info.external_attr = mode << 16

                    dst_zf.writestr(new_info, content)

        rule_records = [
            NormalizationRuleRecord(
                rule_id="FIXED_DATE_TIME_EPOCH",
                artifact_format="zip",
                metadata_field="ZipInfo.date_time",
                original_value=", ".join(sorted(orig_mtimes)[:3]) or "unset",
                normalized_value=str(epoch_tuple),
                rationale="Archive creation timestamps reflect build clock rather than source code semantics.",
                affects_runtime_semantics=False,
                enabled=True,
            ),
            NormalizationRuleRecord(
                rule_id="LEXICOGRAPHICAL_ENTRY_ORDER",
                artifact_format="zip",
                metadata_field="central_directory_order",
                original_value="filesystem_readdir_order",
                normalized_value="sorted_lexicographical",
                rationale="ZIP central directory order depends on OS directory traversal.",
                affects_runtime_semantics=False,
                enabled=True,
            ),
            NormalizationRuleRecord(
                rule_id="ATTR_NORMALIZATION",
                artifact_format="zip",
                metadata_field="ZipInfo.external_attr",
                original_value=", ".join(sorted(orig_modes)[:3]) or "host_umask",
                normalized_value="0o644/0o755",
                rationale="Preserve executable bit only; host umask variations are non-semantic.",
                affects_runtime_semantics=False,
                enabled=True,
            ),
        ]

        norm_sha = ArtifactHasher.compute_sha256(target_zip)
        return NormalizationResult(
            source_path=str(source_zip),
            normalized_path=str(target_zip),
            original_sha256=orig_sha,
            normalized_sha256=norm_sha,
            applied_rules=applied,
            rule_records=rule_records,
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
        orig_mtimes: set[int] = set()
        orig_owners: set[str] = set()

        members_data = []
        with tarfile.open(source_tar, "r:*") as src_tf:
            for member in src_tf.getmembers():
                orig_mtimes.add(member.mtime)
                orig_owners.add(f"{member.uid}:{member.gid}")
                content = None
                if member.isfile():
                    f = src_tf.extractfile(member)
                    content = f.read() if f else b""
                members_data.append((member, content))

        members_data.sort(key=lambda item: item[0].name)
        entries_count = len(members_data)

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

        rule_records = [
            NormalizationRuleRecord(
                rule_id="SOURCE_DATE_EPOCH_MTIME",
                artifact_format="tar",
                metadata_field="TarInfo.mtime",
                original_value=", ".join(str(m) for m in sorted(orig_mtimes)[:3]) or "unset",
                normalized_value=str(self.epoch),
                rationale="Header timestamps reflect execution clock; zeroing/epoching isolates file payloads.",
                affects_runtime_semantics=False,
                enabled=True,
            ),
            NormalizationRuleRecord(
                rule_id="ROOT_OWNERSHIP_ZEROED",
                artifact_format="tar",
                metadata_field="TarInfo.uid/gid/uname/gname",
                original_value=", ".join(sorted(orig_owners)[:3]) or "host_user",
                normalized_value="0:0 (root:root)",
                rationale="Host build user UID/GID leaks builder account identity without changing artifact content.",
                affects_runtime_semantics=False,
                enabled=True,
            ),
            NormalizationRuleRecord(
                rule_id="LEXICOGRAPHICAL_MEMBER_ORDER",
                artifact_format="tar",
                metadata_field="tar_member_order",
                original_value="readdir_order",
                normalized_value="sorted_lexicographical",
                rationale="Removes inode traversal variance across filesystems.",
                affects_runtime_semantics=False,
                enabled=True,
            ),
            NormalizationRuleRecord(
                rule_id="PERMISSION_MODE_CLAMPED",
                artifact_format="tar",
                metadata_field="TarInfo.mode",
                original_value="host_umask_bits",
                normalized_value="0o644/0o755",
                rationale="Preserves executable bit while stripping group/world write umask variance.",
                affects_runtime_semantics=False,
                enabled=True,
            ),
        ]

        is_gzip = source_tar.name.endswith((".tar.gz", ".tgz"))
        if is_gzip:
            applied.append("GZIP_HEADER_MTIME_ZEROED")
            raw_bytes = uncompressed_tar.read_bytes()
            with open(target_tar, "wb") as out_fp:
                with gzip.GzipFile(filename="", mode="wb", fileobj=out_fp, mtime=0.0) as gz:
                    gz.write(raw_bytes)
            if uncompressed_tar.exists():
                uncompressed_tar.unlink()
            rule_records.append(
                NormalizationRuleRecord(
                    rule_id="GZIP_HEADER_MTIME_ZEROED",
                    artifact_format="gzip",
                    metadata_field="gzip_header.mtime",
                    original_value="compression_wall_clock",
                    normalized_value="0.0",
                    rationale="RFC 1952 GZIP MTIME bytes encode compression timestamp.",
                    affects_runtime_semantics=False,
                    enabled=True,
                )
            )
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
            rule_records=rule_records,
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
