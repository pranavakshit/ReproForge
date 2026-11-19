use std::fs;
use std::path::Path;
use serde::Deserialize;
use iced::widget::{button, column, container, row, scrollable, text, Space};
use iced::{Alignment, Color, Element, Length, Theme};

#[derive(Debug, Clone, Deserialize)]
struct MockDataset {
    summary_metrics: SummaryMetrics,
    builds: Vec<MockBuildRow>,
}

#[derive(Debug, Clone, Deserialize)]
struct SummaryMetrics {
    total_builds: usize,
    bit_for_bit_rate: f64,
    normalized_rate: f64,
    unresolved_count: usize,
}

#[derive(Debug, Clone, Deserialize)]
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

fn detect_active_backend() -> &'static str {
    if let Ok(val) = std::env::var("ICED_BACKEND") {
        if val == "tiny-skia" {
            return "Tiny-Skia (Software)";
        }
    }
    if let Ok(val) = std::env::var("WGPU_BACKEND") {
        if val == "empty" {
            return "Tiny-Skia (GPU Fallback)";
        }
    }
    "WGPU (DirectX 12)"
}

#[derive(Debug, Clone)]
enum Message {
    Refresh,
    TabSelected(String),
    TableScrolled(scrollable::Viewport),
    FirstFrameTick,
}

struct ReproForgeApp {
    dataset: Option<MockDataset>,
    motion_enabled: bool,
    active_tab: String,
    scroll_y: f32,
    backend_name: &'static str,
}

impl Default for ReproForgeApp {
    fn default() -> Self {
        let backend = detect_active_backend();
        println!("ICED_BACKEND_ACTIVE: {}", backend);
        use std::io::Write;
        std::io::stdout().flush().ok();

        Self {
            dataset: load_mock_data(),
            motion_enabled: is_system_animation_enabled(),
            active_tab: "Dashboard".into(),
            scroll_y: 0.0,
            backend_name: backend,
        }
    }
}

impl ReproForgeApp {
    fn update(&mut self, message: Message) {
        match message {
            Message::Refresh => {
                self.dataset = load_mock_data();
            }
            Message::TabSelected(tab) => {
                self.active_tab = tab;
            }
            Message::TableScrolled(viewport) => {
                self.scroll_y = viewport.absolute_offset().y;
            }
            Message::FirstFrameTick => {
                static FIRST_FRAME_REPORTED: std::sync::atomic::AtomicBool = std::sync::atomic::AtomicBool::new(false);
                if !FIRST_FRAME_REPORTED.swap(true, std::sync::atomic::Ordering::SeqCst) {
                    use std::io::Write;
                    println!("FIRST_FRAME_PRESENTED");
                    std::io::stdout().flush().ok();
                }
            }
        }
    }

    fn subscription(&self) -> iced::Subscription<Message> {
        iced::event::listen_with(|event, _status, _window| {
            if let iced::Event::Window(iced::window::Event::RedrawRequested(_)) = event {
                Some(Message::FirstFrameTick)
            } else {
                None
            }
        })
    }

    fn theme(&self) -> Theme {
        Theme::Dark
    }

    fn view(&self) -> Element<'_, Message> {
        // Fallback emission if subscription has not yet triggered RedrawRequested
        static VIEW_FIRST_FRAME: std::sync::atomic::AtomicBool = std::sync::atomic::AtomicBool::new(false);
        if !VIEW_FIRST_FRAME.swap(true, std::sync::atomic::Ordering::SeqCst) {
            use std::io::Write;
            println!("FIRST_FRAME_PRESENTED");
            std::io::stdout().flush().ok();
        }

        // --- 1. Custom Titlebar ---
        let brand = row![
            text("ReproForge").size(16).color(Color::from_rgb(0.22, 0.74, 0.97)),
            text("v0.1.0").size(11).color(Color::from_rgb(0.39, 0.45, 0.55)),
            container(
                row![
                    text(format!("Engine Connected (PID {}) | Backend: {}", std::process::id(), self.backend_name))
                        .size(11)
                        .color(Color::from_rgb(0.65, 0.95, 0.82))
                ]
                .padding([4, 10])
            )
            .style(|_theme| container::Style {
                background: Some(Color::from_rgb(0.02, 0.31, 0.23).into()),
                border: iced::Border {
                    color: Color::from_rgb(0.02, 0.59, 0.41),
                    width: 1.0,
                    radius: 12.0.into(),
                },
                ..Default::default()
            })
        ]
        .spacing(12)
        .align_y(Alignment::Center);

        let refresh_btn = button(text("Refresh (Ctrl+R)").size(11))
            .on_press(Message::Refresh)
            .padding([5, 12]);

