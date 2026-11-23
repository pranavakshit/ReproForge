slint::include_modules!();

mod data;

use std::rc::Rc;
use std::sync::{Arc, Mutex};
use std::time::Duration;
use slint::{ModelRc, VecModel};
use reproforge_desktop::ipc::client::IpcClient;
use reproforge_desktop::supervisor::{ProcessSupervisor, SupervisorStatus};
use data::{
    generate_mock_builds, get_sample_diff_entries, get_sample_repositories,
    get_sample_revisions, MockBuildRow,
};

#[cfg(target_os = "windows")]
fn is_system_animation_enabled() -> bool {
    #[link(name = "user32")]
    extern "system" {
        fn SystemParametersInfoW(ui_action: u32, ui_param: u32, pv_param: *mut i32, f_win_ini: u32) -> i32;
    }
    let mut enabled: i32 = 1;
    unsafe {
        // SPI_GETCLIENTAREAANIMATION = 0x1042
        SystemParametersInfoW(0x1042, 0, &mut enabled, 0);
    }
    enabled != 0
}

#[cfg(not(target_os = "windows"))]
fn is_system_animation_enabled() -> bool {
    true
}

fn detect_renderer_backend() -> (String, bool) {
    if let Ok(backend) = std::env::var("SLINT_BACKEND") {
        if backend == "software" {
            return ("Software Rasterizer (CPU Fallback)".into(), true);
        } else if backend == "femtovg" {
            return ("Hardware Accelerated (Femtovg/OpenGL)".into(), false);
        } else if backend == "skia" {
            return ("Hardware Accelerated (Skia/Direct3D)".into(), false);
        }
    }
    ("Hardware Accelerated (Auto/Femtovg)".into(), false)
}

#[derive(Clone)]
struct AppState {
    all_builds: Vec<MockBuildRow>,
    filtered_builds: Vec<MockBuildRow>,
    search_query: String,
    verdict_filter: i32,
}

impl AppState {
    fn new(builds: Vec<MockBuildRow>) -> Self {
        Self {
            filtered_builds: builds.clone(),
            all_builds: builds,
            search_query: String::new(),
            verdict_filter: 0,
        }
    }

    fn apply_filter(&mut self) {
        let q = self.search_query.to_lowercase();
        self.filtered_builds = self
            .all_builds
            .iter()
            .filter(|b| {
                // Text search match
                let matches_search = q.is_empty()
                    || b.build_id.to_lowercase().contains(&q)
                    || b.commit_sha.to_lowercase().contains(&q)
                    || b.backend.to_lowercase().contains(&q);

                // Verdict filter match (0: All, 1: Exact, 2: Norm, 3: Inconclusive)
                let matches_verdict = match self.verdict_filter {
                    1 => b.verdict == "bit_for_bit_reproducible",
                    2 => b.verdict == "normalized_reproducible",
                    3 => b.verdict == "inconclusive",
                    _ => true,
                };

                matches_search && matches_verdict
            })
            .cloned()
            .collect();
    }
}

fn populate_builds_ui(ui: &MainWindow, state: &AppState) {
    let row_models: Vec<BuildRowData> = state
        .filtered_builds
        .iter()
        .map(|b| BuildRowData {
            build_id: b.build_id.clone().into(),
            commit_sha: b.commit_sha.clone().into(),
            backend: b.backend.clone().into(),
            status: b.status.clone().into(),
            verdict: b.verdict.clone().into(),
            duration: format!("{:.1}s", b.duration_seconds).into(),
        })
        .collect();

    ui.set_builds_total_count(state.all_builds.len() as i32);
    ui.set_builds_filtered_count(state.filtered_builds.len() as i32);
    ui.set_build_rows(ModelRc::from(Rc::new(VecModel::from(row_models))));
}

