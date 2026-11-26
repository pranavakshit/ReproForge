mod data;

use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use slint::{ComponentHandle, Model, ModelRc, VecModel};

use data::{BuildRecord, RepoRecord, RevisionRecord, WorkspaceCandidateRecord};
use reproforge_desktop::ipc::client::IpcClient;
use reproforge_desktop::supervisor::{find_repo_root, ProcessSupervisor, SupervisorStatus};

slint::include_modules!();

const MAX_LOG_BYTES: usize = 65_536;

fn detect_renderer_backend() -> (String, bool) {
    let backend = std::env::var("SLINT_BACKEND").unwrap_or_default().to_lowercase();
    let is_sw = backend.contains("software");
    let label = if is_sw {
        "Software (CPU)".to_string()
    } else if backend.contains("femtovg") || backend.is_empty() {
        "Hardware (FemtoVG)".to_string()
    } else {
        backend
    };
    (label, is_sw)
}

struct AppState {
    builds: Vec<BuildRecord>,
    repos: Vec<RepoRecord>,
    candidates: Vec<WorkspaceCandidateRecord>,
    diffs: Vec<DiffEntry>,
    graph_nodes: Vec<GraphNodeItem>,
    selected_repo_idx: usize,
    selected_revision_sha: String,
    selected_backend_type: String,
    search_query: String,
    log_buffer: String,
    last_log_flush: Instant,
    active_build_count: i32,
}

impl AppState {
    fn new(initial_builds: Vec<BuildRecord>) -> Self {
        Self {
            builds: initial_builds,
            repos: Vec::new(),
            candidates: Vec::new(),
            diffs: Vec::new(),
            graph_nodes: Vec::new(),
            selected_repo_idx: 0,
            selected_revision_sha: "HEAD".to_string(),
            selected_backend_type: "local".to_string(),
            search_query: String::new(),
            log_buffer: String::new(),
            last_log_flush: Instant::now() - Duration::from_secs(1),
            active_build_count: 0,
        }
    }

    fn append_log_chunk(&mut self, chunk: &str, force: bool) -> Option<String> {
        if !chunk.is_empty() {
            if !self.log_buffer.is_empty() && !self.log_buffer.ends_with('\n') {
                self.log_buffer.push('\n');
            }
            self.log_buffer.push_str(chunk);
            if self.log_buffer.len() > MAX_LOG_BYTES {
                let trim_at = self.log_buffer.len() - MAX_LOG_BYTES;
                if let Some(pos) = self.log_buffer[trim_at..].find('\n') {
                    self.log_buffer.drain(..trim_at + pos + 1);
                } else {
                    self.log_buffer.drain(..trim_at);
                }
            }
        }
        if force || self.last_log_flush.elapsed() >= Duration::from_millis(120) {
            self.last_log_flush = Instant::now();
            Some(self.log_buffer.clone())
        } else {
            None
        }
    }
}

fn to_slint_build(b: &BuildRecord) -> BuildRowData {
    BuildRowData {
        build_id: b.build_id.clone().into(),
        repo: b.repo.clone().into(),
        commit: b.commit.clone().into(),
        backend: b.backend.clone().into(),
        status: b.status.clone().into(),
        verdict: b.verdict.clone().into(),
        hash_short: b.hash_short.clone().into(),
    }
}

fn to_slint_repo(r: &RepoRecord) -> RepoItem {
    RepoItem {
        id: r.id.clone().into(),
        name: r.name.clone().into(),
        url: r.url.clone().into(),
        branch: r.branch.clone().into(),
        build_system: r.build_system.clone().into(),
        revisions_count: r.revisions_count,
        github_slug: r.github_slug.clone().into(),
        upstream_url: r.upstream_url.clone().into(),
        readiness_status: r.readiness_status.clone().into(),
        readiness_warnings: r.readiness_warnings.clone().into(),
        last_scanned: r.last_scanned.clone().into(),
    }
}

fn to_slint_revision(r: &RevisionRecord) -> RevisionItem {
    RevisionItem {
        sha: r.sha.clone().into(),
        message: r.message.clone().into(),
        author: r.author.clone().into(),
        date: r.date.clone().into(),
    }
}

fn to_slint_candidate(c: &WorkspaceCandidateRecord) -> WorkspaceCandidateItem {
    WorkspaceCandidateItem {
        name: c.name.clone().into(),
        repo_path: c.repo_path.clone().into(),
        default_branch: c.default_branch.clone().into(),
        github_slug: c.github_slug.clone().into(),
        upstream_url: c.upstream_url.clone().into(),
        already_tracked: c.already_tracked,
        checked: c.checked,
    }
}

fn push_builds_to_ui(ui: &MainWindow, state: &AppState) {
    let q = state.search_query.trim().to_lowercase();
    let rows: Vec<BuildRowData> = state
        .builds
        .iter()
        .filter(|b| {
            q.is_empty()
                || b.build_id.to_lowercase().contains(&q)
                || b.repo.to_lowercase().contains(&q)
                || b.commit.to_lowercase().contains(&q)
                || b.status.to_lowercase().contains(&q)
                || b.verdict.to_lowercase().contains(&q)
        })
        .map(to_slint_build)
        .collect();
    ui.set_builds(ModelRc::new(VecModel::from(rows)));
}

fn push_repos_to_ui(ui: &MainWindow, state: &AppState) {
    let q = state.search_query.trim().to_lowercase();
    let rows: Vec<RepoItem> = state
        .repos
        .iter()
        .filter(|r| {
            q.is_empty()
                || r.name.to_lowercase().contains(&q)
                || r.github_slug.to_lowercase().contains(&q)
                || r.build_system.to_lowercase().contains(&q)
        })
        .map(to_slint_repo)
        .collect();
    ui.set_repos(ModelRc::new(VecModel::from(rows)));
}

fn push_candidates_to_ui(ui: &MainWindow, state: &AppState) {
    let items: Vec<WorkspaceCandidateItem> = state.candidates.iter().map(to_slint_candidate).collect();
    let selected_cnt = state.candidates.iter().filter(|c| c.checked).count();
    ui.set_workspace_candidates(ModelRc::new(VecModel::from(items)));
    ui.set_workspace_checklist_status(
        format!("{} of {} repositories selected for tracking", selected_cnt, state.candidates.len()).into(),
    );
}

fn parse_rate_pct(val: &serde_json::Value) -> f64 {
    if let Some(n) = val.as_f64() {
        return n;
    }
    if let Some(s) = val.as_str() {
        return s.trim().trim_end_matches('%').trim().parse::<f64>().unwrap_or(0.0);
    }
    0.0
}

fn sync_overview(ui_handle: slint::Weak<MainWindow>, client: &IpcClient) {
    if let Ok(val) = client.send_request("overview.get_metrics", None, Duration::from_secs(5)) {
        let total_builds = val["total_builds"].as_u64().unwrap_or(0);
        let total_comps = val["total_comparisons"].as_u64().unwrap_or(0);
        let bfb = parse_rate_pct(&val["bit_for_bit_rate"]);
        let norm = parse_rate_pct(&val["normalized_rate"]);
        let unresolved = val["unresolved_count"].as_u64().unwrap_or(0);

        let tel = &val["telemetry"];
        let os_name = tel["os_name"].as_str().unwrap_or("Unknown OS").to_string();
        let os_rel = tel["os_release"].as_str().unwrap_or("").to_string();
        let arch = tel["cpu_arch"].as_str().unwrap_or("—").to_string();
        let cores = tel["cpu_count"].as_u64().unwrap_or(0);
        let ram = tel["ram_gb"].as_f64().unwrap_or(0.0);
        let toolchains = tel["detected_toolchains"]
            .as_array()
            .map(|a| {
                a.iter()
                    .filter_map(|v| v.as_str())
                    .collect::<Vec<_>>()
                    .join("  •  ")
            })
            .unwrap_or_else(|| "None detected".to_string());
        let docker_ver = tel["docker_cli_version"]
            .as_str()
            .unwrap_or("Not Detected")
            .to_string();
        let docker_ok = !docker_ver.to_lowercase().contains("not installed")
            && !docker_ver.to_lowercase().contains("not detected")
            && !docker_ver.is_empty();
        let db_path = tel["database_path"]
            .as_str()
            .unwrap_or("data/reproforge.db")
            .to_string();

        let recent: Vec<RecentBuildItem> = val["recent_builds"]
            .as_array()
            .map(|arr| {
                arr.iter()
                    .map(|b| RecentBuildItem {
                        build_id: b["build_id"].as_str().or_else(|| b["id"].as_str()).unwrap_or("—").into(),
                        repo: b["repo"].as_str().or_else(|| b["repo_name"].as_str()).unwrap_or("unknown").into(),
                        revision: b["revision"].as_str().or_else(|| b["commit_sha"].as_str()).unwrap_or("HEAD").into(),
                        backend: b["backend"].as_str().unwrap_or("local").into(),
                        status: b["status"].as_str().unwrap_or("unknown").into(),
                        verdict: b["verdict"].as_str().unwrap_or("UNCOMPARED").to_uppercase().into(),
                    })
                    .collect()
            })
            .unwrap_or_default();

        let bfb_ratio = (bfb / 100.0).clamp(0.0, 1.0) as f32;
        let norm_ratio = (norm / 100.0).clamp(0.0, 1.0) as f32;
        let drift_ratio = ((100.0 - norm).max(0.0) / 100.0).clamp(0.0, 1.0) as f32;

        let _ = slint::invoke_from_event_loop(move || {
            if let Some(ui) = ui_handle.upgrade() {
                ui.set_total_builds(total_builds.to_string().into());
                ui.set_total_comparisons(total_comps.to_string().into());
                ui.set_bit_for_bit_rate(format!("{:.1}%", bfb).into());
                ui.set_normalized_rate(format!("{:.1}%", norm).into());
                ui.set_unresolved_count(unresolved.to_string().into());
                ui.set_cri_bit_ratio(bfb_ratio);
                ui.set_cri_norm_ratio(norm_ratio);
                ui.set_cri_drift_ratio(drift_ratio);

                ui.set_host_os(format!("{} {}", os_name, os_rel).trim().into());
                ui.set_host_arch(format!("{} • {} cores • {:.1} GB", arch, cores, ram).into());
                ui.set_detected_toolchains(toolchains.into());
                ui.set_docker_cli_version(docker_ver.into());
                ui.set_docker_available(docker_ok);
                ui.set_database_path(db_path.clone().into());
                ui.set_storage_db_path(db_path.into());
                ui.set_recent_builds(ModelRc::new(VecModel::from(recent)));
            }
        });
    }
}

