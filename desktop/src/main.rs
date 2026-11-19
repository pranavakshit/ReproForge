slint::include_modules!();

mod data;

use std::rc::Rc;
use std::sync::{Arc, Mutex};
use slint::{ModelRc, VecModel};
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

    // 2. Set App Header & Engine Status
    main_window.set_engine_status(format!("Engine Connected (PID {})", std::process::id()).into());
    main_window.set_active_backend(renderer_name.clone().into());
    main_window.set_is_software_renderer(is_software);

    // 3. Load Datasets
    let record_count = std::env::var("REPROFORGE_BENCH_RECORDS")
        .ok()
        .and_then(|v| v.parse::<usize>().ok())
        .unwrap_or(200);

    let all_builds = generate_mock_builds(record_count);

    // 4. Populate Overview View
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

    // 5. Populate Repositories View
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

    // 6. Populate Diff & Diagnostics View
    let diff_items: Vec<DiffEntry> = get_sample_diff_entries()
        .into_iter()
        .map(|d| DiffEntry {
            path: d.path.into(),
            change_type: d.change_type.into(),
            detail: d.detail.into(),
        })
        .collect();
    main_window.set_diff_entries(ModelRc::from(Rc::new(VecModel::from(diff_items))));

    // 7. Initialize State & Builds Table
    let state = Arc::new(Mutex::new(AppState::new(all_builds)));
    {
        let s = state.lock().unwrap();
        populate_builds_ui(&main_window, &s);
    }

    // Connect Search Changed Callback
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

    // Connect Verdict Filter Changed Callback
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

    // Connect Refresh Callback
    let handle_refresh = main_window.as_weak();
    main_window.on_refresh_requested(move || {
        if let Some(ui) = handle_refresh.upgrade() {
            println!("UI Refresh requested (Ctrl+R)");
            ui.set_engine_status(format!("Engine Synchronized (PID {})", std::process::id()).into());
        }
    });

    // Connect Repository Click Selection
    let handle_repo = main_window.as_weak();
    main_window.on_repo_clicked(move |idx| {
        if let Some(ui) = handle_repo.upgrade() {
            ui.set_selected_repo_index(idx);
        }
    });

    // First-Frame Presentation Notifier Hook (for benchmarking)
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

    main_window.run()
}
