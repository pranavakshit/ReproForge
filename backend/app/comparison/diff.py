"""Structural differential engine comparing unpacked artifact trees."""

from pathlib import Path
from typing import Dict, List, Optional
from pydantic import BaseModel, Field

from backend.app.comparison.hasher import ArtifactHasher
from backend.app.comparison.unpacker import ArchiveUnpacker, ArtifactEntry, ArtifactTree
from backend.app.core.logging import get_logger
from backend.app.normalization.normalizer import ArtifactNormalizer
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

    def compare_artifacts(self, path1: Path, path2: Path, work_dir: Path) -> ArtifactComparisonSummary:
        """End-to-end comparison: hashes, normalizes, unpacks, and compares two artifacts."""
        work_dir.mkdir(parents=True, exist_ok=True)
        unpack_1 = work_dir / "unpack_1"
        unpack_2 = work_dir / "unpack_2"
        norm_dir = work_dir / "normalized"

        # 1. Strict Hashes
        sha1 = ArtifactHasher.compute_sha256(path1)
        sha2 = ArtifactHasher.compute_sha256(path2)

        # 2. Normalized Hashes
        norm_res1 = self.normalizer.normalize(path1, norm_dir / "art1")
        norm_res2 = self.normalizer.normalize(path2, norm_dir / "art2")

        # 3. Unpack Trees
        tree1 = ArchiveUnpacker.unpack(path1, unpack_1)
        tree2 = ArchiveUnpacker.unpack(path2, unpack_2)

        return self.compare_trees(
            tree1=tree1,
            tree2=tree2,
            strict_sha1=sha1,
            strict_sha2=sha2,
            norm_sha1=norm_res1.normalized_sha256,
            norm_sha2=norm_res2.normalized_sha256,
        )