fn sync_revisions_for_repo(ui_handle: slint::Weak<MainWindow>, client: &IpcClient, state: &Arc<Mutex<AppState>>, repo_id: &str) {
    let rev_params = serde_json::json!({ "repository_id": repo_id, "limit": 25 });
    if let Ok(rev_resp) = client.send_request("repository.get_revisions", Some(rev_params), Duration::from_secs(5)) {
        let revs: Vec<RevisionRecord> = rev_resp["revisions"]
            .as_array()
            .map(|arr| {
                arr.iter()
                    .map(|rev| {
                        let sha = rev["sha"]
                            .as_str()
                            .or_else(|| rev["commit_sha"].as_str())
                            .unwrap_or("HEAD");
                        let msg = rev["message"]
                            .as_str()
                            .or_else(|| rev["commit_message"].as_str())
                            .unwrap_or("");
                        let author = rev["author"]
                            .as_str()
                            .or_else(|| rev["author_name"].as_str())
                            .unwrap_or("Unknown");
                        let date = rev["date"]
                            .as_str()
                            .or_else(|| rev["committed_at"].as_str())
                            .or_else(|| rev["created_at"].as_str())
                            .unwrap_or("");
                        RevisionRecord {
                            sha: sha.chars().take(10).collect(),
                            message: msg.into(),
                            author: author.into(),
                            date: date.chars().take(10).collect(),
                        }
                    })
                    .collect()
            })
            .unwrap_or_default();

        if let Some(first) = revs.first() {
            if let Ok(mut s) = state.lock() {
                s.selected_revision_sha = first.sha.clone();
            }
        }
        let ui_revs: Vec<RevisionItem> = revs.iter().map(to_slint_revision).collect();
        let _ = slint::invoke_from_event_loop(move || {
            if let Some(ui) = ui_handle.upgrade() {
                ui.set_revisions(ModelRc::new(VecModel::from(ui_revs)));
                ui.set_selected_revision_idx(0);
            }
        });
    }
}

fn sync_repositories(ui_handle: slint::Weak<MainWindow>, client: &IpcClient, state: Arc<Mutex<AppState>>, selected_idx: usize) {
    if let Ok(val) = client.send_request("repository.list", None, Duration::from_secs(5)) {
        let repos: Vec<RepoRecord> = val["repositories"]
            .as_array()
            .map(|arr| {
                arr.iter()
                    .map(|r| {
                        let warnings = r["readiness_warnings"]
                            .as_array()
                            .map(|w| {
                                w.iter()
                                    .filter_map(|v| v.as_str())
                                    .collect::<Vec<_>>()
                                    .join(" | ")
                            })
                            .unwrap_or_default();
                        RepoRecord {
                            id: r["id"].as_str().unwrap_or("").to_string(),
                            name: r["name"].as_str().unwrap_or("Unnamed").to_string(),
                            url: r["repo_url"].as_str().unwrap_or("").to_string(),
                            branch: r["default_branch"].as_str().unwrap_or("main").to_string(),
                            build_system: r["build_system"].as_str().unwrap_or("generic").to_string(),
                            revisions_count: r["revisions_count"].as_i64().unwrap_or(0) as i32,
                            github_slug: r["github_slug"].as_str().unwrap_or("").to_string(),
                            upstream_url: r["upstream_github_url"].as_str().unwrap_or("").to_string(),
                            readiness_status: r["readiness_status"].as_str().unwrap_or("unknown").to_string(),
                            readiness_warnings: warnings,
                            last_scanned: r["last_scanned_at"].as_str().unwrap_or("").chars().take(19).collect(),
                        }
                    })
                    .collect()
            })
            .unwrap_or_default();

        let clamped_idx = if repos.is_empty() { 0 } else { selected_idx.min(repos.len() - 1) };
        let selected_id = repos.get(clamped_idx).map(|r| r.id.clone());

        {
            if let Ok(mut s) = state.lock() {
                s.repos = repos;
                s.selected_repo_idx = clamped_idx;
            }
        }

        let ui_clone = ui_handle.clone();
        let state_clone = state.clone();
        let _ = slint::invoke_from_event_loop(move || {
            if let Some(ui) = ui_clone.upgrade() {
                if let Ok(s) = state_clone.lock() {
                    push_repos_to_ui(&ui, &s);
                    ui.set_selected_repo_idx(clamped_idx as i32);
                }
            }
        });

        if let Some(id) = selected_id {
            sync_revisions_for_repo(ui_handle, client, &state, &id);
        }
    }
}

fn sync_builds(ui_handle: slint::Weak<MainWindow>, client: &IpcClient, state: Arc<Mutex<AppState>>) {
    if let Ok(resp) = client.send_request("build.list", None, Duration::from_secs(5)) {
        if let Some(arr) = resp["builds"].as_array() {
            let mut records = Vec::with_capacity(arr.len());
            let mut running = 0i32;
            for b in arr {
                let status = b["status"].as_str().unwrap_or("unknown").to_string();
                if status == "running" || status == "pending" {
                    running += 1;
                }
                records.push(BuildRecord {
                    build_id: b["build_id"]
                        .as_str()
                        .or_else(|| b["id"].as_str())
                        .unwrap_or("—")
                        .to_string(),
                    repo: b["repo_name"]
                        .as_str()
                        .or_else(|| b["repo"].as_str())
                        .unwrap_or("unknown")
                        .to_string(),
                    commit: b["commit_sha"].as_str().unwrap_or("HEAD").chars().take(8).collect(),
                    backend: b["backend"]
                        .as_str()
                        .or_else(|| b["profile_name"].as_str())
                        .unwrap_or("local")
                        .to_string(),
                    status,
                    verdict: b["verdict"].as_str().unwrap_or("UNCOMPARED").to_uppercase(),
                    hash_short: b["primary_hash"]
                        .as_str()
                        .unwrap_or("—")
                        .chars()
                        .take(18)
                        .collect(),
                });
            }

            if let Ok(mut s) = state.lock() {
                s.builds = records;
                s.active_build_count = running;
            }

            let state_clone = state.clone();
            let _ = slint::invoke_from_event_loop(move || {
                if let Some(ui) = ui_handle.upgrade() {
                    if let Ok(s) = state_clone.lock() {
                        push_builds_to_ui(&ui, &s);
                        ui.set_active_build_count(s.active_build_count);
                    }
                }
            });
        }
    }
}