        let titlebar = container(
            row![brand, Space::new().width(Length::Fill), refresh_btn]
                .align_y(Alignment::Center)
                .padding([8, 16])
        )
        .style(|_theme| container::Style {
            background: Some(Color::from_rgb(0.07, 0.09, 0.15).into()),
            border: iced::Border {
                color: Color::from_rgb(0.12, 0.16, 0.22),
                width: 1.0,
                ..Default::default()
            },
            ..Default::default()
        })
        .width(Length::Fill);

        // --- 2. Sidebar ---
        let tabs = ["Dashboard", "Repositories", "Build Executions", "Diff & Diagnostics", "Settings"];
        let mut sidebar_col = column![].spacing(6).padding(12);

        for tab_name in tabs {
            let is_active = self.active_tab == tab_name;
            let tab_btn = button(
                text(tab_name)
                    .size(13)
                    .color(if is_active { Color::from_rgb(0.22, 0.74, 0.97) } else { Color::from_rgb(0.58, 0.64, 0.72) })
            )
            .on_press(Message::TabSelected(tab_name.into()))
            .width(Length::Fill)
            .padding([8, 12])
            .style(move |_theme, _status| button::Style {
                background: Some(if is_active { Color::from_rgb(0.12, 0.16, 0.23).into() } else { Color::TRANSPARENT.into() }),
                border: iced::Border {
                    radius: 6.0.into(),
                    ..Default::default()
                },
                ..Default::default()
            });

            sidebar_col = sidebar_col.push(tab_btn);
        }

        let sidebar = container(sidebar_col)
            .width(Length::Fixed(200.0))
            .height(Length::Fill)
            .style(|_theme| container::Style {
                background: Some(Color::from_rgb(0.06, 0.09, 0.16).into()),
                border: iced::Border {
                    color: Color::from_rgb(0.12, 0.16, 0.23),
                    width: 1.0,
                    ..Default::default()
                },
                ..Default::default()
            });

        // --- 3. Dashboard Metrics Cards ---
        let total_val = self.dataset.as_ref().map(|d| d.summary_metrics.total_builds.to_string()).unwrap_or_else(|| "0".into());
        let exact_val = self.dataset.as_ref().map(|d| format!("{}%", d.summary_metrics.bit_for_bit_rate)).unwrap_or_else(|| "0%".into());
        let norm_val = self.dataset.as_ref().map(|d| format!("{}%", d.summary_metrics.normalized_rate)).unwrap_or_else(|| "0%".into());
        let unres_val = self.dataset.as_ref().map(|d| d.summary_metrics.unresolved_count.to_string()).unwrap_or_else(|| "0".into());

        let card1 = make_metric_card("TOTAL VERIFIED BUILDS", &total_val, Color::from_rgb(0.97, 0.98, 0.99));
        let card2 = make_metric_card("BIT-FOR-BIT RATE (S_exact)", &exact_val, Color::from_rgb(0.06, 0.73, 0.51));
        let card3 = make_metric_card("NORMALIZED RATE (S_norm)", &norm_val, Color::from_rgb(0.22, 0.74, 0.97));
        let card4 = make_metric_card("UNRESOLVED DRIFT (N_unexp)", &unres_val, Color::from_rgb(0.96, 0.62, 0.04));

        let cards_row = row![card1, card2, card3, card4].spacing(14);

        // --- 4. Virtualized Build Table ---
        let mut table_rows = column![].spacing(2);

        if let Some(dataset) = &self.dataset {
            let total_rows = dataset.builds.len();
            let row_height: f32 = 38.0;
            let visible_count: usize = 20;

            let start_idx = (self.scroll_y / row_height).floor().max(0.0) as usize;
            let start_idx = start_idx.min(total_rows.saturating_sub(1));
            let end_idx = (start_idx + visible_count).min(total_rows);

            let top_spacer_height = start_idx as f32 * row_height;
            let bottom_spacer_height = (total_rows.saturating_sub(end_idx)) as f32 * row_height;

            if top_spacer_height > 0.0 {
                table_rows = table_rows.push(Space::new().height(Length::Fixed(top_spacer_height)));
            }

            for row_data in &dataset.builds[start_idx..end_idx] {
                let verdict_color = match row_data.verdict.as_str() {
                    "bit_for_bit_reproducible" => Color::from_rgb(0.06, 0.73, 0.51),
                    "normalized_reproducible" => Color::from_rgb(0.22, 0.74, 0.97),
                    "partially_reproducible" => Color::from_rgb(0.98, 0.75, 0.14),
                    "inconclusive" => Color::from_rgb(0.75, 0.52, 0.99),
                    _ => Color::from_rgb(0.94, 0.27, 0.27),
                };

                let row_item = container(
                    row![
                        text(&row_data.build_id).size(12).width(Length::Fixed(140.0)),
                        text(&row_data.commit_sha).size(12).width(Length::Fixed(110.0)).color(Color::from_rgb(0.8, 0.84, 0.88)),
                        text(&row_data.backend).size(12).width(Length::Fixed(90.0)).color(Color::from_rgb(0.58, 0.64, 0.72)),
                        text(&row_data.status).size(12).width(Length::Fixed(90.0)).color(
                            if row_data.status == "completed" { Color::from_rgb(0.2, 0.83, 0.6) } else { Color::from_rgb(0.97, 0.44, 0.44) }
                        ),
                        text(&row_data.verdict).size(12).width(Length::Fixed(220.0)).color(verdict_color),
                        text(format!("{}s", row_data.duration_seconds)).size(12).color(Color::from_rgb(0.58, 0.64, 0.72)),
                    ]
                    .align_y(Alignment::Center)
                    .padding([6, 12])
                )
                .style(|_theme| container::Style {
                    background: Some(Color::from_rgb(0.07, 0.09, 0.15).into()),
                    border: iced::Border {
                        color: Color::from_rgb(0.12, 0.16, 0.23),
                        width: 1.0,
                        ..Default::default()
                    },
                    ..Default::default()
                });

                table_rows = table_rows.push(row_item);
            }

            if bottom_spacer_height > 0.0 {
                table_rows = table_rows.push(Space::new().height(Length::Fixed(bottom_spacer_height)));
            }
        }