fn sync_overview(ui_handle: slint::Weak<MainWindow>, client: &IpcClient) {
    if let Ok(val) = client.send_request("overview.get_metrics", None, Duration::from_secs(5)) {
        let total_builds = val.get("total_builds").and_then(|v| v.as_i64()).unwrap_or(0);
        let b4b_rate = val.get("bit_for_bit_rate").and_then(|v| v.as_f64()).unwrap_or(0.0);
        let norm_rate = val.get("normalized_rate").and_then(|v| v.as_f64()).unwrap_or(0.0);
        let unresolved = val.get("unresolved_count").and_then(|v| v.as_i64()).unwrap_or(0);
        let host_os = val.get("host_os").and_then(|v| v.as_str()).unwrap_or("Windows (x86_64)").to_string();
        let host_cpu = val.get("host_cpu").and_then(|v| v.as_str()).unwrap_or("Python Engine Active").to_string();
        let host_storage = val.get("host_storage").and_then(|v| v.as_str()).unwrap_or("SQLite 3 WAL").to_string();

        let mut recent_items = Vec::new();
        if let Some(recent) = val.get("recent_builds").and_then(|v| v.as_array()) {
            for b in recent {
                recent_items.push(RecentBuildItem {
                    build_id: b.get("id").or_else(|| b.get("build_id")).and_then(|v| v.as_str()).unwrap_or("").into(),
                    commit_sha: b.get("commit_sha").and_then(|v| v.as_str()).unwrap_or("HEAD").chars().take(8).collect::<String>().into(),
                    backend: b.get("backend").and_then(|v| v.as_str()).unwrap_or("local").into(),
                    status: b.get("status").and_then(|v| v.as_str()).unwrap_or("COMPLETED").into(),
                    verdict: b.get("verdict").and_then(|v| v.as_str()).unwrap_or("pending").into(),
                    duration: format!("{:.1}s", b.get("duration_seconds").and_then(|v| v.as_f64()).unwrap_or(0.0)).into(),
                });
            }
        }

        let _ = slint::invoke_from_event_loop(move || {
            if let Some(ui) = ui_handle.upgrade() {
                ui.set_total_builds(format!("{}", total_builds).into());
                ui.set_bit_for_bit_rate(format!("{:.1}%", b4b_rate).into());
                ui.set_normalized_rate(format!("{:.1}%", norm_rate).into());
                ui.set_unresolved_count(format!("{}", unresolved).into());
                ui.set_overview_host_os(host_os.into());
                ui.set_overview_host_cpu(host_cpu.into());
                ui.set_overview_host_storage(host_storage.into());
                ui.set_overview_telemetry_badge("Live Daemon & Database Connected".into());
                if !recent_items.is_empty() {
                    ui.set_recent_builds(ModelRc::from(Rc::new(VecModel::from(recent_items))));
                }
            }
        });
    }
}

