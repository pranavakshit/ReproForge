use std::io::{BufRead, BufReader};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::Duration;

use crate::ipc::client::IpcClient;
use crate::supervisor::spawner;

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum SupervisorStatus {
    Stopped,
    Starting,
    Connected { pid: u32, version: String },
    Reconnecting { attempt: u32, max_attempts: u32 },
    Failed(String),
}

pub struct ProcessSupervisor {
    status: Arc<Mutex<SupervisorStatus>>,
    client: Arc<Mutex<Option<IpcClient>>>,
    engine_pid: Arc<Mutex<Option<u32>>>,
    stop_signal: Arc<AtomicBool>,
    status_callbacks: Arc<Mutex<Vec<Box<dyn Fn(&SupervisorStatus) + Send + 'static>>>>,
}

impl ProcessSupervisor {
    pub fn new() -> Self {
        Self {
            status: Arc::new(Mutex::new(SupervisorStatus::Stopped)),
            client: Arc::new(Mutex::new(None)),
            engine_pid: Arc::new(Mutex::new(None)),
            stop_signal: Arc::new(AtomicBool::new(false)),
            status_callbacks: Arc::new(Mutex::new(Vec::new())),
        }
    }

    pub fn get_status(&self) -> SupervisorStatus {
        self.status.lock().unwrap().clone()
    }

    pub fn get_client(&self) -> Option<IpcClient> {
        self.client.lock().unwrap().clone()
    }

    pub fn get_engine_pid(&self) -> Option<u32> {
        *self.engine_pid.lock().unwrap()
    }

    pub fn on_status_change<F>(&self, callback: F)
    where
        F: Fn(&SupervisorStatus) + Send + 'static,
    {
        let mut cbs = self.status_callbacks.lock().unwrap();
        cbs.push(Box::new(callback));
    }

    fn update_status(&self, new_status: SupervisorStatus) {
        {
            let mut guard = self.status.lock().unwrap();
            *guard = new_status.clone();
        }
        let cbs = self.status_callbacks.lock().unwrap();
        for cb in cbs.iter() {
            cb(&new_status);
        }
    }

    pub fn start(self: &Arc<Self>) {
        self.stop_signal.store(false, Ordering::SeqCst);
        let supervisor = Arc::clone(self);

        thread::Builder::new()
            .name("reproforge-supervisor".into())
            .spawn(move || {
                supervisor.run_supervision_loop();
            })
            .expect("Failed to spawn supervisor thread");
    }

    pub fn stop(&self) {
        self.stop_signal.store(true, Ordering::SeqCst);

        // Attempt graceful system.shutdown RPC
        if let Some(client) = self.get_client() {
            let _ = client.send_request("system.shutdown", None, Duration::from_millis(1500));
            client.close();
        }

        {
            let mut c_guard = self.client.lock().unwrap();
            *c_guard = None;
        }

        self.update_status(SupervisorStatus::Stopped);
    }