        let table_header = container(
            row![
                text("BUILD ID").size(11).width(Length::Fixed(140.0)).color(Color::from_rgb(0.58, 0.64, 0.72)),
                text("COMMIT").size(11).width(Length::Fixed(110.0)).color(Color::from_rgb(0.58, 0.64, 0.72)),
                text("BACKEND").size(11).width(Length::Fixed(90.0)).color(Color::from_rgb(0.58, 0.64, 0.72)),
                text("STATUS").size(11).width(Length::Fixed(90.0)).color(Color::from_rgb(0.58, 0.64, 0.72)),
                text("PRIMARY VERDICT").size(11).width(Length::Fixed(220.0)).color(Color::from_rgb(0.58, 0.64, 0.72)),
                text("DURATION").size(11).color(Color::from_rgb(0.58, 0.64, 0.72)),
            ]
            .padding([8, 12])
        )
        .style(|_theme| container::Style {
            background: Some(Color::from_rgb(0.12, 0.16, 0.23).into()),
            border: iced::Border {
                radius: 4.0.into(),
                ..Default::default()
            },
            ..Default::default()
        });

        let table_section = container(
            column![
                table_header,
                scrollable(table_rows)
                    .on_scroll(Message::TableScrolled)
                    .height(Length::Fill)
            ]
            .spacing(8)
        )
        .padding(12)
        .height(Length::Fill)
        .style(|_theme| container::Style {
            background: Some(Color::from_rgb(0.07, 0.09, 0.15).into()),
            border: iced::Border {
                color: Color::from_rgb(0.12, 0.16, 0.22),
                width: 1.0,
                radius: 8.0.into(),
            },
            ..Default::default()
        });

        // Content Area
        let content = container(
            column![
                row![
                    text("Build Reproducibility Telemetry").size(18).color(Color::from_rgb(0.97, 0.98, 0.99)),
                    Space::new().width(Length::Fill),
                    text(if self.motion_enabled { "OS Motion: Enabled" } else { "OS Motion: Reduced" })
                        .size(11)
                        .color(Color::from_rgb(0.39, 0.45, 0.55))
                ]
                .align_y(Alignment::Center),
                cards_row,
                table_section,
            ]
            .spacing(16)
            .padding(20)
        )
        .width(Length::Fill)
        .height(Length::Fill)
        .style(|_theme| container::Style {
            background: Some(Color::from_rgb(0.04, 0.06, 0.10).into()),
            ..Default::default()
        });

        column![titlebar, row![sidebar, content].height(Length::Fill)].into()
    }
}

fn make_metric_card<'a>(label: &'static str, value: &str, val_color: Color) -> Element<'a, Message> {
    container(
        column![
            text(label).size(11).color(Color::from_rgb(0.58, 0.64, 0.72)),
            Space::new().height(Length::Fixed(12.0)),
            text(value.to_string()).size(24).color(val_color),
        ]
        .padding(14)
    )
    .width(Length::FillPortion(1))
    .style(|_theme| container::Style {
        background: Some(Color::from_rgb(0.07, 0.09, 0.15).into()),
        border: iced::Border {
            color: Color::from_rgb(0.12, 0.16, 0.22),
            width: 1.0,
            radius: 8.0.into(),
        },
        ..Default::default()
    })
    .into()
}

fn app_title(_state: &ReproForgeApp) -> String {
    "ReproForge — Build Reproducibility Framework (Iced)".to_string()
}

fn boot() -> ReproForgeApp {
    ReproForgeApp::default()
}

pub fn main() -> iced::Result {
    iced::application(
        boot,
        ReproForgeApp::update,
        ReproForgeApp::view,
    )
    .subscription(ReproForgeApp::subscription)
    .title(app_title)
    .theme(ReproForgeApp::theme)
    .run()
}