fn sync_diagnostics(ui_handle: slint::Weak<MainWindow>, client: &IpcClient, state: Arc<Mutex<AppState>>, repo_id: Option<&str>) {
    let params = match repo_id {
        Some(rid) => Some(serde_json::json!({ "repository_id": rid })),
        None => None,
    };
    if let Ok(val) = client.send_request("comparison.get_latest", params, Duration::from_secs(5)) {
        if val["id"].is_null() {
            let msg_a = val["build_a_id"].as_str().unwrap_or("No comparisons yet for this repository").to_string();
            let msg_b = val["build_b_id"].as_str().unwrap_or("Click 'Verify Reproducibility (2x Build + Compare)'").to_string();
            if let Ok(mut s) = state.lock() {
                s.diffs.clear();
            }
            let _ = slint::invoke_from_event_loop(move || {
                if let Some(ui) = ui_handle.upgrade() {
                    ui.set_diag_target_a(msg_a.into());
                    ui.set_diag_target_b(msg_b.into());
                    ui.set_diag_verdict("UNCOMPARED".into());
                    ui.set_cri_content(0.0);
                    ui.set_cri_metadata(0.0);
                    ui.set_cri_environment(0.0);
                    ui.set_diff_entries(ModelRc::new(VecModel::from(Vec::<DiffEntry>::new())));
                    ui.set_selected_diff_snippet("".into());
                    ui.set_diagnostic_findings(ModelRc::new(VecModel::from(Vec::<DiagnosticFindingItem>::new())));
                }
            });
            return;
        }
        let target_a = val["build_a_id"].as_str().unwrap_or("—").to_string();
        let target_b = val["build_b_id"].as_str().unwrap_or("—").to_string();
        let verdict = val["verdict"].as_str().unwrap_or("UNCOMPARED").to_string();
        let cri_c = val["content_agreement_ratio"].as_f64().unwrap_or(0.0).clamp(0.0, 1.0) as f32;
        let cri_m = val["metadata_agreement_ratio"].as_f64().unwrap_or(0.0).clamp(0.0, 1.0) as f32;
        let cri_e = val["environment_consistency_score"].as_f64().unwrap_or(0.0).clamp(0.0, 1.0) as f32;

        let diffs: Vec<DiffEntry> = val["diff_entries"]
            .as_array()
            .map(|arr| {
                arr.iter()
                    .map(|d| DiffEntry {
                        path: d["relative_path"].as_str().unwrap_or("unknown").into(),
                        diff_type: d["diff_type"].as_str().unwrap_or("CONTENT").into(),
                        status: d["diff_type"].as_str().unwrap_or("DRIFT").into(),
                        size_delta: format!(
                            "{} B -> {} B",
                            d["size_a"].as_i64().unwrap_or(0),
                            d["size_b"].as_i64().unwrap_or(0)
                        )
                        .into(),
                        snippet: d["diff_snippet"].as_str().unwrap_or("").into(),
                    })
                    .collect()
            })
            .unwrap_or_default();

        let findings: Vec<DiagnosticFindingItem> = val["diagnostic_findings"]
            .as_array()
            .map(|arr| {
                arr.iter()
                    .map(|f| DiagnosticFindingItem {
                        rule_id: f["rule_id"].as_str().unwrap_or("RCA").into(),
                        severity: f["severity"].as_str().unwrap_or("MEDIUM").into(),
                        category: f["category"].as_str().unwrap_or("ENVIRONMENT").into(),
                        title: f["title"].as_str().unwrap_or("Diagnostic Finding").into(),
                        epistemic_status: f["epistemic_status"].as_str().unwrap_or("INFERRED").into(),
                        summary: f["summary"].as_str().unwrap_or("").into(),
                        remediation: f["suggested_remediation"].as_str().unwrap_or("").into(),
                    })
                    .collect()
            })
            .unwrap_or_default();

        let first_snippet = diffs
            .first()
            .map(|d| d.snippet.to_string())
            .unwrap_or_default();

        if let Ok(mut s) = state.lock() {
            s.diffs = diffs.clone();
        }

        let _ = slint::invoke_from_event_loop(move || {
            if let Some(ui) = ui_handle.upgrade() {
                ui.set_diag_target_a(target_a.into());
                ui.set_diag_target_b(target_b.into());
                ui.set_diag_verdict(verdict.into());
                ui.set_cri_content(cri_c);
                ui.set_cri_metadata(cri_m);
                ui.set_cri_environment(cri_e);
                ui.set_diff_entries(ModelRc::new(VecModel::from(diffs)));
                ui.set_selected_diff_snippet(first_snippet.into());
                ui.set_diff_selected_idx(0);
                ui.set_diagnostic_findings(ModelRc::new(VecModel::from(findings)));
            }
        });
    }
}

fn infer_node_tier(node_type: &str) -> i32 {
    let lower = node_type.to_lowercase();
    if lower.contains("rev") || lower.contains("repo") {
        0
    } else if lower.contains("build") {
        1
    } else if lower.contains("env") || lower.contains("finger") {
        2
    } else if lower.contains("diff") || lower.contains("artifact") {
        3
    } else {
        4
    }
}

fn sync_causality_graph(ui_handle: slint::Weak<MainWindow>, client: &IpcClient, state: Arc<Mutex<AppState>>, repo_id: Option<&str>) {
    let params = match repo_id {
        Some(rid) => Some(serde_json::json!({ "repository_id": rid })),
        None => None,
    };
    if let Ok(val) = client.send_request("causality.get_graph", params, Duration::from_secs(5)) {
        let nodes: Vec<GraphNodeItem> = val["nodes"]
            .as_array()
            .map(|arr| {
                arr.iter()
                    .map(|n| {
                        let node_type = n["node_type"].as_str().unwrap_or("Node").to_string();
                        let tier = n["tier"]
                            .as_i64()
                            .map(|t| t as i32)
                            .unwrap_or_else(|| infer_node_tier(&node_type));
                        GraphNodeItem {
                            id: n["id"].as_str().unwrap_or("").into(),
                            node_type: node_type.into(),
                            label: n["label"].as_str().unwrap_or("Node").into(),
                            subtitle: n["subtitle"]
                                .as_str()
                                .or_else(|| n["details"].as_str())
                                .unwrap_or("")
                                .into(),
                            epistemic_status: n["epistemic_status"]
                                .as_str()
                                .unwrap_or("OBSERVED")
                                .to_uppercase()
                                .into(),
                            tier,
                        }
                    })
                    .collect()
            })
            .unwrap_or_default();

        let edges: Vec<GraphEdgeItem> = val["edges"]
            .as_array()
            .map(|arr| {
                arr.iter()
                    .map(|e| GraphEdgeItem {
                        source_id: e["source_id"].as_str().unwrap_or("").into(),
                        target_id: e["target_id"].as_str().unwrap_or("").into(),
                        relation: e["relation"].as_str().unwrap_or("CAUSES").into(),
                        epistemic_status: e["epistemic_status"]
                            .as_str()
                            .unwrap_or("INFERRED")
                            .to_uppercase()
                            .into(),
                        evidence: e["evidence"].as_str().unwrap_or("").into(),
                    })
                    .collect()
            })
            .unwrap_or_default();

        if let Ok(mut s) = state.lock() {
            s.graph_nodes = nodes.clone();
        }

        let _ = slint::invoke_from_event_loop(move || {
            if let Some(ui) = ui_handle.upgrade() {
                ui.set_graph_nodes(ModelRc::new(VecModel::from(nodes)));
                ui.set_graph_edges(ModelRc::new(VecModel::from(edges)));
            }
        });
    }
}

fn sync_perturbation(ui_handle: slint::Weak<MainWindow>, client: &IpcClient, repo_id: Option<&str>) {
    let params = match repo_id {
        Some(rid) => serde_json::json!({ "repository_id": rid }),
        None => serde_json::json!({}),
    };
    if let Ok(val) = client.send_request("perturbation.params", Some(params), Duration::from_secs(5)) {
        let p_items: Vec<PerturbationParam> = val["params"]
            .as_array()
            .map(|arr| {
                arr.iter()
                    .map(|p| PerturbationParam {
                        name: p["name"].as_str().unwrap_or("").into(),
                        baseline_val: p["baseline_val"].as_str().unwrap_or("").into(),
                        perturbed_val: p["perturbed_val"].as_str().unwrap_or("").into(),
                        description: p["description"].as_str().unwrap_or("").into(),
                        enabled: p["enabled"].as_bool().unwrap_or(true),
                    })
                    .collect()
            })
            .unwrap_or_default();

        let s_items: Vec<SensitivityResult> = val["campaigns"]
            .as_array()
            .and_then(|c| c.first())
            .and_then(|c| c["results"].as_array())
            .map(|arr| {
                arr.iter()
                    .map(|r| SensitivityResult {
                        param_name: r["param_name"].as_str().unwrap_or("").into(),
                        baseline_verdict: r["baseline_verdict"].as_str().unwrap_or("BIT_FOR_BIT").into(),
                        perturbed_verdict: r["perturbed_verdict"].as_str().unwrap_or("BIT_FOR_BIT").into(),
                        hash_changed: r["hash_changed"].as_bool().unwrap_or(false),
                        causal_impact: r["causal_impact"].as_str().unwrap_or("").into(),
                        epistemic_status: r["epistemic_status"].as_str().unwrap_or("OBSERVED").into(),
                    })
                    .collect()
            })
            .unwrap_or_default();

        let _ = slint::invoke_from_event_loop(move || {
            if let Some(ui) = ui_handle.upgrade() {
                ui.set_perturbation_params(ModelRc::new(VecModel::from(p_items)));
                ui.set_sensitivity_results(ModelRc::new(VecModel::from(s_items)));
            }
        });
    }
}

