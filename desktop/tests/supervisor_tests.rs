use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use std::time::Duration;

use reproforge_desktop::supervisor::{find_python_interpreter, find_repo_root, ProcessSupervisor, SupervisorStatus};

#[test]
fn test_python_interpreter_discovery() {
    let root = find_repo_root();
    assert!(root.is_some(), "Repository root should be detected");

    let py = find_python_interpreter();
    assert!(py.is_some(), "Python interpreter should be detected in .venv or PATH");
    let py_path = py.unwrap();
    assert!(py_path.exists(), "Detected Python path must exist on disk");
}

#[test]
fn test_supervisor_lifecycle_and_handshake() {
    let supervisor = Arc::new(ProcessSupervisor::new());
    assert_eq!(supervisor.get_status(), SupervisorStatus::Stopped);

    let connected_flag = Arc::new(AtomicBool::new(false));
    let connected_flag_clone = connected_flag.clone();

    supervisor.on_status_change(move |status| {
        if matches!(status, SupervisorStatus::Connected { .. }) {
            connected_flag_clone.store(true, Ordering::SeqCst);
        }
    });

    supervisor.start();

    // Wait for connection with timeout
    let mut connected = false;
    for _ in 0..60 {
        if let SupervisorStatus::Connected { pid, ref version } = supervisor.get_status() {
            assert!(pid > 0);
            assert_eq!(version, "1.0.0");
            connected = true;
            break;
        }
        std::thread::sleep(Duration::from_millis(200));
    }

    assert!(connected, "Supervisor should connect and complete handshake within 12 seconds");
    assert!(connected_flag.load(Ordering::SeqCst));

    // Test client call
    let client = supervisor.get_client().expect("Client should be available when connected");
    let ping_res = client.send_request("system.ping", None, Duration::from_secs(3));
    assert!(ping_res.is_ok(), "Ping RPC should succeed: {:?}", ping_res);
    let ping_val = ping_res.unwrap();
    assert_eq!(ping_val.get("status").and_then(|s| s.as_str()), Some("healthy"));

    // Test method not found error
    let err_res = client.send_request("unknown.method", None, Duration::from_secs(3));
    assert!(err_res.is_err());
    assert!(err_res.unwrap_err().contains("Method 'unknown.method' not found"));

    // Test build cancellation RPC (non-existent build returns error but does NOT crash engine)
    let cancel_res = client.send_request(
        "build.cancel",
        Some(serde_json::json!({ "build_id": "non-existent-build" })),
        Duration::from_secs(3),
    );
    assert!(cancel_res.is_err(), "Non-existent build cancellation should return error");

    // Verify engine remains alive and healthy after cancel attempt
    let ping_after = client.send_request("system.ping", None, Duration::from_secs(3));
    assert!(ping_after.is_ok(), "Engine should remain alive and responsive after failed cancel");

    // Clean shutdown
    supervisor.stop();
    std::thread::sleep(Duration::from_millis(500));
    assert_eq!(supervisor.get_status(), SupervisorStatus::Stopped);
}

#[test]
fn test_supervisor_crash_detection_and_auto_restart() {
    let supervisor = Arc::new(ProcessSupervisor::new());
    supervisor.start();

    // Wait for initial connection
    let mut initial_pid = 0;
    for _ in 0..50 {
        if let SupervisorStatus::Connected { pid, .. } = supervisor.get_status() {
            initial_pid = pid;
            break;
        }
        std::thread::sleep(Duration::from_millis(100));
    }
    assert!(initial_pid > 0, "Initial engine PID must be established");

    // Forcibly kill the python process to simulate unexpected crash
    #[cfg(target_os = "windows")]
    {
        let _ = std::process::Command::new("taskkill")
            .args(["/F", "/PID", &initial_pid.to_string()])
            .output();
    }
    #[cfg(not(target_os = "windows"))]
    {
        let _ = std::process::Command::new("kill")
            .args(["-9", &initial_pid.to_string()])
            .output();
    }

    // Supervisor should detect the crash and automatically restart + reconnect with a new PID
    let mut restarted_with_new_pid = false;
    for _ in 0..80 {
        if let SupervisorStatus::Connected { pid, .. } = supervisor.get_status() {
            if pid != initial_pid {
                restarted_with_new_pid = true;
                break;
            }
        }
        std::thread::sleep(Duration::from_millis(150));
    }

    assert!(
        restarted_with_new_pid,
        "Supervisor must detect process termination and auto-restart with a new PID"
    );

    // Verify communication with the revived engine
    let client = supervisor.get_client().expect("Client must be available after auto-restart");
    let ping_res = client.send_request("system.ping", None, Duration::from_secs(3));
    assert!(ping_res.is_ok(), "Revived engine should respond to ping");

    supervisor.stop();
}