    fn run_supervision_loop(&self) {
        let max_attempts = 3;
        let mut retry_count = 0;

        while !self.stop_signal.load(Ordering::SeqCst) {
            if retry_count == 0 {
                self.update_status(SupervisorStatus::Starting);
            } else {
                self.update_status(SupervisorStatus::Reconnecting {
                    attempt: retry_count,
                    max_attempts,
                });
            }

            // Spawn daemon child process
            let spawn_result = spawner::spawn_engine_daemon();
            let mut child = match spawn_result {
                Ok((c, _py_path)) => c,
                Err(err) => {
                    retry_count += 1;
                    if retry_count >= max_attempts {
                        self.update_status(SupervisorStatus::Failed(format!(
                            "Spawn failed after {} retries: {}",
                            max_attempts, err
                        )));
                        return;
                    }
                    let delay = Duration::from_millis(500 * (1 << (retry_count - 1)));
                    thread::sleep(delay);
                    continue;
                }
            };

            let stdin = match child.stdin.take() {
                Some(s) => s,
                None => {
                    let _ = child.kill();
                    retry_count += 1;
                    continue;
                }
            };

            let stdout = match child.stdout.take() {
                Some(s) => s,
                None => {
                    let _ = child.kill();
                    retry_count += 1;
                    continue;
                }
            };

            let client = IpcClient::new(stdin);
            {
                let mut c_guard = self.client.lock().unwrap();
                *c_guard = Some(client.clone());
            }

            // Reader thread for stdout
            let reader_client = client.clone();
            let stop_sig_reader = Arc::clone(&self.stop_signal);
            let reader_handle = thread::Builder::new()
                .name("ipc-stdout-reader".into())
                .spawn(move || {
                    let buf_reader = BufReader::new(stdout);
                    for line in buf_reader.lines() {
                        if stop_sig_reader.load(Ordering::SeqCst) {
                            break;
                        }
                        match line {
                            Ok(text) => reader_client.handle_incoming_line(&text),
                            Err(_) => break,
                        }
                    }
                    reader_client.abort_pending("IPC stdout disconnected");
                });

            // Handshake verification
            let handshake_res = client.send_request("system.handshake", None, Duration::from_secs(4));
            let connected_info = match handshake_res {
                Ok(val) => {
                    let pid = val
                        .get("engine_pid")
                        .and_then(|v| v.as_u64())
                        .map(|v| v as u32)
                        .unwrap_or_else(|| child.id());
                    let ver = val
                        .get("protocol_version")
                        .and_then(|v| v.as_str())
                        .unwrap_or("1.0.0")
                        .to_string();
                    Some((pid, ver))
                }
                Err(err) => {
                    eprintln!("[Supervisor] Handshake failed: {}", err);
                    None
                }
            };

            if let Some((pid, ver)) = connected_info {
                {
                    let mut p_guard = self.engine_pid.lock().unwrap();
                    *p_guard = Some(pid);
                }
                retry_count = 0;
                self.update_status(SupervisorStatus::Connected { pid, version: ver });

                // Active connected loop with 5s heartbeats
                while !self.stop_signal.load(Ordering::SeqCst) {
                    // Sleep 5s in small increments
                    for _ in 0..50 {
                        if self.stop_signal.load(Ordering::SeqCst) {
                            break;
                        }
                        thread::sleep(Duration::from_millis(100));
                    }

                    if self.stop_signal.load(Ordering::SeqCst) {
                        break;
                    }

                    // Check if child unexpectedly exited
                    match child.try_wait() {
                        Ok(Some(status)) => {
                            eprintln!("[Supervisor] Child process exited with: {:?}", status);
                            break;
                        }
                        Ok(None) => {}
                        Err(e) => {
                            eprintln!("[Supervisor] Error querying child status: {:?}", e);
                            break;
                        }
                    }

                    // Send heartbeat ping
                    let ping_param = serde_json::json!({ "timestamp": std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().as_secs_f64() });
                    if let Err(e) = client.send_request("system.ping", Some(ping_param), Duration::from_secs(4)) {
                        eprintln!("[Supervisor] Heartbeat ping failed: {}", e);
                        break;
                    }
                }
            }

            // Process disconnected or failed handshake
            client.close();
            let _ = child.kill();
            let _ = child.wait();
            let _ = reader_handle;

            {
                let mut c_guard = self.client.lock().unwrap();
                *c_guard = None;
                let mut p_guard = self.engine_pid.lock().unwrap();
                *p_guard = None;
            }

            if self.stop_signal.load(Ordering::SeqCst) {
                break;
            }

            retry_count += 1;
            if retry_count >= max_attempts {
                self.update_status(SupervisorStatus::Failed(format!(
                    "Daemon process terminated unexpectedly; {} recovery attempts exhausted",
                    max_attempts
                )));
                break;
            }

            let backoff = Duration::from_millis(500 * (1 << (retry_count - 1)));
            thread::sleep(backoff);
        }

        self.update_status(SupervisorStatus::Stopped);
    }
}
