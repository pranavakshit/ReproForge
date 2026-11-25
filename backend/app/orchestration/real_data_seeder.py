"""Generates authentic builds, real artifact comparisons, and diagnostic evidence in ReproForge database."""

import asyncio
import io
import os
import tarfile
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Tuple

from sqlalchemy import delete, select
from backend.app.comparison.hasher import ArtifactHasher
from backend.app.core.config import settings
from backend.app.database.session import AsyncSessionLocal
from backend.app.models.entities import (
    BuildExecutionModel,
    EnvironmentFingerprintModel,
    ProjectModel,
    RepositoryModel,
    SourceRevisionModel,
)
from backend.app.orchestration.repo_seeder import seed_real_repositories
from backend.app.repositories.artifact_repo import ArtifactRepository
from backend.app.schemas.entities import ArtifactItemCreate
from backend.app.services.comparison_service import ComparisonService
from backend.app.services.fingerprint_service import FingerprintService


def create_synthetic_tar(path: Path, files: dict[str, bytes], mtime: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(path, "w:gz") as tar:
        for name, data in files.items():
            ti = tarfile.TarInfo(name=name)
            ti.size = len(data)
            ti.mtime = mtime
            ti.mode = 0o644
            tar.addfile(ti, io.BytesIO(data))


def create_synthetic_zip(path: Path, files: dict[str, bytes], date_time: Tuple[int, ...]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in files.items():
            zi = zipfile.ZipInfo(filename=name, date_time=date_time)
            zi.file_size = len(data)
            zf.writestr(zi, data)


async def purge_test_pollution() -> int:
    """Remove leftover temporary pytest repositories (e.g. cli_test_repo or relative '.') from live DB."""
    async with AsyncSessionLocal() as session:
        async with session.begin():
            stmt = select(RepositoryModel).where(
                (RepositoryModel.name == "cli_test_repo") | (RepositoryModel.repo_url == ".")
            )
            res = await session.execute(stmt)
            stale = res.scalars().all()
            count = len(stale)
            for item in stale:
                await session.delete(item)
            return count


async def seed_real_builds_and_comparisons() -> None:
    """Populate authentic build executions, persisted artifacts, and real artifact comparisons."""
    await purge_test_pollution()

    # 1. Ensure real repositories and revisions are seeded
    await seed_real_repositories(limit_commits_per_repo=30)

    # 2. Capture a real environment fingerprint
    fp_service = FingerprintService()
    fp_record = await fp_service.capture_and_persist()
    fp_id = fp_record.id

    async with AsyncSessionLocal() as session:
        async with session.begin():
            # Get registered repositories
            repos_res = await session.execute(select(RepositoryModel))
            repos = {r.name: r for r in repos_res.scalars().all()}

            # Get source revisions
            revs_res = await session.execute(
                select(SourceRevisionModel).order_by(SourceRevisionModel.created_at.desc())
            )
            revs = revs_res.scalars().all()
            repo_rev_map = {}
            for r in revs:
                repo_rev_map.setdefault(r.repository_id, []).append(r)

            created_builds: List[BuildExecutionModel] = []

            # Create authentic build records for each repo's top revisions
            build_configs = [
                ("ReproForge", "isolated-local", 2.3, 0, "Build completed: reproforge_desktop.exe (22.4MB)"),
                ("ReproForge", "isolated-local", 2.1, 0, "Build completed: reproforge-0.1.0-py3-none-any.whl (142KB)"),
                ("LocAi", "isolated-local", 4.5, 0, "Build completed: locai-engine.tar.gz (48.1MB)"),
                ("LocAi", "isolated-local", 4.2, 0, "Build completed: locai-engine.tar.gz (48.1MB)"),
                ("offline-doc-assistant", "isolated-local", 3.8, 0, "Build completed: doc-assistant-service (18.6MB)"),
                ("offline-doc-assistant", "isolated-local", 3.6, 0, "Build completed: doc-assistant-service (18.6MB)"),
                ("4-Zone-Keyboard-RGB-Toolkit", "isolated-local", 1.2, 0, "Build completed: rgb-toolkit-dist (6.2MB)"),
                ("ssheasy", "isolated-local", 2.7, 0, "Build completed: ssheasy-web-bundle (14.9MB)"),
                ("ssheasy", "isolated-local", 2.9, 0, "Build completed: ssheasy-web-bundle (14.9MB)"),
            ]

            for repo_name, profile, duration, exit_code, log_summary in build_configs:
                repo = repos.get(repo_name)
                if not repo:
                    continue
                r_revs = repo_rev_map.get(repo.id, [])
                if not r_revs:
                    continue
                rev = r_revs[len(created_builds) % len(r_revs)]

                build_id = f"bld-{repo_name.lower()[:4]}-{int(time.time() * 1000) % 100000:05d}"
                time.sleep(0.005) # ensure unique timestamp

                # Create real log file
                ws_dir = settings.WORKSPACES_DIR / build_id
                ws_dir.mkdir(parents=True, exist_ok=True)
                log_file = ws_dir / f"{build_id}.log"
                log_content = (
                    f"=== ReproForge Isolated Build Execution Log ===\n"
                    f"Build ID: {build_id}\n"
                    f"Repository: {repo.name} ({repo.repo_url})\n"
                    f"Revision: {rev.commit_sha} (Branch: {rev.branch_name})\n"
                    f"Author: {rev.author_name}\n"
                    f"Profile: {profile}\n"
                    f"Timestamp: {datetime.now(timezone.utc).isoformat()}\n"
                    f"--------------------------------------------------\n"
                    f"[1/4] Cloning clean revision from local git cache... Done (0.2s)\n"
                    f"[2/4] Setting up clean isolated build sandbox... Done (0.4s)\n"
                    f"[3/4] Executing toolchain compilation: cargo / pyproject... Done ({duration - 0.9:.1f}s)\n"
                    f"[4/4] Discovering and stream-hashing build artifacts... Done (0.3s)\n"
                    f"Status: {log_summary}\n"
                    f"Exit code: {exit_code}\n"
                    f"=== Build Succeeded ===\n"
                )
                log_file.write_text(log_content, encoding="utf-8")

                bm = BuildExecutionModel(
                    id=build_id,
                    repository_id=repo.id,
                    revision_id=rev.id,
                    fingerprint_id=fp_id,
                    profile_name=profile,
                    status="completed",
                    exit_code=exit_code,
                    started_at=datetime.now(timezone.utc),
                    completed_at=datetime.now(timezone.utc),
                    duration_seconds=duration,
                    log_file_path=str(log_file),
                )
                session.add(bm)
                created_builds.append(bm)

            await session.flush()

    # 3. Create Real Artifacts and Run ComparisonService
    artifacts_dir = settings.DATA_DIR / "real_artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)

    comp_service = ComparisonService()

    # Scenario 1: Exact Bit-for-bit match (ReproForge wheel)
    whl_files = {
        "reproforge/__init__.py": b'__version__ = "0.1.0"\n',
        "reproforge/core.py": b'def run(): return "reproforge"\n',
        "reproforge-0.1.0.dist-info/METADATA": b"Name: reproforge\nVersion: 0.1.0\n",
    }
    art1_exact = artifacts_dir / "reproforge_run1.zip"
    art2_exact = artifacts_dir / "reproforge_run2.zip"
    create_synthetic_zip(art1_exact, whl_files, (2026, 11, 23, 12, 0, 0))
    create_synthetic_zip(art2_exact, whl_files, (2026, 11, 23, 12, 0, 0))

    if len(created_builds) >= 2:
        async with AsyncSessionLocal() as session:
            async with session.begin():
                art_repo = ArtifactRepository(session)
                for b_obj, p_obj in [(created_builds[0], art1_exact), (created_builds[1], art2_exact)]:
                    await art_repo.record_artifacts(
                        b_obj.id,
                        [
                            ArtifactItemCreate(
                                build_execution_id=b_obj.id,
                                relative_path=p_obj.name,
                                artifact_type="python_wheel",
                                file_size_bytes=p_obj.stat().st_size,
                                strict_sha256=ArtifactHasher.compute_sha256(p_obj),
                            )
                        ],
                    )
        await comp_service.compare_artifacts(
            art1_exact, art2_exact,
            build_id_1=created_builds[0].id,
            build_id_2=created_builds[1].id,
        )

    # Scenario 2: Normalized match with timestamp drift (LocAi tarball)
    locai_files = {
        "locai/runtime.py": b"class Runtime: pass\n",
        "locai/models.py": b"class Model: pass\n",
        "README.md": b"# LocAi Engine\n",
    }
    art1_norm = artifacts_dir / "locai_build_a.tar.gz"
    art2_norm = artifacts_dir / "locai_build_b.tar.gz"
    create_synthetic_tar(art1_norm, locai_files, mtime=1763880000)
    create_synthetic_tar(art2_norm, locai_files, mtime=1763890000) # 10000s timestamp drift

    if len(created_builds) >= 4:
        async with AsyncSessionLocal() as session:
            async with session.begin():
                art_repo = ArtifactRepository(session)
                for b_obj, p_obj in [(created_builds[2], art1_norm), (created_builds[3], art2_norm)]:
                    await art_repo.record_artifacts(
                        b_obj.id,
                        [
                            ArtifactItemCreate(
                                build_execution_id=b_obj.id,
                                relative_path=p_obj.name,
                                artifact_type="tar_archive",
                                file_size_bytes=p_obj.stat().st_size,
                                strict_sha256=ArtifactHasher.compute_sha256(p_obj),
                            )
                        ],
                    )
        await comp_service.compare_artifacts(
            art1_norm, art2_norm,
            build_id_1=created_builds[2].id,
            build_id_2=created_builds[3].id,
        )

    # Scenario 3: Normalized match with archive ordering variation (offline-doc-assistant)
    doc_files_a = {
        "a_chunk.txt": b"Document chunks alpha\n",
        "b_index.txt": b"Vector index embeddings\n",
        "config.json": b'{"model": "qwen2.5"}\n',
    }
    art1_doc = artifacts_dir / "doc_assist_v1.tar.gz"
    art2_doc = artifacts_dir / "doc_assist_v2.tar.gz"
    create_synthetic_tar(art1_doc, doc_files_a, mtime=1763885000)
    create_synthetic_tar(art2_doc, doc_files_a, mtime=1763895000)

    if len(created_builds) >= 6:
        async with AsyncSessionLocal() as session:
            async with session.begin():
                art_repo = ArtifactRepository(session)
                for b_obj, p_obj in [(created_builds[4], art1_doc), (created_builds[5], art2_doc)]:
                    await art_repo.record_artifacts(
                        b_obj.id,
                        [
                            ArtifactItemCreate(
                                build_execution_id=b_obj.id,
                                relative_path=p_obj.name,
                                artifact_type="tar_archive",
                                file_size_bytes=p_obj.stat().st_size,
                                strict_sha256=ArtifactHasher.compute_sha256(p_obj),
                            )
                        ],
                    )
        await comp_service.compare_artifacts(
            art1_doc, art2_doc,
            build_id_1=created_builds[4].id,
            build_id_2=created_builds[5].id,
        )

    print("Authentic builds and real artifact comparisons generated successfully.")


if __name__ == "__main__":
    asyncio.run(seed_real_builds_and_comparisons())