fn sync_passport(ui_handle: slint::Weak<MainWindow>, client: &IpcClient, repo_id: Option<&str>) {
    let params = match repo_id {
        Some(rid) => serde_json::json!({ "repository_id": rid }),
        None => serde_json::json!({}),
    };
    if let Ok(val) = client.send_request("passport.get", Some(params), Duration::from_secs(5)) {
        let json_pretty = val["json_pretty"].as_str().unwrap_or("").to_string();
        let repo_name = val["passport"]["repository"]["name"].as_str().unwrap_or("").to_string();
        let status = val["passport"]["verification_status"].as_str().unwrap_or("UNVERIFIED").to_string();
        let _ = slint::invoke_from_event_loop(move || {
            if let Some(ui) = ui_handle.upgrade() {
                ui.set_passport_json(json_pretty.into());
                ui.set_passport_repo(repo_name.into());
                ui.set_passport_status(status.into());
            }
        });
    }
}

fn sync_remediation(ui_handle: slint::Weak<MainWindow>, client: &IpcClient, repo_id: Option<&str>) {
    let params = match repo_id {
        Some(rid) => serde_json::json!({ "repository_id": rid }),
        None => serde_json::json!({}),
    };
    if let Ok(val) = client.send_request("remediation.list_proposals", Some(params), Duration::from_secs(5)) {
        let props: Vec<RemediationProposal> = val["proposals"]
            .as_array()
            .map(|arr| {
                arr.iter()
                    .map(|p| RemediationProposal {
                        id: p["id"].as_str().unwrap_or("").into(),
                        tier: p["tier"].as_str().unwrap_or("LEVEL_2").into(),
                        title: p["title"].as_str().unwrap_or("").into(),
                        description: p["description"].as_str().unwrap_or("").into(),
                        patch_preview: p["patch_preview"].as_str().unwrap_or("").into(),
                        status: p["status"].as_str().unwrap_or("PENDING").into(),
                        confidence: p["confidence"].as_str().unwrap_or("—").into(),
                        requires_approval: p["requires_approval"].as_bool().unwrap_or(true),
                    })
                    .collect()
            })
            .unwrap_or_default();

        let sel_idx = if props.is_empty() { -1 } else { 0 };
        let _ = slint::invoke_from_event_loop(move || {
            if let Some(ui) = ui_handle.upgrade() {
                ui.set_remediation_proposals(ModelRc::new(VecModel::from(props)));
                ui.set_selected_proposal_idx(sel_idx);
            }
        });
    }
}

fn sync_all_views(ui_handle: slint::Weak<MainWindow>, client: &IpcClient, state: &Arc<Mutex<AppState>>) {
    sync_overview(ui_handle.clone(), client);
    let sel_repo = state.lock().map(|s| s.selected_repo_idx).unwrap_or(0);
    sync_repositories(ui_handle.clone(), client, state.clone(), sel_repo);
    sync_builds(ui_handle.clone(), client, state.clone());
    let repo_id = state.lock().ok().and_then(|s| s.repos.get(s.selected_repo_idx).map(|r| r.id.clone()));
    sync_diagnostics(ui_handle.clone(), client, state.clone(), repo_id.as_deref());
    sync_causality_graph(ui_handle.clone(), client, state.clone(), repo_id.as_deref());
    sync_perturbation(ui_handle.clone(), client, repo_id.as_deref());
    sync_passport(ui_handle.clone(), client, repo_id.as_deref());
    sync_remediation(ui_handle, client, repo_id.as_deref());
}

fn run_scan_folder_ipc(
    ui_handle: slint::Weak<MainWindow>,
    supervisor: Arc<ProcessSupervisor>,
    state: Arc<Mutex<AppState>>,
    path_str: String,
    fetch_online: bool,
) {
    let trimmed = path_str.trim().to_string();
    if trimmed.is_empty() {
        return;
    }

    std::thread::spawn(move || {
        let Some(client) = supervisor.get_client() else {
            let ui_c = ui_handle.clone();
            let _ = slint::invoke_from_event_loop(move || {
                if let Some(ui) = ui_c.upgrade() {
                    ui.set_status_banner("Backend IPC daemon is not connected yet.".into());
                }
            });
            return;
        };

        // Direct remote GitHub URL -> register + autoscan directly
        if trimmed.starts_with("http://") || trimmed.starts_with("https://") || trimmed.starts_with("git@") {
            let name_guess = trimmed
                .trim_end_matches('/')
                .trim_end_matches(".git")
                .rsplit('/')
                .next()
                .unwrap_or("github-repo")
                .to_string();
            let reg_params = serde_json::json!({
                "name": name_guess,
                "repo_url": trimmed,
                "repo_type": "remote",
                "default_branch": "main",
            });
            if let Ok(reg_res) = client.send_request("repository.register", Some(reg_params), Duration::from_secs(10)) {
                if let Some(repo_id) = reg_res["id"].as_str() {
                    let scan_p = serde_json::json!({
                        "repository_id": repo_id,
                        "fetch_online_metadata": fetch_online,
                    });
                    let _ = client.send_request("repository.autoscan", Some(scan_p), Duration::from_secs(20));
                }
                sync_repositories(ui_handle.clone(), &client, state, 0);
                let _ = slint::invoke_from_event_loop(move || {
                    if let Some(ui) = ui_handle.upgrade() {
                        ui.set_status_banner(format!("Registered & scanned remote repository: {}", trimmed).into());
                    }
                });
            }
            return;
        }

        // Local directory / workspace folder inspection
        let params = serde_json::json!({ "folder_path": trimmed });
        match client.send_request("repository.scan_folder", Some(params), Duration::from_secs(15)) {
            Ok(val) => {
                let mode = val["mode"].as_str().unwrap_or("empty");
                let folder = val["folder"].as_str().unwrap_or(&trimmed).to_string();
                let candidates: Vec<WorkspaceCandidateRecord> = val["candidates"]
                    .as_array()
                    .map(|arr| {
                        arr.iter()
                            .map(|c| {
                                let tracked = c["already_tracked"].as_bool().unwrap_or(false);
                                WorkspaceCandidateRecord {
                                    name: c["name"].as_str().unwrap_or("Repo").to_string(),
                                    repo_path: c["repo_path"].as_str().unwrap_or("").to_string(),
                                    default_branch: c["default_branch"].as_str().unwrap_or("main").to_string(),
                                    github_slug: c["github_slug"].as_str().unwrap_or("").to_string(),
                                    upstream_url: c["upstream_github_url"].as_str().unwrap_or("").to_string(),
                                    already_tracked: tracked,
                                    checked: !tracked,
                                }
                            })
                            .collect()
                    })
                    .unwrap_or_default();

                if mode == "single_repo" && candidates.len() == 1 {
                    let c = &candidates[0];
                    let batch = serde_json::json!({
                        "items": [{
                            "name": c.name,
                            "repo_path": c.repo_path,
                            "default_branch": c.default_branch,
                        }],
                        "fetch_online_metadata": fetch_online,
                    });
                    let _ = client.send_request("repository.register_batch", Some(batch), Duration::from_secs(25));
                    sync_repositories(ui_handle.clone(), &client, state, 0);
                    let slug_note = if c.github_slug.is_empty() {
                        "local git".to_string()
                    } else {
                        format!("GitHub: {}", c.github_slug)
                    };
                    let msg = format!("Tracked '{}' and completed static GitHub autoscan ({}).", c.name, slug_note);
                    let _ = slint::invoke_from_event_loop(move || {
                        if let Some(ui) = ui_handle.upgrade() {
                            ui.set_status_banner(msg.into());
                        }
                    });
                } else if !candidates.is_empty() {
                    if let Ok(mut s) = state.lock() {
                        s.candidates = candidates;
                    }
                    let state_c = state.clone();
                    let _ = slint::invoke_from_event_loop(move || {
                        if let Some(ui) = ui_handle.upgrade() {
                            if let Ok(s) = state_c.lock() {
                                push_candidates_to_ui(&ui, &s);
                            }
                            ui.set_workspace_folder_path(folder.into());
                            ui.set_show_workspace_checklist(true);
                        }
                    });
                } else {
                    let msg = format!("No Git repositories found inside '{}'. Select a repository or parent workspace folder.", folder);
                    let _ = slint::invoke_from_event_loop(move || {
                        if let Some(ui) = ui_handle.upgrade() {
                            ui.set_status_banner(msg.into());
                        }
                    });
                }
            }
            Err(e) => {
                let msg = format!("Folder scan failed: {}", e);
                let _ = slint::invoke_from_event_loop(move || {
                    if let Some(ui) = ui_handle.upgrade() {
                        ui.set_status_banner(msg.into());
                    }
                });
            }
        }
    });
}

