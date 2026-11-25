"""Structural differential engine comparing unpacked artifact trees."""

import difflib
from pathlib import Path
import tarfile
import tempfile
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

from backend.app.comparison.hasher import ArtifactHasher
from backend.app.comparison.oci import OCIImageInspector
from backend.app.comparison.unpacker import ArchiveUnpacker, ArtifactEntry, ArtifactTree
from backend.app.core.logging import get_logger
from backend.app.normalization.normalizer import ArtifactNormalizer, NormalizationRuleRecord
from backend.app.schemas.entities import DiffFindingItem

logger = get_logger("reproforge.comparison.diff")


class ArtifactComparisonSummary(BaseModel):
    """Aggregate differential comparison metrics between two artifacts."""

    artifact_1_path: str
    artifact_2_path: str
    strict_sha_1: str
    strict_sha_2: str
    normalized_sha_1: str
    normalized_sha_2: str
    exact_match: bool
    normalized_match: bool
    content_agreement_ratio: float
    metadata_agreement_ratio: float
    total_files_evaluated: int
    added_files_count: int
    removed_files_count: int
    modified_content_count: int
    modified_metadata_count: int
    findings: List[DiffFindingItem] = Field(default_factory=list)
    normalization_rules_1: List[NormalizationRuleRecord] = Field(default_factory=list)
    normalization_rules_2: List[NormalizationRuleRecord] = Field(default_factory=list)
    oci_summary: Optional[Dict[str, Any]] = None


