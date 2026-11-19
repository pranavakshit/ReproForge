pub mod client;
pub mod protocol;

pub use client::IpcClient;
pub use protocol::{
    HandshakeResult, JsonRpcErrorObj, JsonRpcNotification, JsonRpcRequest, JsonRpcResponse,
    PingResult,
};