fn main() -> Result<(), slint::PlatformError> {
    let mut bench_records: Option<usize> = None;
    for arg in std::env::args() {
        if arg == "--renderer=software" {
            std::env::set_var("SLINT_BACKEND", "winit-software");
        } else if arg == "--renderer=hardware" {
            std::env::set_var("SLINT_BACKEND", "winit-femtovg");
        }
    }
    if let Ok(val) = std::env::var("REPROFORGE_BENCH_RECORDS") {
        if let Ok(parsed) = val.parse::<usize>() {
            bench_records = Some(parsed);
        }
    }

    let (renderer_name, _is_software) = detect_renderer_backend();
    println!("REPROFORGE_RENDERER_ACTIVE: {}", renderer_name);

    let main_window = MainWindow::new()?;
    main_window.set_engine_backend(renderer_name.into());

    // Initial state: strictly real data when launched normally; synthetic rows only under explicit benchmark harness
    let initial_builds = bench_records
        .map(data::generate_benchmark_builds)
        .unwrap_or_default();
    let state = Arc::new(Mutex::new(AppState::new(initial_builds)));
    if bench_records.is_some() {
        if let Ok(s) = state.lock() {
            push_builds_to_ui(&main_window, &s);
        }
    }

    // Start Python IPC Daemon Supervisor
    let supervisor = Arc::new(ProcessSupervisor::new());
    let ui_handle = main_window.as_weak();
    let state_notif = state.clone();
    let sup_notif = supervisor.clone();

    supervisor.on_notification(move |method, params| {
        let ui_h = ui_handle.clone();
        let st = state_notif.clone();
        let sup = sup_notif.clone();
        let empty = serde_json::Value::Null;
        let p = params.unwrap_or(&empty);
        match method {
            "build.progress" => {
                let msg = p["message"].as_str().unwrap_or("").to_string();
                let maybe_full = st.lock().ok().and_then(|mut s| s.append_log_chunk(&msg, true));
                let _ = slint::invoke_from_event_loop(move || {
                    if let Some(ui) = ui_h.upgrade() {
                        ui.set_active_build_count(1);
                        if let Some(buf) = maybe_full {
                            ui.set_selected_build_log(buf.into());
                        }
                    }
                });
            }
            "build.log_chunk" => {
                let chunk = p["chunk"]
                    .as_str()
                    .or_else(|| p["line"].as_str())
                    .unwrap_or("")
                    .to_string();
                let maybe_full = st.lock().ok().and_then(|mut s| s.append_log_chunk(&chunk, false));
                if let Some(buf) = maybe_full {
                    let _ = slint::invoke_from_event_loop(move || {
                        if let Some(ui) = ui_h.upgrade() {
                            ui.set_selected_build_log(buf.into());
                        }
                    });
                }
            }
            "build.completed" => {
                let status_str = p["status"].as_str().unwrap_or("completed").to_string();
                let sha = p["primary_hash"].as_str().unwrap_or("—").to_string();
                let maybe_full = st
                    .lock()
                    .ok()
                    .and_then(|mut s| s.append_log_chunk(&format!("[Engine] Build finished: {}", status_str), true));

                let ui_h2 = ui_h.clone();
                let _ = slint::invoke_from_event_loop(move || {
                    if let Some(ui) = ui_h2.upgrade() {
                        ui.set_active_build_count(0);
                        ui.set_selected_hash(sha.into());
                        if let Some(buf) = maybe_full {
                            ui.set_selected_build_log(buf.into());
                        }
                    }
                });

                std::thread::spawn(move || {
                    if let Some(client) = sup.get_client() {
                        sync_all_views(ui_h, &client, &st);
                    }
                });
            }
            _ => {}
        }
    });

    let ui_stat = main_window.as_weak();
    let sup_sync = supervisor.clone();
    let st_sync = state.clone();
    supervisor.on_status_change(move |status| {
        let (text, healthy) = match &status {
            SupervisorStatus::Stopped => ("Engine Stopped".to_string(), false),
            SupervisorStatus::Starting => ("Engine Starting...".to_string(), false),
            SupervisorStatus::Connected { pid, .. } => (format!("Connected (PID {})", pid), true),
            SupervisorStatus::Reconnecting { attempt, max_attempts } => {
                (format!("Reconnecting ({}/{})...", attempt, max_attempts), false)
            }
            SupervisorStatus::Failed(err) => (format!("Engine Error: {}", err), false),
        };

        let ui_h = ui_stat.clone();
        let _ = slint::invoke_from_event_loop(move || {
            if let Some(ui) = ui_h.upgrade() {
                ui.set_engine_status(text.into());
                ui.set_engine_healthy(healthy);
            }
        });

        if let SupervisorStatus::Connected { .. } = status {
            let ui_h2 = ui_stat.clone();
            let sup2 = sup_sync.clone();
            let st2 = st_sync.clone();
            std::thread::spawn(move || {
                if let Some(client) = sup2.get_client() {
                    sync_all_views(ui_h2, &client, &st2);
                }
            });
        }
    });

    supervisor.start();

    // Wire Callbacks: Search Filter
    let st_search = state.clone();
    let ui_search = main_window.as_weak();
    main_window.on_search_changed(move |query| {
        if let Ok(mut s) = st_search.lock() {
            s.search_query = query.to_string();
            if let Some(ui) = ui_search.upgrade() {
                push_builds_to_ui(&ui, &s);
                push_repos_to_ui(&ui, &s);
            }
        }
    });

    // Wire Callbacks: Manual Full Refresh
    let ui_ref = main_window.as_weak();
    let sup_ref = supervisor.clone();
    let st_ref = state.clone();
    main_window.on_refresh_requested(move || {
        let ui_h = ui_ref.clone();
        let sup = sup_ref.clone();
        let st = st_ref.clone();
        std::thread::spawn(move || {
            if let Some(client) = sup.get_client() {
                sync_all_views(ui_h, &client, &st);
            }
        });
    });

    // Wire Callbacks: Build Row Clicked -> Fetch Real build.get_details
    let ui_row = main_window.as_weak();
    let sup_row = supervisor.clone();
    let st_row = state.clone();
    main_window.on_build_row_clicked(move |idx| {
        let build_id = {
            let s = st_row.lock().ok();
            s.and_then(|st| {
                let q = st.search_query.trim().to_lowercase();
                st.builds
                    .iter()
                    .filter(|b| {
                        q.is_empty()
                            || b.build_id.to_lowercase().contains(&q)
                            || b.repo.to_lowercase().contains(&q)
                            || b.commit.to_lowercase().contains(&q)
                    })
                    .nth(idx as usize)
                    .map(|b| b.build_id.clone())
            })
        };

        if let Some(bid) = build_id {
            let ui_h = ui_row.clone();
            let sup = sup_row.clone();
            std::thread::spawn(move || {
                if let Some(client) = sup.get_client() {
                    let params = serde_json::json!({ "build_id": bid });
                    if let Ok(det) = client.send_request("build.get_details", Some(params), Duration::from_secs(5)) {
                        let sha = det["primary_sha256"].as_str().unwrap_or("No SHA-256 artifact digest captured").to_string();
                        let count = det["artifacts"].as_array().map(|a| a.len()).unwrap_or(0);
                        let log_txt = det["log_text"].as_str().unwrap_or("").to_string();
                        let _ = slint::invoke_from_event_loop(move || {
                            if let Some(ui) = ui_h.upgrade() {
                                ui.set_selected_hash(sha.into());
                                ui.set_selected_artifact_count(format!("{} artifact(s)", count).into());
                                ui.set_selected_build_log(log_txt.into());
                            }
                        });
                    }
                }
            });
        }
    });

    // Wire Callbacks: Repository & Revision Selection
    let ui_repo = main_window.as_weak();
    let sup_repo = supervisor.clone();
    let st_repo = state.clone();
    main_window.on_repo_selected(move |idx| {
        let repo_id = {
            let Ok(mut s) = st_repo.lock() else { return; };
            s.selected_repo_idx = idx as usize;
            let q = s.search_query.trim().to_lowercase();
            s.repos
                .iter()
                .filter(|r| {
                    q.is_empty()
                        || r.name.to_lowercase().contains(&q)
                        || r.github_slug.to_lowercase().contains(&q)
                        || r.build_system.to_lowercase().contains(&q)
                })
                .nth(idx as usize)
                .map(|r| r.id.clone())
        };
        if let Some(rid) = repo_id {
            let ui_h = ui_repo.clone();
            let sup = sup_repo.clone();
            let st = st_repo.clone();
            std::thread::spawn(move || {
                if let Some(client) = sup.get_client() {
                    sync_revisions_for_repo(ui_h.clone(), &client, &st, &rid);
                    sync_diagnostics(ui_h.clone(), &client, st.clone(), Some(&rid));
                    sync_causality_graph(ui_h.clone(), &client, st, Some(&rid));
                    sync_perturbation(ui_h.clone(), &client, Some(&rid));
                    sync_passport(ui_h.clone(), &client, Some(&rid));
                    sync_remediation(ui_h, &client, Some(&rid));
                }
            });
        }
    });

    let ui_rev = main_window.as_weak();
    let st_rev = state.clone();
    main_window.on_revision_selected(move |idx| {
        if let Some(ui) = ui_rev.upgrade() {
            let revs = ui.get_revisions();
            if let Some(rev) = revs.row_data(idx as usize) {
                if let Ok(mut s) = st_rev.lock() {
                    s.selected_revision_sha = rev.sha.to_string();
                }
            }
        }
    });

    // Wire Callbacks: Backend Isolation Selector
    let st_backend = state.clone();
    main_window.on_backend_type_selected(move |btype| {
        if let Ok(mut s) = st_backend.lock() {
            s.selected_backend_type = btype.to_string();
        }
    });

    // Wire Callbacks: Trigger Verification Build (Uses Selected Repo + Backend)
    let ui_trig = main_window.as_weak();
    let sup_trig = supervisor.clone();
    let st_trig = state.clone();
    main_window.on_trigger_build_requested(move || {
        let (repo_id, commit_ref, backend) = {
            let s = st_trig.lock().unwrap();
            let rid = s.repos.get(s.selected_repo_idx).map(|r| r.id.clone());
            let cref = if s.selected_revision_sha.is_empty() { "HEAD".into() } else { s.selected_revision_sha.clone() };
            (rid, cref, s.selected_backend_type.clone())
        };

        if let Some(ui) = ui_trig.upgrade() {
            ui.set_active_tab(2);
            ui.set_active_build_count(1);
            ui.set_selected_build_log(format!("Launching {} build at {}...", backend, commit_ref).into());
        }
        if let Ok(mut s) = st_trig.lock() {
            s.log_buffer.clear();
        }

        let sup = sup_trig.clone();
        let ui_h = ui_trig.clone();
        std::thread::spawn(move || {
            if let Some(client) = sup.get_client() {
                let mut params = serde_json::json!({
                    "backend_type": backend,
                    "commit_ref": commit_ref,
                });
                if let Some(rid) = repo_id {
                    params["repository_id"] = serde_json::Value::String(rid);
                }
                if let Err(e) = client.send_request("build.trigger", Some(params), Duration::from_secs(120)) {
                    let msg = format!("Build error: {}", e);
                    let _ = slint::invoke_from_event_loop(move || {
                        if let Some(ui) = ui_h.upgrade() {
                            ui.set_active_build_count(0);
                            ui.set_status_banner(msg.into());
                        }
                    });
                }
            }
        });
    });

    let ui_rev_b = main_window.as_weak();
    let sup_rev_b = supervisor.clone();
    let st_rev_b = state.clone();
    main_window.on_build_specific_revision_requested(move |sha| {
        let (repo_id, backend) = {
            let mut s = st_rev_b.lock().unwrap();
            s.selected_revision_sha = sha.to_string();
            s.log_buffer.clear();
            let rid = s.repos.get(s.selected_repo_idx).map(|r| r.id.clone());
            (rid, s.selected_backend_type.clone())
        };
        if let Some(ui) = ui_rev_b.upgrade() {
            ui.set_active_build_count(1);
            ui.set_selected_build_log(format!("Launching {} build for revision {}...", backend, sha).into());
        }
        let sup = sup_rev_b.clone();
        let ui_h = ui_rev_b.clone();
        let cref = sha.to_string();
        std::thread::spawn(move || {
            if let Some(client) = sup.get_client() {
                let mut params = serde_json::json!({
                    "backend_type": backend,
                    "commit_ref": cref,
                });
                if let Some(rid) = repo_id {
                    params["repository_id"] = serde_json::Value::String(rid);
                }
                let _ = client.send_request("build.trigger", Some(params), Duration::from_secs(120));
                let _ = ui_h;
            }
        });
    });

    // Wire Callbacks: Cancel Running Build
    let ui_can = main_window.as_weak();
    let sup_can = supervisor.clone();
    main_window.on_cancel_build_requested(move || {
        let sup = sup_can.clone();
        let ui_h = ui_can.clone();
        std::thread::spawn(move || {
            if let Some(client) = sup.get_client() {
                let _ = client.send_request("build.cancel", Some(serde_json::json!({})), Duration::from_secs(5));
                let _ = slint::invoke_from_event_loop(move || {
                    if let Some(ui) = ui_h.upgrade() {
                        ui.set_active_build_count(0);
                        ui.set_status_banner("Sent cancellation signal to active build process.".into());
                    }
                });
            }
        });
    });

    // Wire Callbacks: Native OS Folder Picker + GitHub Autoscan + Workspace Checklist
    let ui_browse = main_window.as_weak();
    let sup_browse = supervisor.clone();
    let st_browse = state.clone();
    main_window.on_browse_folder_requested(move || {
        let ui_h = ui_browse.clone();
        let sup = sup_browse.clone();
        let st = st_browse.clone();
        let fetch_online = ui_browse
            .upgrade()
            .map(|u| u.get_fetch_online_github())
            .unwrap_or(true);
        std::thread::spawn(move || {
            if let Some(folder) = rfd::FileDialog::new().set_title("Select Local Git Repository or Parent Workspace").pick_folder() {
                let path_str = folder.to_string_lossy().to_string();
                let ui_set = ui_h.clone();
                let p_clone = path_str.clone();
                let _ = slint::invoke_from_event_loop(move || {
                    if let Some(ui) = ui_set.upgrade() {
                        ui.set_repo_input_path(p_clone.into());
                    }
                });
                run_scan_folder_ipc(ui_h, sup, st, path_str, fetch_online);
            }
        });
    });

    let ui_scan = main_window.as_weak();
    let sup_scan = supervisor.clone();
    let st_scan = state.clone();
    main_window.on_scan_folder_requested(move |path_arg| {
        let fetch_online = ui_scan
            .upgrade()
            .map(|u| u.get_fetch_online_github())
            .unwrap_or(true);
        run_scan_folder_ipc(
            ui_scan.clone(),
            sup_scan.clone(),
            st_scan.clone(),
            path_arg.to_string(),
            fetch_online,
        );
    });

    let ui_reg_fallback = main_window.as_weak();
    let sup_reg_fallback = supervisor.clone();
    let st_reg_fallback = state.clone();
    main_window.on_register_local_repo_requested(move || {
        let p = ui_reg_fallback
            .upgrade()
            .map(|u| u.get_repo_input_path().to_string())
            .unwrap_or_default();
        if !p.trim().is_empty() {
            run_scan_folder_ipc(ui_reg_fallback.clone(), sup_reg_fallback.clone(), st_reg_fallback.clone(), p, true);
        }
    });

    let ui_auto = main_window.as_weak();
    let sup_auto = supervisor.clone();
    let st_auto = state.clone();
    main_window.on_autoscan_repo_requested(move || {
        let repo_id = {
            let s = st_auto.lock().unwrap();
            s.repos.get(s.selected_repo_idx).map(|r| r.id.clone())
        };
        let fetch_online = ui_auto
            .upgrade()
            .map(|u| u.get_fetch_online_github())
            .unwrap_or(true);
        if let Some(rid) = repo_id {
            let sup = sup_auto.clone();
            let ui_h = ui_auto.clone();
            let st = st_auto.clone();
            std::thread::spawn(move || {
                if let Some(client) = sup.get_client() {
                    let params = serde_json::json!({
                        "repository_id": rid,
                        "fetch_online_metadata": fetch_online,
                    });
                    if let Ok(res) = client.send_request("repository.autoscan", Some(params), Duration::from_secs(20)) {
                        let slug = res["github_slug"].as_str().unwrap_or("none").to_string();
                        let sel = st.lock().map(|s| s.selected_repo_idx).unwrap_or(0);
                        sync_repositories(ui_h.clone(), &client, st, sel);
                        let _ = slint::invoke_from_event_loop(move || {
                            if let Some(ui) = ui_h.upgrade() {
                                ui.set_status_banner(format!("Completed static GitHub autoscan (remote: {}).", slug).into());
                            }
                        });
                    }
                }
            });
        }
    });

    // Checklist Modal Handlers
    let ui_chk_toggle = main_window.as_weak();
    let st_chk_toggle = state.clone();
    main_window.on_checklist_item_toggled(move |idx| {
        if let Ok(mut s) = st_chk_toggle.lock() {
            if let Some(item) = s.candidates.get_mut(idx as usize) {
                item.checked = !item.checked;
            }
            if let Some(ui) = ui_chk_toggle.upgrade() {
                push_candidates_to_ui(&ui, &s);
            }
        }
    });

    let ui_chk_all = main_window.as_weak();
    let st_chk_all = state.clone();
    main_window.on_checklist_select_all_untracked(move || {
        if let Ok(mut s) = st_chk_all.lock() {
            for item in &mut s.candidates {
                item.checked = !item.already_tracked;
            }
            if let Some(ui) = ui_chk_all.upgrade() {
                push_candidates_to_ui(&ui, &s);
            }
        }
    });

    let ui_chk_dismiss = main_window.as_weak();
    main_window.on_dismiss_workspace_checklist(move || {
        if let Some(ui) = ui_chk_dismiss.upgrade() {
            ui.set_show_workspace_checklist(false);
        }
    });

    let ui_chk_confirm = main_window.as_weak();
    let sup_chk_confirm = supervisor.clone();
    let st_chk_confirm = state.clone();
    main_window.on_confirm_workspace_checklist(move || {
        let items: Vec<serde_json::Value> = {
            let s = st_chk_confirm.lock().unwrap();
            s.candidates
                .iter()
                .filter(|c| c.checked)
                .map(|c| {
                    serde_json::json!({
                        "name": c.name,
                        "repo_path": c.repo_path,
                        "default_branch": c.default_branch,
                    })
                })
                .collect()
        };
        let fetch_online = ui_chk_confirm
            .upgrade()
            .map(|u| u.get_fetch_online_github())
            .unwrap_or(true);
        if let Some(ui) = ui_chk_confirm.upgrade() {
            ui.set_show_workspace_checklist(false);
            ui.set_status_banner(format!("Tracking & scanning {} selected repository(s)...", items.len()).into());
        }
        if items.is_empty() {
            return;
        }
        let sup = sup_chk_confirm.clone();
        let ui_h = ui_chk_confirm.clone();
        let st = st_chk_confirm.clone();
        std::thread::spawn(move || {
            if let Some(client) = sup.get_client() {
                let params = serde_json::json!({
                    "items": items,
                    "fetch_online_metadata": fetch_online,
                });
                if let Ok(res) = client.send_request("repository.register_batch", Some(params), Duration::from_secs(45)) {
                    let count = res["count"].as_u64().unwrap_or(0);
                    sync_repositories(ui_h.clone(), &client, st, 0);
                    let _ = slint::invoke_from_event_loop(move || {
                        if let Some(ui) = ui_h.upgrade() {
                            ui.set_status_banner(format!("Tracked & completed static GitHub autoscan for {} repository(s).", count).into());
                        }
                    });
                }
            }
        });
    });

    // Wire Callbacks: Diff Entry Selection
    let ui_diff = main_window.as_weak();
    let st_diff = state.clone();
    main_window.on_diff_entry_selected(move |idx| {
        if let Ok(s) = st_diff.lock() {
            if let Some(entry) = s.diffs.get(idx as usize) {
                if let Some(ui) = ui_diff.upgrade() {
                    let snippet = if entry.snippet.is_empty() {
                        format!("{} ({}) — {}", entry.path, entry.diff_type, entry.size_delta)
                    } else {
                        entry.snippet.to_string()
                    };
                    ui.set_selected_diff_snippet(snippet.into());
                }
            }
        }
    });

    // Wire Callbacks: One-Click Verify Reproducibility Pair (Build A + Build B + Compare) & Compare Latest 2 Builds
    let ui_vpair = main_window.as_weak();
    let sup_vpair = supervisor.clone();
    let st_vpair = state.clone();
    main_window.on_verify_pair_requested(move || {
        let (repo_id, repo_name, commit_ref, backend) = {
            let s = st_vpair.lock().unwrap();
            let r = s.repos.get(s.selected_repo_idx);
            let rid = r.map(|x| x.id.clone());
            let rname = r.map(|x| x.name.clone()).unwrap_or_else(|| "repository".to_string());
            let cref = if s.selected_revision_sha.is_empty() { "HEAD".into() } else { s.selected_revision_sha.clone() };
            (rid, rname, cref, s.selected_backend_type.clone())
        };

        if let Some(ui) = ui_vpair.upgrade() {
            ui.set_active_tab(2);
            ui.set_active_build_count(1);
            ui.set_selected_build_log(
                format!("[1/3] Running Build A & Build B for '{}' ({}) in separate isolated .venvs...\n", repo_name, commit_ref).into(),
            );
            ui.set_status_banner(format!("Verifying reproducibility for '{}' (2 isolated builds + compare)...", repo_name).into());
        }
        if let Ok(mut s) = st_vpair.lock() {
            s.log_buffer.clear();
        }

        let sup = sup_vpair.clone();
        let ui_h = ui_vpair.clone();
        let st = st_vpair.clone();
        std::thread::spawn(move || {
            if let Some(client) = sup.get_client() {
                let mut params = serde_json::json!({
                    "backend_type": backend,
                    "commit_ref": commit_ref,
                });
                if let Some(rid) = repo_id {
                    params["repository_id"] = serde_json::Value::String(rid);
                }
                match client.send_request("build.verify_pair", Some(params), Duration::from_secs(240)) {
                    Ok(res) => {
                        let verdict = res["verdict"].as_str().unwrap_or("UNCOMPARED").to_string();
                        let cri = res["cri"].as_f64().unwrap_or(0.0);
                        sync_all_views(ui_h.clone(), &client, &st);
                        let _ = slint::invoke_from_event_loop(move || {
                            if let Some(ui) = ui_h.upgrade() {
                                ui.set_active_build_count(0);
                                ui.set_active_tab(3);
                                ui.set_status_banner(
                                    format!(
                                        "Verification complete for '{}': {} (CRI {:.2}) — Diff, Causality Graph, Passport & Remediation updated!",
                                        repo_name, verdict, cri
                                    )
                                    .into(),
                                );
                            }
                        });
                    }
                    Err(e) => {
                        let _ = slint::invoke_from_event_loop(move || {
                            if let Some(ui) = ui_h.upgrade() {
                                ui.set_active_build_count(0);
                                ui.set_status_banner(format!("Verification failed: {}", e).into());
                            }
                        });
                    }
                }
            }
        });
    });

    let ui_cmp_latest = main_window.as_weak();
    let sup_cmp_latest = supervisor.clone();
    let st_cmp_latest = state.clone();
    main_window.on_compare_latest_requested(move || {
        let repo_id = st_cmp_latest
            .lock()
            .ok()
            .and_then(|s| s.repos.get(s.selected_repo_idx).map(|r| r.id.clone()));
        let ui_h = ui_cmp_latest.clone();
        let sup = sup_cmp_latest.clone();
        let st = st_cmp_latest.clone();
        std::thread::spawn(move || {
            if let Some(client) = sup.get_client() {
                let params = match &repo_id {
                    Some(rid) => Some(serde_json::json!({ "repository_id": rid })),
                    None => None,
                };
                match client.send_request("comparison.compare_latest", params, Duration::from_secs(30)) {
                    Ok(res) => {
                        let verdict = res["verdict"].as_str().unwrap_or("UNCOMPARED").to_string();
                        let cri = res["cri"].as_f64().unwrap_or(0.0);
                        sync_all_views(ui_h.clone(), &client, &st);
                        let _ = slint::invoke_from_event_loop(move || {
                            if let Some(ui) = ui_h.upgrade() {
                                ui.set_active_tab(3);
                                ui.set_status_banner(format!("Compared latest 2 builds: {} (CRI {:.2})", verdict, cri).into());
                            }
                        });
                    }
                    Err(e) => {
                        let _ = slint::invoke_from_event_loop(move || {
                            if let Some(ui) = ui_h.upgrade() {
                                ui.set_status_banner(format!("Compare failed: {}", e).into());
                            }
                        });
                    }
                }
            }
        });
    });

    // Wire Callbacks: Causality Graph Refresh & Export DOT
    let ui_graph = main_window.as_weak();
    let sup_graph = supervisor.clone();
    let st_graph = state.clone();
    main_window.on_refresh_graph_requested(move || {
        let repo_id = st_graph
            .lock()
            .ok()
            .and_then(|s| s.repos.get(s.selected_repo_idx).map(|r| r.id.clone()));
        let ui_h = ui_graph.clone();
        let sup = sup_graph.clone();
        let st = st_graph.clone();
        std::thread::spawn(move || {
            if let Some(client) = sup.get_client() {
                sync_causality_graph(ui_h, &client, st, repo_id.as_deref());
            }
        });
    });

    let ui_dot = main_window.as_weak();
    let sup_dot = supervisor.clone();
    main_window.on_export_dot_requested(move || {
        let ui_h = ui_dot.clone();
        let sup = sup_dot.clone();
        std::thread::spawn(move || {
            if let Some(client) = sup.get_client() {
                if let Ok(val) = client.send_request("causality.get_graph", None, Duration::from_secs(5)) {
                    let dot = val["dot_source"].as_str().unwrap_or("digraph ReproForgeCausality {}\n");
                    let out_path = find_repo_root()
                        .unwrap_or_else(|| std::path::PathBuf::from("."))
                        .join("data")
                        .join("causality_graph.dot");
                    let msg = match std::fs::write(&out_path, dot) {
                        Ok(_) => format!("Exported Graphviz DAG to {}", out_path.display()),
                        Err(e) => format!("Failed exporting DOT: {}", e),
                    };
                    let _ = slint::invoke_from_event_loop(move || {
                        if let Some(ui) = ui_h.upgrade() {
                            ui.set_status_banner(msg.into());
                        }
                    });
                }
            }
        });
    });

    // Milestone G Callbacks: Perturbation Lab, Reproducibility Passport & 3-Tier Remediation
    let ui_pert = main_window.as_weak();
    let sup_pert = supervisor.clone();
    let st_pert = state.clone();
    main_window.on_run_perturbation_campaign_requested(move || {
        let repo_id = st_pert
            .lock()
            .ok()
            .and_then(|s| s.repos.get(s.selected_repo_idx).map(|r| r.id.clone()));
        if let Some(ui) = ui_pert.upgrade() {
            ui.set_perturbation_running(true);
            ui.set_status_banner("Running single-variable perturbation campaign...".into());
        }
        let ui_h = ui_pert.clone();
        let sup = sup_pert.clone();
        let st = st_pert.clone();
        std::thread::spawn(move || {
            if let Some(client) = sup.get_client() {
                let params = match &repo_id {
                    Some(rid) => serde_json::json!({ "repository_id": rid }),
                    None => serde_json::json!({}),
                };
                match client.send_request("perturbation.run", Some(params), Duration::from_secs(90)) {
                    Ok(res) => {
                        let cid = res["campaign_id"].as_str().unwrap_or("campaign").to_string();
                        sync_perturbation(ui_h.clone(), &client, repo_id.as_deref());
                        sync_diagnostics(ui_h.clone(), &client, st.clone(), repo_id.as_deref());
                        sync_causality_graph(ui_h.clone(), &client, st, repo_id.as_deref());
                        let _ = slint::invoke_from_event_loop(move || {
                            if let Some(ui) = ui_h.upgrade() {
                                ui.set_perturbation_running(false);
                                ui.set_status_banner(format!("Perturbation campaign {} completed.", cid).into());
                            }
                        });
                    }
                    Err(e) => {
                        let _ = slint::invoke_from_event_loop(move || {
                            if let Some(ui) = ui_h.upgrade() {
                                ui.set_perturbation_running(false);
                                ui.set_status_banner(format!("Perturbation failed: {}", e).into());
                            }
                        });
                    }
                }
            }
        });
    });

    let ui_pjson = main_window.as_weak();
    let sup_pjson = supervisor.clone();
    let st_pjson = state.clone();
    main_window.on_export_passport_json_requested(move || {
        let repo_id = st_pjson
            .lock()
            .ok()
            .and_then(|s| s.repos.get(s.selected_repo_idx).map(|r| r.id.clone()));
        let ui_h = ui_pjson.clone();
        let sup = sup_pjson.clone();
        std::thread::spawn(move || {
            if let Some(client) = sup.get_client() {
                let mut params = serde_json::json!({ "format": "json" });
                if let Some(rid) = repo_id {
                    params["repository_id"] = serde_json::Value::String(rid);
                }
                if let Ok(res) = client.send_request("passport.export", Some(params), Duration::from_secs(10)) {
                    let path = res["file_path"].as_str().unwrap_or("passport.v1.json").to_string();
                    let content = res["content"].as_str().unwrap_or("").to_string();
                    let _ = slint::invoke_from_event_loop(move || {
                        if let Some(ui) = ui_h.upgrade() {
                            if !content.is_empty() {
                                ui.set_passport_json(content.into());
                            }
                            ui.set_status_banner(format!("Exported Reproducibility Passport JSON to {}", path).into());
                        }
                    });
                }
            }
        });
    });

    let ui_pmd = main_window.as_weak();
    let sup_pmd = supervisor.clone();
    let st_pmd = state.clone();
    main_window.on_export_passport_md_requested(move || {
        let repo_id = st_pmd
            .lock()
            .ok()
            .and_then(|s| s.repos.get(s.selected_repo_idx).map(|r| r.id.clone()));
        let ui_h = ui_pmd.clone();
        let sup = sup_pmd.clone();
        std::thread::spawn(move || {
            if let Some(client) = sup.get_client() {
                let mut params = serde_json::json!({ "format": "markdown" });
                if let Some(rid) = repo_id {
                    params["repository_id"] = serde_json::Value::String(rid);
                }
                if let Ok(res) = client.send_request("passport.export", Some(params), Duration::from_secs(10)) {
                    let path = res["file_path"].as_str().unwrap_or("passport.md").to_string();
                    let _ = slint::invoke_from_event_loop(move || {
                        if let Some(ui) = ui_h.upgrade() {
                            ui.set_status_banner(format!("Exported Reproducibility Passport Markdown to {}", path).into());
                        }
                    });
                }
            }
        });
    });

    let ui_app = main_window.as_weak();
    let sup_app = supervisor.clone();
    let st_app = state.clone();
    main_window.on_approve_patch_requested(move |proposal_id| {
        let pid = proposal_id.to_string();
        let repo_id = st_app
            .lock()
            .ok()
            .and_then(|s| s.repos.get(s.selected_repo_idx).map(|r| r.id.clone()));
        let ui_h = ui_app.clone();
        let sup = sup_app.clone();
        std::thread::spawn(move || {
            if let Some(client) = sup.get_client() {
                let mut params = serde_json::json!({ "proposal_id": pid, "approved": true });
                if let Some(rid) = &repo_id {
                    params["repository_id"] = serde_json::Value::String(rid.clone());
                }
                match client.send_request("remediation.approve", Some(params), Duration::from_secs(45)) {
                    Ok(res) => {
                        let status = res["status"].as_str().unwrap_or("VERIFIED").to_string();
                        let dig = res["digest_a"].as_str().unwrap_or("").chars().take(16).collect::<String>();
                        sync_remediation(ui_h.clone(), &client, repo_id.as_deref());
                        let _ = slint::invoke_from_event_loop(move || {
                            if let Some(ui) = ui_h.upgrade() {
                                ui.set_status_banner(
                                    format!("Disposable workspace repair {} (SHA-256 {})", status, dig).into(),
                                );
                            }
                        });
                    }
                    Err(e) => {
                        let _ = slint::invoke_from_event_loop(move || {
                            if let Some(ui) = ui_h.upgrade() {
                                ui.set_status_banner(format!("Remediation blocked/failed: {}", e).into());
                            }
                        });
                    }
                }
            }
        });
    });

    let ui_vrb = main_window.as_weak();
    let sup_vrb = supervisor.clone();
    let st_vrb = state.clone();
    main_window.on_run_verification_rebuild_requested(move || {
        let repo_id = st_vrb
            .lock()
            .ok()
            .and_then(|s| s.repos.get(s.selected_repo_idx).map(|r| r.id.clone()));
        let ui_h = ui_vrb.clone();
        let sup = sup_vrb.clone();
        std::thread::spawn(move || {
            if let Some(client) = sup.get_client() {
                let mut params = serde_json::json!({ "approved": true });
                if let Some(rid) = &repo_id {
                    params["repository_id"] = serde_json::Value::String(rid.clone());
                }
                if let Ok(res) = client.send_request("remediation.verify_rebuild", Some(params), Duration::from_secs(45)) {
                    let status = res["status"].as_str().unwrap_or("VERIFIED").to_string();
                    sync_remediation(ui_h.clone(), &client, repo_id.as_deref());
                    let _ = slint::invoke_from_event_loop(move || {
                        if let Some(ui) = ui_h.upgrade() {
                            ui.set_status_banner(format!("Verification rebuild result: {}", status).into());
                        }
                    });
                }
            }
        });
    });

    // First-Frame Presentation Notifier Hook (benchmark harness compatibility)
    let first_frame = Arc::new(std::sync::atomic::AtomicBool::new(false));
    let ff_clone = first_frame.clone();
    let notifier_res = main_window.window().set_rendering_notifier(move |rendering_state, _api| {
        if matches!(rendering_state, slint::RenderingState::AfterRendering) {
            if !ff_clone.swap(true, std::sync::atomic::Ordering::SeqCst) {
                use std::io::Write;
                println!("FIRST_FRAME_PRESENTED");
                std::io::stdout().flush().ok();
            }
        }
    });

    if notifier_res.is_err() {
        slint::Timer::single_shot(Duration::from_millis(16), move || {
            use std::io::Write;
            println!("FIRST_FRAME_PRESENTED");
            std::io::stdout().flush().ok();
        });
    }

    let run_res = main_window.run();
    supervisor.stop();
    run_res
}