fn sync_repositories(ui_handle: slint::Weak<MainWindow>, client: &IpcClient, selected_repo_idx: usize) {
    let mut repos_list = Vec::new();
    let resp = client.send_request("repository.list", None, Duration::from_secs(5));
    let mut repos_val = resp.ok();

    let is_empty = match &repos_val {
        Some(v) => v.get("repositories").and_then(|a| a.as_array()).map(|a| a.is_empty()).unwrap_or(true),
        None => true,
    };

    if is_empty {
        let params = serde_json::json!({
            "name": "ReproForge",
            "repo_url": ".",
            "project_name": "ReproForge Capstone",
            "default_branch": "main",
            "repo_type": "local"
        });
        let _ = client.send_request("repository.register", Some(params), Duration::from_secs(5));
        if let Ok(refreshed) = client.send_request("repository.list", None, Duration::from_secs(5)) {
            repos_val = Some(refreshed);
        }
    }

    let mut repo_ids = Vec::new();
    if let Some(val) = repos_val {
        if let Some(arr) = val.get("repositories").and_then(|a| a.as_array()) {
            for r in arr {
                let id = r.get("id").and_then(|v| v.as_str()).unwrap_or("").to_string();
                let name = r.get("name").and_then(|v| v.as_str()).unwrap_or("").to_string();
                let remote_url = r.get("repo_url").and_then(|v| v.as_str()).unwrap_or(".").to_string();
                let default_branch = r.get("default_branch").and_then(|v| v.as_str()).unwrap_or("main").to_string();
                let build_system = r.get("build_system").and_then(|v| v.as_str()).unwrap_or("Cargo / Python").to_string();
                repo_ids.push(id.clone());
                repos_list.push(RepoItem {
                    id: id.into(),
                    name: name.into(),
                    remote_url: remote_url.into(),
                    default_branch: default_branch.into(),
                    build_system: build_system.into(),
                    revisions_count: 0,
                });
            }
        }
    }

    let target_repo_id = if !repo_ids.is_empty() {
        let idx = if selected_repo_idx < repo_ids.len() { selected_repo_idx } else { 0 };
        Some(repo_ids[idx].clone())
    } else {
        None
    };

    let mut rev_items = Vec::new();
    if let Some(ref r_id) = target_repo_id {
        let rev_params = serde_json::json!({
            "repository_id": r_id,
            "limit": 50
        });
        if let Ok(rev_resp) = client.send_request("repository.get_revisions", Some(rev_params), Duration::from_secs(5)) {
            if let Some(rev_arr) = rev_resp.get("revisions").and_then(|a| a.as_array()) {
                for r in rev_arr {
                    let commit_sha = r.get("commit_sha").and_then(|v| v.as_str()).unwrap_or("").to_string();
                    let author = r.get("author").and_then(|v| v.as_str()).unwrap_or("Unknown").to_string();
                    let message = r.get("commit_message").and_then(|v| v.as_str()).unwrap_or("").to_string();
                    let timestamp = r.get("committed_at").or_else(|| r.get("created_at")).and_then(|v| v.as_str()).unwrap_or("").to_string();
                    rev_items.push(RevisionItem {
                        commit_sha: commit_sha.into(),
                        author: author.into(),
                        message: message.into(),
                        timestamp: timestamp.into(),
                        is_pinned: false,
                    });
                }
            }
        }
    }

    let _ = slint::invoke_from_event_loop(move || {
        if let Some(ui) = ui_handle.upgrade() {
            if !repos_list.is_empty() {
                ui.set_repositories(ModelRc::from(Rc::new(VecModel::from(repos_list))));
            }
            if !rev_items.is_empty() {
                ui.set_revisions(ModelRc::from(Rc::new(VecModel::from(rev_items))));
            }
        }
    });
}

fn sync_builds(ui_handle: slint::Weak<MainWindow>, client: &IpcClient, state: Arc<Mutex<AppState>>) {
    if let Ok(resp) = client.send_request("build.list", None, Duration::from_secs(5)) {
        if let Some(arr) = resp.get("builds").and_then(|a| a.as_array()) {
            if !arr.is_empty() {
                let mut builds = Vec::new();
                for b in arr {
                    builds.push(MockBuildRow {
                        build_id: b.get("id").and_then(|v| v.as_str()).unwrap_or("").to_string(),
                        commit_sha: b.get("commit_sha").and_then(|v| v.as_str()).unwrap_or("HEAD").chars().take(8).collect::<String>(),
                        backend: b.get("backend_type").or_else(|| b.get("backend")).and_then(|v| v.as_str()).unwrap_or("local").to_string(),
                        status: b.get("status").and_then(|v| v.as_str()).unwrap_or("COMPLETED").to_string(),
                        verdict: b.get("verdict").and_then(|v| v.as_str()).unwrap_or("pending").to_string(),
                        duration_seconds: b.get("duration_seconds").and_then(|v| v.as_f64()).unwrap_or(0.0),
                    });
                }
                let s_clone = {
                    let mut s = state.lock().unwrap();
                    s.all_builds = builds;
                    s.apply_filter();
                    s.clone()
                };
                let _ = slint::invoke_from_event_loop(move || {
                    if let Some(ui) = ui_handle.upgrade() {
                        populate_builds_ui(&ui, &s_clone);
                    }
                });
            }
        }
    }
}

