slint::include_modules!();

use std::fs;
use std::path::Path;
use std::rc::Rc;
use serde::Deserialize;
use slint::{ModelRc, VecModel};

#[derive(Deserialize)]
struct MockDataset {
    summary_metrics: SummaryMetrics,
    builds: Vec<MockBuildRow>,
}

#[derive(Deserialize)]
struct SummaryMetrics {
    total_builds: usize,
    bit_for_bit_rate: f64,
    normalized_rate: f64,
    unresolved_count: usize,
}

#[derive(Deserialize)]
struct MockBuildRow {
    build_id: String,
    commit_sha: String,
    backend: String,
    status: String,
    verdict: String,
    duration_seconds: f64,
}

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

fn generate_mock_dataset(n: usize) -> MockDataset {
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

    MockDataset {
        summary_metrics: SummaryMetrics {
            total_builds: n,
            bit_for_bit_rate: 85.4,
            normalized_rate: 96.2,
            unresolved_count: 14,
        },
        builds,
    }
}

fn load_mock_data() -> Option<MockDataset> {
    if let Ok(count_str) = std::env::var("REPROFORGE_BENCH_RECORDS") {
        if let Ok(n) = count_str.parse::<usize>() {
            return Some(generate_mock_dataset(n));
        }
    }

    let candidate_paths = [
        "data/mock_builds.json",
        "../data/mock_builds.json",
        "prototyping/benchmarks/data/mock_builds.json",
    ];

    for path_str in candidate_paths {
        let p = Path::new(path_str);
        if p.exists() {
            if let Ok(content) = fs::read_to_string(p) {
                if let Ok(dataset) = serde_json::from_str::<MockDataset>(&content) {
                    return Some(dataset);
                }
            }
        }
    }
    Some(generate_mock_dataset(200))
}

fn populate_ui(ui: &MainWindow, dataset: &MockDataset) {
    ui.set_total_builds(dataset.summary_metrics.total_builds.to_string().into());
    ui.set_bit_for_bit_rate(format!("{}%", dataset.summary_metrics.bit_for_bit_rate).into());
    ui.set_normalized_rate(format!("{}%", dataset.summary_metrics.normalized_rate).into());
    ui.set_unresolved_count(dataset.summary_metrics.unresolved_count.to_string().into());

    let rows: Vec<BuildRowData> = dataset.builds.iter().map(|b| {
        BuildRowData {
            build_id: b.build_id.clone().into(),
            commit_sha: b.commit_sha.clone().into(),
            backend: b.backend.clone().into(),
            status: b.status.clone().into(),
            verdict: b.verdict.clone().into(),
            duration: format!("{}s", b.duration_seconds).into(),
        }
    }).collect();

    let model = Rc::new(VecModel::from(rows));
    ui.set_build_rows(ModelRc::from(model));
}

fn main() -> Result<(), slint::PlatformError> {
    let main_window = MainWindow::new()?;

    // Check OS reduced motion preference
    let motion_enabled = is_system_animation_enabled();
    main_window.set_motion_enabled(motion_enabled);

    // Initial load
    if let Some(dataset) = load_mock_data() {
        populate_ui(&main_window, &dataset);
    }

    // Connect refresh callback
    let handle = main_window.as_weak();
    main_window.on_refresh_requested(move || {
        if let Some(ui) = handle.upgrade() {
            if let Some(dataset) = load_mock_data() {
                populate_ui(&ui, &dataset);
            }
        }
    });

    // Notify upon first-frame presentation
    let first_frame = std::sync::Arc::new(std::sync::atomic::AtomicBool::new(false));
    let ff_clone = first_frame.clone();
    main_window.window().set_rendering_notifier(move |state, _api| {
        if matches!(state, slint::RenderingState::AfterRendering) {
            if !ff_clone.swap(true, std::sync::atomic::Ordering::SeqCst) {
                use std::io::Write;
                println!("FIRST_FRAME_PRESENTED");
                std::io::stdout().flush().ok();
            }
        }
    }).ok();

    main_window.run()
}