class StructuralDiffEngine:
    """Performs deep pairwise structural and metadata differential analysis."""

    def __init__(self, normalizer: Optional[ArtifactNormalizer] = None) -> None:
        self.normalizer = normalizer or ArtifactNormalizer()

    def compare_trees(
        self,
        tree1: ArtifactTree,
        tree2: ArtifactTree,
        strict_sha1: str,
        strict_sha2: str,
        norm_sha1: Optional[str] = None,
        norm_sha2: Optional[str] = None,
    ) -> ArtifactComparisonSummary:
        """Compare two unpacked artifact trees and generate granular diff findings."""
        paths1 = {p for p, e in tree1.entries.items() if not e.is_dir}
        paths2 = {p for p, e in tree2.entries.items() if not e.is_dir}
        all_paths = paths1 | paths2

        findings: List[DiffFindingItem] = []
        identical_content_count = 0
        identical_metadata_count = 0
        added_count = 0
        removed_count = 0
        modified_content_count = 0
        modified_meta_count = 0

        for path in sorted(all_paths):
            e1 = tree1.get_entry(path)
            e2 = tree2.get_entry(path)

            if e1 and not e2:
                # Removed in tree2
                removed_count += 1
                findings.append(
                    DiffFindingItem(
                        file_path=path,
                        diff_type="REMOVED",
                        strict_match=False,
                        normalized_match=False,
                        size_delta_bytes=-e1.size_bytes,
                        details={"original_sha256": e1.sha256, "size_1": e1.size_bytes},
                    )
                )
            elif e2 and not e1:
                # Added in tree2
                added_count += 1
                findings.append(
                    DiffFindingItem(
                        file_path=path,
                        diff_type="ADDED",
                        strict_match=False,
                        normalized_match=False,
                        size_delta_bytes=e2.size_bytes,
                        details={"target_sha256": e2.sha256, "size_2": e2.size_bytes},
                    )
                )
            elif e1 and e2:
                # Present in both
                content_match = e1.sha256 == e2.sha256
                meta_match = (
                    e1.mtime == e2.mtime
                    and e1.mode == e2.mode
                    and e1.uid == e2.uid
                    and e1.gid == e2.gid
                )

                if content_match:
                    identical_content_count += 1

                if meta_match:
                    identical_metadata_count += 1

                if not content_match:
                    modified_content_count += 1
                    findings.append(
                        DiffFindingItem(
                            file_path=path,
                            diff_type="MODIFIED_CONTENT",
                            strict_match=False,
                            normalized_match=False,
                            size_delta_bytes=e2.size_bytes - e1.size_bytes,
                            details={
                                "sha256_1": e1.sha256,
                                "sha256_2": e2.sha256,
                                "mtime_1": e1.mtime,
                                "mtime_2": e2.mtime,
                            },
                        )
                    )
                elif not meta_match:
                    # Content is identical, but metadata differs!
                    modified_meta_count += 1
                    findings.append(
                        DiffFindingItem(
                            file_path=path,
                            diff_type="METADATA_ONLY",
                            strict_match=False,
                            normalized_match=True,  # Normalized match holds!
                            size_delta_bytes=0,
                            details={
                                "sha256": e1.sha256,
                                "mtime_1": e1.mtime,
                                "mtime_2": e2.mtime,
                                "mode_1": oct(e1.mode),
                                "mode_2": oct(e2.mode),
                            },
                        )
                    )

        total_files = len(all_paths)
        content_ratio = round(identical_content_count / total_files, 4) if total_files > 0 else 1.0
        meta_ratio = round(identical_metadata_count / total_files, 4) if total_files > 0 else 1.0

        exact_match = strict_sha1 == strict_sha2
        normalized_match = (norm_sha1 == norm_sha2) if (norm_sha1 and norm_sha2) else (content_ratio == 1.0)

        return ArtifactComparisonSummary(
            artifact_1_path=tree1.source_path,
            artifact_2_path=tree2.source_path,
            strict_sha_1=strict_sha1,
            strict_sha_2=strict_sha2,
            normalized_sha_1=norm_sha1 or strict_sha1,
            normalized_sha_2=norm_sha2 or strict_sha2,
            exact_match=exact_match,
            normalized_match=normalized_match,
            content_agreement_ratio=content_ratio,
            metadata_agreement_ratio=meta_ratio,
            total_files_evaluated=total_files,
            added_files_count=added_count,
            removed_files_count=removed_count,
            modified_content_count=modified_content_count,
            modified_metadata_count=modified_meta_count,
            findings=findings,
        )

    @staticmethod
    def _extract_text_diff_snippet(file1: Path, file2: Path, max_bytes: int = 128 * 1024, max_chars: int = 2000) -> Optional[str]:
        """Extract bounded UTF-8 unified diff snippet for small modified text/config files."""
        try:
            if not (file1.is_file() and file2.is_file()):
                return None
            if file1.stat().st_size > max_bytes or file2.stat().st_size > max_bytes:
                return None
            raw1 = file1.read_bytes()
            raw2 = file2.read_bytes()
            if b"\x00" in raw1 or b"\x00" in raw2:
                return None
            lines1 = raw1.decode("utf-8").splitlines(keepends=True)
            lines2 = raw2.decode("utf-8").splitlines(keepends=True)
            diff_text = "".join(
                difflib.unified_diff(lines1, lines2, fromfile=file1.name, tofile=file2.name, n=1)
            )
            return diff_text[:max_chars] if diff_text else None
        except Exception:
            return None

    @staticmethod
    def _is_oci_tarball(artifact_path: Path) -> bool:
        """Return True if artifact is a Docker/OCI image tarball containing manifest.json."""
        name = artifact_path.name.lower()
        if not artifact_path.is_file() or not name.endswith((".tar", ".tar.gz", ".tgz")):
            return False
        try:
            with tarfile.open(artifact_path, "r:*") as tf:
                names = set(tf.getnames())
                return "manifest.json" in names
        except Exception:
            return False

    def compare_artifacts(self, path1: Path, path2: Path, work_dir: Path) -> ArtifactComparisonSummary:
        """End-to-end comparison inside an isolated ephemeral temp subdir: hashes, normalizes, unpacks, & compares."""
        work_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=work_dir, prefix="cmp_run_") as run_tmp:
            run_root = Path(run_tmp)
            unpack_1 = run_root / "unpack_1"
            unpack_2 = run_root / "unpack_2"
            norm_dir = run_root / "normalized"

            # 1. Strict Hashes
            sha1 = ArtifactHasher.compute_sha256(path1)
            sha2 = ArtifactHasher.compute_sha256(path2)

            # 2. Normalized Hashes + Audit Rule Records
            norm_res1 = self.normalizer.normalize(path1, norm_dir / "art1")
            norm_res2 = self.normalizer.normalize(path2, norm_dir / "art2")

            # 3. Unpack Trees
            tree1 = ArchiveUnpacker.unpack(path1, unpack_1)
            tree2 = ArchiveUnpacker.unpack(path2, unpack_2)

            summary = self.compare_trees(
                tree1=tree1,
                tree2=tree2,
                strict_sha1=sha1,
                strict_sha2=sha2,
                norm_sha1=norm_res1.normalized_sha256,
                norm_sha2=norm_res2.normalized_sha256,
            )
            summary.normalization_rules_1 = norm_res1.rule_records
            summary.normalization_rules_2 = norm_res2.rule_records

            # 4. Attach bounded text diff snippets to MODIFIED_CONTENT findings
            for f in summary.findings:
                if f.diff_type == "MODIFIED_CONTENT":
                    snippet = self._extract_text_diff_snippet(unpack_1 / f.file_path, unpack_2 / f.file_path)
                    if snippet:
                        f.details["diff_snippet"] = snippet

            # 5. Deep OCI/Docker container inspection if both are OCI tarballs
            if self._is_oci_tarball(path1) and self._is_oci_tarball(path2):
                try:
                    oci1 = OCIImageInspector.inspect(path1, run_root / "oci1", unpack_layers=False)
                    oci2 = OCIImageInspector.inspect(path2, run_root / "oci2", unpack_layers=False)
                    layer_shas_1 = [l.layer_tar_sha256 for l in oci1.layers]
                    layer_shas_2 = [l.layer_tar_sha256 for l in oci2.layers]
                    summary.oci_summary = {
                        "total_layers_1": oci1.total_layers,
                        "total_layers_2": oci2.total_layers,
                        "arch_1": oci1.architecture,
                        "arch_2": oci2.architecture,
                        "layers_match": layer_shas_1 == layer_shas_2,
                    }
                    for f in summary.findings:
                        f.details["docker_oci_image"] = True
                except Exception as exc:
                    logger.debug(f"OCI deep inspection skipped: {exc}")

            return summary
