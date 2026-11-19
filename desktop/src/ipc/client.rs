use std::collections::HashMap;
use std::io::Write;
use std::process::ChildStdin;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{mpsc, Arc, Mutex};
use std::time::Duration;

use crate::ipc::protocol::{JsonRpcRequest, JsonRpcResponse};

type ResponseSender = mpsc::Sender<Result<JsonRpcResponse, String>>;

#[derive(Clone)]
pub struct IpcClient {
    stdin: Arc<Mutex<Option<ChildStdin>>>,
    pending_requests: Arc<Mutex<HashMap<u64, ResponseSender>>>,
    next_id: Arc<AtomicU64>,
}

impl IpcClient {
    pub fn new(stdin: ChildStdin) -> Self {
        Self {
            stdin: Arc::new(Mutex::new(Some(stdin))),
            pending_requests: Arc::new(Mutex::new(HashMap::new())),
            next_id: Arc::new(AtomicU64::new(1)),
        }
    }

    pub fn send_request(
        &self,
        method: &str,
        params: Option<serde_json::Value>,
        timeout: Duration,
    ) -> Result<serde_json::Value, String> {
        let req_id = self.next_id.fetch_add(1, Ordering::SeqCst);
        let request = JsonRpcRequest::new(req_id, method, params);
        let encoded = request
            .encode()
            .map_err(|e| format!("Failed to encode request: {}", e))?;

        let (tx, rx) = mpsc::channel();
        {
            let mut pending = self.pending_requests.lock().map_err(|e| e.to_string())?;
            pending.insert(req_id, tx);
        }

        {
            let mut stdin_guard = self.stdin.lock().map_err(|e| e.to_string())?;
            if let Some(ref mut stdin) = *stdin_guard {
                stdin
                    .write_all(encoded.as_bytes())
                    .map_err(|e| format!("Failed to write to stdin: {}", e))?;
                stdin
                    .flush()
                    .map_err(|e| format!("Failed to flush stdin: {}", e))?;
            } else {
                return Err("IPC stdin is closed".into());
            }
        }

        match rx.recv_timeout(timeout) {
            Ok(Ok(response)) => {
                if let Some(err) = response.error {
                    Err(format!("JSON-RPC Error [{}]: {}", err.code, err.message))
                } else if let Some(res) = response.result {
                    Ok(res)
                } else {
                    Ok(serde_json::Value::Null)
                }
            }
            Ok(Err(err_msg)) => Err(err_msg),
            Err(mpsc::RecvTimeoutError::Timeout) => {
                let mut pending = self.pending_requests.lock().map_err(|e| e.to_string())?;
                pending.remove(&req_id);
                Err(format!("Request timed out after {:?}", timeout))
            }
            Err(mpsc::RecvTimeoutError::Disconnected) => {
                Err("IPC channel disconnected before response received".into())
            }
        }
    }

    pub fn send_notification(
        &self,
        method: &str,
        params: Option<serde_json::Value>,
    ) -> Result<(), String> {
        let notif = JsonRpcRequest::notification(method, params);
        let encoded = notif
            .encode()
            .map_err(|e| format!("Failed to encode notification: {}", e))?;

        let mut stdin_guard = self.stdin.lock().map_err(|e| e.to_string())?;
        if let Some(ref mut stdin) = *stdin_guard {
            stdin
                .write_all(encoded.as_bytes())
                .map_err(|e| format!("Failed to write notification to stdin: {}", e))?;
            stdin
                .flush()
                .map_err(|e| format!("Failed to flush notification: {}", e))?;
            Ok(())
        } else {
            Err("IPC stdin is closed".into())
        }
    }

    pub fn handle_incoming_line(&self, line: &str) {
        let trimmed = line.trim();
        if trimmed.is_empty() {
            return;
        }

        if let Ok(resp) = serde_json::from_str::<JsonRpcResponse>(trimmed) {
            if let Some(id) = resp.id {
                let sender = {
                    let mut pending = self.pending_requests.lock().ok();
                    pending.as_mut().and_then(|p| p.remove(&id))
                };
                if let Some(tx) = sender {
                    let _ = tx.send(Ok(resp));
                }
            }
        }
    }

    pub fn abort_pending(&self, reason: &str) {
        let mut pending = match self.pending_requests.lock() {
            Ok(p) => p,
            Err(_) => return,
        };
        for (_, tx) in pending.drain() {
            let _ = tx.send(Err(reason.to_string()));
        }
    }

    pub fn close(&self) {
        if let Ok(mut guard) = self.stdin.lock() {
            *guard = None;
        }
        self.abort_pending("IPC client closed");
    }
}