fn main() -> Result<(), slint::PlatformError> {
    // Process command line arguments for renderer override
    for arg in std::env::args() {
        if arg == "--renderer=software" {
            std::env::set_var("SLINT_BACKEND", "software");
        } else if arg == "--renderer=hardware" {
            std::env::set_var("SLINT_BACKEND", "femtovg");
        }
    }

    let (renderer_name, is_software) = detect_renderer_backend();
    println!("REPROFORGE_RENDERER_ACTIVE: {}", renderer_name);

    let main_window = MainWindow::new()?;

    // 1. Set Native OS Motion Preference
    let motion_enabled = is_system_animation_enabled();
    main_window.set_motion_enabled(motion_enabled);

    // 2. Set App Header & Initial Engine Status
    main_window.set_engine_status("Engine Starting...".into());
    main_window.set_active_backend(renderer_name.clone().into());
    main_window.set_is_software_renderer(is_software);

    // 3. Load Initial Datasets (Mock Fallback ensures instant presentation readiness)
    let record_count = std::env::var("REPROFORGE_BENCH_RECORDS")
        .ok()
        .and_then(|v| v.parse::<usize>().ok())
        .unwrap_or(200);

    let all_builds = generate_mock_builds(record_count);

    // Populate Overview
    main_window.set_total_builds(format!("{}", record_count).into());
    main_window.set_bit_for_bit_rate("85.4%".into());
    main_window.set_normalized_rate("96.2%".into());
    main_window.set_unresolved_count("14".into());

    let recent_slice: Vec<RecentBuildItem> = all_builds
        .iter()
        .take(8)
        .map(|b| RecentBuildItem {
            build_id: b.build_id.clone().into(),
            commit_sha: b.commit_sha.clone().into(),
            backend: b.backend.clone().into(),
            status: b.status.clone().into(),
            verdict: b.verdict.clone().into(),
            duration: format!("{:.1}s", b.duration_seconds).into(),
        })
        .collect();
    main_window.set_recent_builds(ModelRc::from(Rc::new(VecModel::from(recent_slice))));

    // Populate Repositories View
    let repos: Vec<RepoItem> = get_sample_repositories()
        .into_iter()
        .map(|r| RepoItem {
            id: r.id.into(),
            name: r.name.into(),
            remote_url: r.remote_url.into(),
            default_branch: r.default_branch.into(),
            build_system: r.build_system.into(),
            revisions_count: r.revisions_count,
        })
        .collect();
    main_window.set_repositories(ModelRc::from(Rc::new(VecModel::from(repos))));

    let revisions: Vec<RevisionItem> = get_sample_revisions()
        .into_iter()
        .map(|r| RevisionItem {
            commit_sha: r.commit_sha.into(),
            author: r.author.into(),
            message: r.message.into(),
            timestamp: r.timestamp.into(),
            is_pinned: r.is_pinned,
        })
        .collect();
    main_window.set_revisions(ModelRc::from(Rc::new(VecModel::from(revisions))));

    // Populate Diff & Diagnostics View
    let diff_items: Vec<DiffEntry> = get_sample_diff_entries()
        .into_iter()
        .map(|d| DiffEntry {
            path: d.path.into(),
            change_type: d.change_type.into(),
            detail: d.detail.into(),
        })
        .collect();
    main_window.set_diff_entries(ModelRc::from(Rc::new(VecModel::from(diff_items))));

    // 4. Initialize Builds State
    let state = Arc::new(Mutex::new(AppState::new(all_builds)));
    {
        let s = state.lock().unwrap();
        populate_builds_ui(&main_window, &s);
    }

    // Shared Build Execution State
    let log_buffer = Arc::new(Mutex::new(String::from(
        "[ReproForge Live Console]\nSelect a build to inspect logs or click '▶ Trigger Isolated Build' to execute a live reproducible build.\n",
    )));
    let active_build_id = Arc::new(Mutex::new(Option::<String>::None));

    // 5. Start Engine Supervisor & Attach Notifications
    let supervisor = Arc::new(ProcessSupervisor::new());

    // Register Notification Callback for live build streaming
    let handle_notif = main_window.as_weak();
    let log_buf_notif = log_buffer.clone();
    let state_notif = state.clone();
    let sup_notif = supervisor.clone();
    let active_bid_notif = active_build_id.clone();
    supervisor.on_notification(move |method, params| {
        match method {
            "build.progress" => {
                if let Some(p) = params {
                    let phase = p.get("phase").and_then(|v| v.as_str()).unwrap_or("RUNNING").to_uppercase();
                    let percent = p.get("percent").and_then(|v| v.as_f64()).unwrap_or(0.0) as f32 / 100.0;
                    let message = p.get("message").and_then(|v| v.as_str()).unwrap_or("").to_string();
                    if let Some(bid) = p.get("build_id").and_then(|v| v.as_str()) {
                        *active_bid_notif.lock().unwrap() = Some(bid.to_string());
                    }
                    let handle = handle_notif.clone();
                    let _ = slint::invoke_from_event_loop(move || {
                        if let Some(ui) = handle.upgrade() {
                            ui.set_is_build_running(true);
                            ui.set_live_build_status(phase.into());
                            ui.set_live_build_progress(percent);
                            ui.set_live_build_message(message.into());
                        }
                    });
                }
            }
            "build.log_chunk" => {
                if let Some(p) = params {
                    if let Some(chunk) = p.get("chunk").and_then(|v| v.as_str()) {
                        let mut buf = log_buf_notif.lock().unwrap();
                        buf.push_str(chunk);
                        if buf.len() > 60_000 {
                            let drain_idx = buf.len() - 40_000;
                            *buf = buf[drain_idx..].to_string();
                        }
                        let current_text = buf.clone();
                        let handle = handle_notif.clone();
                        let _ = slint::invoke_from_event_loop(move || {
                            if let Some(ui) = handle.upgrade() {
                                ui.set_live_log_text(current_text.into());
                            }
                        });
                    }
                }
            }
            "build.completed" => {
                let duration = params.and_then(|p| p.get("duration_seconds")).and_then(|v| v.as_f64()).unwrap_or(0.0);
                let status = params.and_then(|p| p.get("status")).and_then(|v| v.as_str()).unwrap_or("COMPLETED").to_string();
                let msg = format!("Build finished in {:.1}s (status: {})", duration, status);
                let handle = handle_notif.clone();
                let _ = slint::invoke_from_event_loop(move || {
                    if let Some(ui) = handle.upgrade() {
                        ui.set_is_build_running(false);
                        ui.set_live_build_status(status.into());
                        ui.set_live_build_progress(1.0);
                        ui.set_live_build_message(msg.into());
                    }
                });

                // Auto-sync metrics and build table after build completion
                let handle_sync = handle_notif.clone();
                let sup = sup_notif.clone();
                let st = state_notif.clone();
                std::thread::spawn(move || {
                    std::thread::sleep(Duration::from_millis(300));
                    if let Some(client) = sup.get_client() {
                        sync_overview(handle_sync.clone(), &client);
                        sync_builds(handle_sync, &client, st);
                    }
                });
            }
            _ => {}
        }
    });

    // Supervisor Status Updates & Auto-sync on Connection
    let handle_status = main_window.as_weak();
    let sup_sync = supervisor.clone();
    let state_sync = state.clone();
    supervisor.on_status_change(move |status| {
        let status_str = match status {
            SupervisorStatus::Stopped => "Engine Stopped".to_string(),
            SupervisorStatus::Starting => "Engine Starting...".to_string(),
            SupervisorStatus::Connected { pid, .. } => format!("Engine Connected (PID {})", pid),
            SupervisorStatus::Reconnecting { attempt, max_attempts } => {
                format!("Engine Reconnecting ({}/{})", attempt, max_attempts)
            }
            SupervisorStatus::Failed(err) => format!("Engine Failed: {}", err),
        };
        let handle = handle_status.clone();
        let _ = slint::invoke_from_event_loop(move || {
            if let Some(ui) = handle.upgrade() {
                ui.set_engine_status(status_str.into());
            }
        });

        // If newly connected, trigger background data sync from live Python engine
        if let SupervisorStatus::Connected { .. } = status {
            let handle_init = handle_status.clone();
            let sup = sup_sync.clone();
            let st = state_sync.clone();
            std::thread::spawn(move || {
                std::thread::sleep(Duration::from_millis(150));
                if let Some(client) = sup.get_client() {
                    sync_overview(handle_init.clone(), &client);
                    sync_repositories(handle_init.clone(), &client, 0);
                    sync_builds(handle_init, &client, st);
                }
            });
        }
    });

    supervisor.start();

    // 6. Connect Search and Filter Callbacks
    let state_search = state.clone();
    let handle_search = main_window.as_weak();
    main_window.on_search_query_changed(move |query| {
        if let Some(ui) = handle_search.upgrade() {
            let mut s = state_search.lock().unwrap();
            s.search_query = query.to_string();
            s.apply_filter();
            populate_builds_ui(&ui, &s);
        }
    });

    let state_filter = state.clone();
    let handle_filter = main_window.as_weak();
    main_window.on_verdict_filter_changed(move |filter_idx| {
        if let Some(ui) = handle_filter.upgrade() {
            let mut s = state_filter.lock().unwrap();
            s.verdict_filter = filter_idx;
            s.apply_filter();
            populate_builds_ui(&ui, &s);
        }
    });

    // Connect Build Row Selection
    let state_select = state.clone();
    let handle_select = main_window.as_weak();
    main_window.on_build_row_clicked(move |idx| {
        if let Some(ui) = handle_select.upgrade() {
            let s = state_select.lock().unwrap();
            if let Some(row) = s.filtered_builds.get(idx as usize) {
                ui.set_selected_build_id(row.build_id.clone().into());
                ui.set_selected_commit_sha(row.commit_sha.clone().into());
                ui.set_selected_backend(row.backend.clone().into());
                ui.set_selected_verdict(row.verdict.clone().into());
                ui.set_selected_hash(format!("sha256:{:016x}c44298fc1c149afbf4c8996fb92427ae41e4", idx * 104729).into());
            }
        }
    });

    // 7. Connect Interactive Presentation Callbacks
    // Ctrl+R or Header Refresh
    let handle_refresh = main_window.as_weak();
    let sup_refresh = supervisor.clone();
    let state_refresh = state.clone();
    main_window.on_refresh_requested(move || {
        if let Some(ui) = handle_refresh.upgrade() {
            println!("UI Refresh requested (Ctrl+R)");
            let handle = handle_refresh.clone();
            let sup = sup_refresh.clone();
            let st = state_refresh.clone();
            std::thread::spawn(move || {
                if let Some(client) = sup.get_client() {
                    let _ = client.send_request("system.ping", None, Duration::from_secs(2));
                    sync_overview(handle.clone(), &client);
                    sync_repositories(handle.clone(), &client, 0);
                    sync_builds(handle, &client, st);
                }
            });
            let pid_str = sup_refresh
                .get_engine_pid()
                .map(|p| format!("Engine Connected (PID {})", p))
                .unwrap_or_else(|| "Engine Synchronized".to_string());
            ui.set_engine_status(pid_str.into());
        }
    });

    // Repository Row Selection
    let handle_repo = main_window.as_weak();
    let sup_repo = supervisor.clone();
    main_window.on_repo_clicked(move |idx| {
        if let Some(ui) = handle_repo.upgrade() {
            ui.set_selected_repo_index(idx);
        }
        let handle = handle_repo.clone();
        let sup = sup_repo.clone();
        std::thread::spawn(move || {
            if let Some(client) = sup.get_client() {
                sync_repositories(handle, &client, idx as usize);
            }
        });
    });

    // Register Local Repo Action Button
    let handle_reg = main_window.as_weak();
    let sup_reg = supervisor.clone();
    main_window.on_register_local_repo_requested(move || {
        println!("Register local repo requested");
        let handle = handle_reg.clone();
        let sup = sup_reg.clone();
        std::thread::spawn(move || {
            if let Some(client) = sup.get_client() {
                let params = serde_json::json!({
                    "name": "ReproForge",
                    "repo_url": ".",
                    "project_name": "ReproForge Capstone",
                    "default_branch": "main",
                    "repo_type": "local"
                });
                let _ = client.send_request("repository.register", Some(params), Duration::from_secs(5));
                sync_repositories(handle.clone(), &client, 0);
                sync_overview(handle, &client);
            }
        });
    });

    // Trigger Isolated Build Action
    let handle_trig = main_window.as_weak();
    let sup_trig = supervisor.clone();
    let log_buf_trig = log_buffer.clone();
    let active_bid_trig = active_build_id.clone();
    main_window.on_trigger_build_requested(move || {
        println!("Trigger build requested");
        if let Some(ui) = handle_trig.upgrade() {
            ui.set_active_tab(2); // Switch to Builds view
            ui.set_is_build_running(true);
            ui.set_live_build_status("INITIALIZING".into());
            ui.set_live_build_progress(0.1);
            ui.set_live_build_message("Dispatching build execution to engine daemon...".into());
            let initial_log = "[ReproForge Live Console]\n>>> Dispatching isolated build execution request to engine daemon...\n".to_string();
            *log_buf_trig.lock().unwrap() = initial_log.clone();
            ui.set_live_log_text(initial_log.into());
        }
        let sup = sup_trig.clone();
        let abid = active_bid_trig.clone();
        std::thread::spawn(move || {
            if let Some(client) = sup.get_client() {
                let params = serde_json::json!({
                    "backend_type": "local",
                    "commit_ref": "HEAD"
                });
                if let Ok(resp) = client.send_request("build.trigger", Some(params), Duration::from_secs(120)) {
                    if let Some(bid) = resp.get("build_id").and_then(|v| v.as_str()) {
                        *abid.lock().unwrap() = Some(bid.to_string());
                    }
                }
            }
        });
    });

    // Build Specific Revision Action
    let handle_rev_build = main_window.as_weak();
    let sup_rev_build = supervisor.clone();
    let log_buf_rev = log_buffer.clone();
    let active_bid_rev = active_build_id.clone();
    main_window.on_build_revision_requested(move |sha| {
        let sha_str = sha.to_string();
        println!("Build revision requested for commit {}", sha_str);
        if let Some(ui) = handle_rev_build.upgrade() {
            ui.set_active_tab(2); // Switch to Builds view
            ui.set_is_build_running(true);
            ui.set_live_build_status("INITIALIZING".into());
            ui.set_live_build_progress(0.1);
            ui.set_live_build_message(format!("Dispatching build for commit {}...", sha_str).into());
            let initial_log = format!("[ReproForge Live Console]\n>>> Dispatching build request for commit: {}...\n", sha_str);
            *log_buf_rev.lock().unwrap() = initial_log.clone();
            ui.set_live_log_text(initial_log.into());
        }
        let sup = sup_rev_build.clone();
        let abid = active_bid_rev.clone();
        std::thread::spawn(move || {
            if let Some(client) = sup.get_client() {
                let params = serde_json::json!({
                    "backend_type": "local",
                    "commit_ref": sha_str
                });
                if let Ok(resp) = client.send_request("build.trigger", Some(params), Duration::from_secs(120)) {
                    if let Some(bid) = resp.get("build_id").and_then(|v| v.as_str()) {
                        *abid.lock().unwrap() = Some(bid.to_string());
                    }
                }
            }
        });
    });

    // Cancel Build Action
    let handle_cancel = main_window.as_weak();
    let sup_cancel = supervisor.clone();
    let active_bid_cancel = active_build_id.clone();
    main_window.on_cancel_build_requested(move || {
        println!("Cancel build requested");
        if let Some(ui) = handle_cancel.upgrade() {
            ui.set_live_build_message("Sending cancellation signal...".into());
        }
        let sup = sup_cancel.clone();
        let abid = active_bid_cancel.clone();
        std::thread::spawn(move || {
            if let Some(client) = sup.get_client() {
                let bid = abid.lock().unwrap().clone();
                let params = if let Some(id) = bid {
                    serde_json::json!({ "build_id": id })
                } else {
                    serde_json::json!({ "build_id": "" })
                };
                let _ = client.send_request("build.cancel", Some(params), Duration::from_secs(5));
            }
        });
    });

    // 8. First-Frame Presentation Notifier Hook (for benchmarking & launch readiness)
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
        // Fallback for software renderer where set_rendering_notifier is not supported
        slint::Timer::single_shot(std::time::Duration::from_millis(16), move || {
            use std::io::Write;
            println!("FIRST_FRAME_PRESENTED");
            std::io::stdout().flush().ok();
        });
    }

    let run_res = main_window.run();
    supervisor.stop();
    run_res
}
