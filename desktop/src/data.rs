#[derive(Clone, Debug)]
pub struct BuildRecord {
    pub build_id: String,
    pub repo: String,
    pub commit: String,
    pub backend: String,
    pub status: String,
    pub verdict: String,
    pub hash_short: String,
}

#[derive(Clone, Debug)]
pub struct RepoRecord {
    pub id: String,
    pub name: String,
    pub url: String,
    pub branch: String,
    pub build_system: String,
    pub revisions_count: i32,
    pub github_slug: String,
    pub upstream_url: String,
    pub readiness_status: String,
    pub readiness_warnings: String,
    pub last_scanned: String,
    pub primary_config: String,
    pub lockfile_path: String,
    pub declared_deps: i32,
    pub suggested_cmd: String,
}

#[derive(Clone, Debug)]
pub struct RevisionRecord {
    pub sha: String,
    pub message: String,
    pub author: String,
    pub date: String,
}

#[derive(Clone, Debug)]
pub struct WorkspaceCandidateRecord {
    pub name: String,
    pub repo_path: String,
    pub default_branch: String,
    pub github_slug: String,
    pub upstream_url: String,
    pub already_tracked: bool,
    pub checked: bool,
}

/// Used strictly when REPROFORGE_BENCH_RECORDS is set by desktop/benchmarks/run_desktop_bench.py
/// to stress-test ListView virtualization and first-frame presentation scaling (N=200..50,000).
pub fn generate_benchmark_builds(count: usize) -> Vec<BuildRecord> {
    let verdicts = ["BIT_FOR_BIT", "NORMALIZED", "DRIFT"];
    (0..count)
        .map(|i| BuildRecord {
            build_id: format!("bench-{:05}", i + 1),
            repo: format!("bench-repo-{}", (i % 5) + 1),
            commit: format!("{:07x}", 0x1000000 + i),
            backend: if i % 2 == 0 { "docker".into() } else { "local".into() },
            status: "succeeded".into(),
            verdict: verdicts[i % verdicts.len()].into(),
            hash_short: format!("sha256:{:012x}", 0xabcdef000000u64 + i as u64),
        })
        .collect()
}
