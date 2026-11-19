use serde::{Deserialize, Serialize};

#[allow(dead_code)]
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct MockDataset {
    pub summary_metrics: SummaryMetrics,
    pub builds: Vec<MockBuildRow>,
}

#[allow(dead_code)]
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SummaryMetrics {
    pub total_builds: usize,
    pub bit_for_bit_rate: f64,
    pub normalized_rate: f64,
    pub unresolved_count: usize,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct MockBuildRow {
    pub build_id: String,
    pub commit_sha: String,
    pub backend: String,
    pub status: String,
    pub verdict: String,
    pub duration_seconds: f64,
}

#[derive(Debug, Clone)]
pub struct MockRepo {
    pub id: String,
    pub name: String,
    pub remote_url: String,
    pub default_branch: String,
    pub build_system: String,
    pub revisions_count: i32,
}

#[derive(Debug, Clone)]
pub struct MockRevision {
    pub commit_sha: String,
    pub author: String,
    pub message: String,
    pub timestamp: String,
    pub is_pinned: bool,
}

#[derive(Debug, Clone)]
pub struct MockDiffEntry {
    pub path: String,
    pub change_type: String,
    pub detail: String,
}

pub fn generate_mock_builds(n: usize) -> Vec<MockBuildRow> {
    let mut builds = Vec::with_capacity(n);
    let backends = ["docker", "podman", "buildkit"];
    let verdicts = [
        "bit_for_bit_reproducible",
        "normalized_reproducible",
        "partially_reproducible",
        "inconclusive",
    ];

    for i in 0..n {
        builds.push(MockBuildRow {
            build_id: format!("bld-rec-{:05}", i + 1),
            commit_sha: format!("{:07x}f4", (i * 7919) & 0x0fffffff),
            backend: backends[i % backends.len()].to_string(),
            status: if i % 17 == 0 { "failed".to_string() } else { "completed".to_string() },
            verdict: verdicts[i % verdicts.len()].to_string(),
            duration_seconds: 12.0 + ((i % 50) as f64) * 0.5,
        });
    }
    builds
}

pub fn get_sample_repositories() -> Vec<MockRepo> {
    vec![
        MockRepo {
            id: "repo-01".into(),
            name: "reproforge-core".into(),
            remote_url: "https://github.com/reproforge/reproforge".into(),
            default_branch: "main".into(),
            build_system: "Dockerfile / Python".into(),
            revisions_count: 84,
        },
        MockRepo {
            id: "repo-02".into(),
            name: "sample-docker-app".into(),
            remote_url: "https://github.com/reproforge/sample-docker-app".into(),
            default_branch: "release/v1.0".into(),
            build_system: "BuildKit Multi-stage".into(),
            revisions_count: 52,
        },
        MockRepo {
            id: "repo-03".into(),
            name: "wheel-packaging-test".into(),
            remote_url: "https://github.com/reproforge/wheel-packaging-test".into(),
            default_branch: "master".into(),
            build_system: "pyproject.toml / Flit".into(),
            revisions_count: 64,
        },
    ]
}

pub fn get_sample_revisions() -> Vec<MockRevision> {
    vec![
        MockRevision {
            commit_sha: "7745f0a8".into(),
            author: "Lead DevOps Engineer <dev@reproforge.org>".into(),
            message: "bench(milestone-b): dual-backend Iced and scaling benchmarks".into(),
            timestamp: "2026-11-19 12:50 UTC".into(),
            is_pinned: true,
        },
        MockRevision {
            commit_sha: "45f798d1".into(),
            author: "Lead DevOps Engineer <dev@reproforge.org>".into(),
            message: "feat(desktop): add Iced tiny-skia prototype".into(),
            timestamp: "2026-11-19 12:16 UTC".into(),
            is_pinned: false,
        },
        MockRevision {
            commit_sha: "88d56b8e".into(),
            author: "Lead DevOps Engineer <dev@reproforge.org>".into(),
            message: "feat(phase-5a): shared application services and persistence".into(),
            timestamp: "2026-11-17 06:18 UTC".into(),
            is_pinned: false,
        },
        MockRevision {
            commit_sha: "a3dda85b".into(),
            author: "Lead DevOps Engineer <dev@reproforge.org>".into(),
            message: "feat(phase-4): diagnostic engine and evidence model".into(),
            timestamp: "2026-11-16 23:25 UTC".into(),
            is_pinned: false,
        },
    ]
}

pub fn get_sample_diff_entries() -> Vec<MockDiffEntry> {
    vec![
        MockDiffEntry {
            path: "opt/reproforge/bin/reproforge".into(),
            change_type: "identical".into(),
            detail: "Bit-for-bit cryptographic SHA-256 match".into(),
        },
        MockDiffEntry {
            path: "opt/reproforge/lib/site-packages/pkg_info.json".into(),
            change_type: "metadata_drift".into(),
            detail: "mtime skewed by 3600s; normalized byte hash matches".into(),
        },
        MockDiffEntry {
            path: "opt/reproforge/etc/reproforge.conf".into(),
            change_type: "identical".into(),
            detail: "Bit-for-bit cryptographic SHA-256 match".into(),
        },
        MockDiffEntry {
            path: "opt/reproforge/share/locale/en/LC_MESSAGES/core.mo".into(),
            change_type: "metadata_drift".into(),
            detail: "File permissions: 0644 vs 0755; umask normalization applies".into(),
        },
        MockDiffEntry {
            path: "opt/reproforge/lib/site-packages/__pycache__/app.cpython-314.pyc".into(),
            change_type: "metadata_drift".into(),
            detail: "PEP 552 hash-based pyc header timestamp drift".into(),
        },
    ]
}
