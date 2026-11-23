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
    let authentic_records = [
        ("bld-repr-00001", "93075a4", "isolated-local", "completed", "bit_for_bit_reproducible", 2.3),
        ("bld-repr-00002", "eb52168", "isolated-local", "completed", "bit_for_bit_reproducible", 3.1),
        ("bld-repr-00003", "99d364a", "isolated-local", "completed", "bit_for_bit_reproducible", 1.9),
        ("bld-loca-00001", "227e876", "isolated-local", "completed", "normalized_reproducible", 4.5),
        ("bld-loca-00002", "e14ab0f", "isolated-local", "completed", "normalized_reproducible", 4.2),
        ("bld-offl-00001", "15a3edd", "isolated-local", "completed", "normalized_reproducible", 3.8),
        ("bld-offl-00002", "593cab9", "isolated-local", "completed", "normalized_reproducible", 3.6),
        ("bld-rgbk-00001", "b31dad8", "isolated-local", "completed", "bit_for_bit_reproducible", 1.2),
        ("bld-sshe-00001", "e105656", "isolated-local", "completed", "bit_for_bit_reproducible", 2.7),
        ("bld-sshe-00002", "9ac39e4", "isolated-local", "completed", "bit_for_bit_reproducible", 2.9),
    ];

    let mut builds = Vec::with_capacity(n.max(authentic_records.len()));
    for r in &authentic_records {
        builds.push(MockBuildRow {
            build_id: r.0.to_string(),
            commit_sha: r.1.to_string(),
            backend: r.2.to_string(),
            status: r.3.to_string(),
            verdict: r.4.to_string(),
            duration_seconds: r.5,
        });
    }

    // If a benchmark requested extra records (e.g. 200 or 10000), synthesize from authentic seeds
    if n > authentic_records.len() {
        let backends = ["isolated-local", "docker-buildkit", "local-sandbox"];
        let verdicts = [
            "bit_for_bit_reproducible",
            "normalized_reproducible",
            "partially_reproducible",
            "inconclusive",
        ];
        for i in authentic_records.len()..n {
            builds.push(MockBuildRow {
                build_id: format!("bld-run-{:05}", i + 1),
                commit_sha: format!("{:07x}f4", (i * 7919) & 0x0fffffff),
                backend: backends[i % backends.len()].to_string(),
                status: if i % 23 == 0 { "failed".to_string() } else { "completed".to_string() },
                verdict: verdicts[i % verdicts.len()].to_string(),
                duration_seconds: 1.5 + ((i % 30) as f64) * 0.2,
            });
        }
    }

    builds
}

pub fn get_sample_repositories() -> Vec<MockRepo> {
    vec![
        MockRepo {
            id: "repo-reproforge".into(),
            name: "ReproForge".into(),
            remote_url: "D:/Repositories/ReproForge".into(),
            default_branch: "main".into(),
            build_system: "Cargo / Python (pyproject.toml)".into(),
            revisions_count: 14,
        },
        MockRepo {
            id: "repo-locai".into(),
            name: "LocAi".into(),
            remote_url: "D:/Repositories/LocAi".into(),
            default_branch: "main".into(),
            build_system: "FastAPI / React / build.bat".into(),
            revisions_count: 99,
        },
        MockRepo {
            id: "repo-offlinedoc".into(),
            name: "offline-doc-assistant".into(),
            remote_url: "D:/Repositories/offline-doc-assistant".into(),
            default_branch: "main".into(),
            build_system: "Python / Qwen2.5 Hybrid Search".into(),
            revisions_count: 34,
        },
        MockRepo {
            id: "repo-rgbtoolkit".into(),
            name: "4-Zone-Keyboard-RGB-Toolkit".into(),
            remote_url: "D:/Repositories/4-Zone-Keyboard-RGB-Toolkit".into(),
            default_branch: "feature/start-minimized".into(),
            build_system: "Python / PySide Desktop App".into(),
            revisions_count: 149,
        },
        MockRepo {
            id: "repo-ssheasy".into(),
            name: "ssheasy".into(),
            remote_url: "D:/Repositories/ssheasy".into(),
            default_branch: "master".into(),
            build_system: "Docker Compose / Go / Web".into(),
            revisions_count: 54,
        },
    ]
}

pub fn get_sample_revisions() -> Vec<MockRevision> {
    vec![
        MockRevision {
            commit_sha: "93075a4".into(),
            author: "Pranav Akshit <pranavakshit05@gmail.com>".into(),
            message: "feat(desktop): implement live overview, repository browser, and streaming build execution (Milestone E)".into(),
            timestamp: "Mon Nov 23 13:40:15 2026 +0530".into(),
            is_pinned: true,
        },
        MockRevision {
            commit_sha: "eb52168".into(),
            author: "Pranav Akshit <pranavakshit05@gmail.com>".into(),
            message: "feat(ipc): implement Rust child process supervisor, crash auto-restart, and Slint UI integration (Milestone D)".into(),
            timestamp: "Thu Nov 19 13:41:00 2026 +0530".into(),
            is_pinned: false,
        },
        MockRevision {
            commit_sha: "99d364a".into(),
            author: "Pranav Akshit <pranavakshit05@gmail.com>".into(),
            message: "feat(ipc): implement Python background engine daemon, dispatcher, and stdout isolation".into(),
            timestamp: "Thu Nov 19 13:29:26 2026 +0530".into(),
            is_pinned: false,
        },
        MockRevision {
            commit_sha: "e1a062e".into(),
            author: "Pranav Akshit <pranavakshit05@gmail.com>".into(),
            message: "feat(milestone-c): implement permanent Slint desktop client with 5 interactive views and hardware/software rendering".into(),
            timestamp: "Thu Nov 19 12:58:12 2026 +0530".into(),
            is_pinned: false,
        },
    ]
}

pub fn get_sample_diff_entries() -> Vec<MockDiffEntry> {
    vec![
        MockDiffEntry {
            path: "a_chunk.txt".into(),
            change_type: "METADATA_ONLY".into(),
            detail: "Timestamp drift detected in tarinfo mtime; byte contents match 100%".into(),
        },
        MockDiffEntry {
            path: "b_index.txt".into(),
            change_type: "METADATA_ONLY".into(),
            detail: "Timestamp drift detected in tarinfo mtime; byte contents match 100%".into(),
        },
        MockDiffEntry {
            path: "config.json".into(),
            change_type: "METADATA_ONLY".into(),
            detail: "Timestamp drift detected in tarinfo mtime; byte contents match 100%".into(),
        },
        MockDiffEntry {
            path: "reproforge/__init__.py".into(),
            change_type: "identical".into(),
            detail: "Bit-for-bit cryptographic SHA-256 match".into(),
        },
        MockDiffEntry {
            path: "reproforge/core.py".into(),
            change_type: "identical".into(),
            detail: "Bit-for-bit cryptographic SHA-256 match".into(),
        },
    ]
}
