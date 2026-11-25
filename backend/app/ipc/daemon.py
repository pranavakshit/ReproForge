"""Python Background Engine Daemon with strict stdout isolation and async JSON-RPC 2.0 loop."""

import asyncio
import io
import os
import sys
import threading
import traceback
from typing import Any, Dict, Optional, Set
from backend.app.database.session import AsyncSessionLocal, init_db
from backend.app.ipc.dispatcher import RequestDispatcher
from backend.app.ipc.protocol import (
    JsonRpcErrorCode,
    JsonRpcErrorObject,
    JsonRpcErrorResponse,
    decode_line,
    encode_message,
)
from backend.app.repositories.build_repo import BuildRepository


class IpcDaemon:
    """Manages stdio JSON-RPC IPC with strict stdout isolation."""

    def __init__(self, log_to_file: bool = True) -> None:
        self.dispatcher = RequestDispatcher(on_notify=self.notify_client)
        self.log_to_file = log_to_file
        self._ipc_writer: Optional[io.TextIOWrapper] = None
        self._real_stdout_fd: Optional[int] = None
        self._write_lock = threading.Lock()
        self._inflight_tasks: Set[asyncio.Task] = set()

    def notify_client(self, method: str, params: Dict[str, Any]) -> None:
        """Send asynchronous JSON-RPC notification to connected client."""
        from backend.app.ipc.protocol import JsonRpcNotification
        notif = JsonRpcNotification(method=method, params=params)
        self.write_ipc_message(encode_message(notif))

    def _setup_stdout_isolation(self) -> None:
        """Isolate raw OS stdout for framed IPC messages only, redirecting sys.stdout to stderr/file."""
        # 1. Duplicate current OS stdout descriptor (fd 1) for exclusive IPC use
        self._real_stdout_fd = os.dup(1)
        raw_ipc_file = open(self._real_stdout_fd, "wb", buffering=0)
        self._ipc_writer = io.TextIOWrapper(raw_ipc_file, encoding="utf-8", write_through=True)

        # 2. Redirect sys.stdout and OS fd 1 to stderr or log file
        if self.log_to_file:
            log_dir = os.path.expanduser("~/.reproforge/logs")
            os.makedirs(log_dir, exist_ok=True)
            log_path = os.path.join(log_dir, "engine_daemon.log")
            log_fd = os.open(log_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND)
            os.dup2(log_fd, 1)  # Direct fd 1 to log file
            os.close(log_fd)
            sys.stdout = open(1, "w", encoding="utf-8", buffering=1)
        else:
            os.dup2(2, 1)  # Direct fd 1 to stderr (fd 2)
            sys.stdout = sys.stderr

    def write_ipc_message(self, line: str) -> None:
        """Write single-line JSONL message directly to isolated IPC stream."""
        with self._write_lock:
            if self._ipc_writer:
                self._ipc_writer.write(line)
                self._ipc_writer.flush()

    async def _dispatch_and_reply(self, request: Any) -> None:
        """Execute a single dispatched RPC request concurrently without blocking stdin."""
        try:
            response = await self.dispatcher.dispatch(request)
            if response is not None:
                self.write_ipc_message(encode_message(response))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            sys.stderr.write(f"[IpcDaemon] Unhandled dispatch error for {request.method}: {exc}\n{traceback.format_exc()}\n")

    async def run(self) -> None:
        """Run the daemon async loop."""
        self._setup_stdout_isolation()

        # Ensure schema tables exist on clean/ephemeral databases
        try:
            await init_db()
        except Exception as exc:
            sys.stderr.write(f"[IpcDaemon] Warning during DB init: {exc}\n")

        # Step 1: Reconcile any orphaned active builds from past engine crashes
        try:
            async with AsyncSessionLocal() as session:
                async with session.begin():
                    build_repo = BuildRepository(session)
                    reconciled = await build_repo.reconcile_interrupted_builds()
                    if reconciled:
                        sys.stderr.write(f"[IpcDaemon] Reconciled {reconciled} orphaned build(s) to EXECUTION_INTERRUPTED\n")
        except Exception as exc:
            sys.stderr.write(f"[IpcDaemon] Warning during orphan reconciliation: {exc}\n")

        # Step 2: Read loop over stdin using threadpool executor for cross-platform stdio safety
        sys.stderr.write(f"[IpcDaemon] Engine Daemon ready (PID {os.getpid()}). Listening on stdin...\n")

        while not self.dispatcher.shutdown_requested.is_set():
            try:
                line_str = await asyncio.to_thread(sys.stdin.readline)
                if not line_str:
                    # Pipe closed by supervisor / EOF
                    sys.stderr.write("[IpcDaemon] Stdin EOF received, shutting down...\n")
                    break

                line_str = line_str.strip()
                if not line_str:
                    continue

                # Parse request
                try:
                    request = decode_line(line_str)
                except ValueError as val_err:
                    err_resp = JsonRpcErrorResponse(
                        id=None,
                        error=JsonRpcErrorObject(
                            code=JsonRpcErrorCode.PARSE_ERROR,
                            message=str(val_err),
                        ),
                    )
                    self.write_ipc_message(encode_message(err_resp))
                    continue

                # Dispatch concurrently so heartbeats and cancellations are never blocked by builds
                if request.method == "system.shutdown":
                    await self._dispatch_and_reply(request)
                else:
                    task = asyncio.create_task(self._dispatch_and_reply(request))
                    self._inflight_tasks.add(task)
                    task.add_done_callback(self._inflight_tasks.discard)

            except asyncio.CancelledError:
                break
            except Exception as exc:
                sys.stderr.write(f"[IpcDaemon] Error processing request: {exc}\n{traceback.format_exc()}\n")

        # Cleanup any inflight background tasks on shutdown
        if self._inflight_tasks:
            for t in list(self._inflight_tasks):
                t.cancel()
            await asyncio.gather(*self._inflight_tasks, return_exceptions=True)

        sys.stderr.write(f"[IpcDaemon] Engine Daemon (PID {os.getpid()}) exiting cleanly.\n")


def main() -> None:
    """CLI entrypoint for the background daemon."""
    daemon = IpcDaemon(log_to_file=True)
    asyncio.run(daemon.run())


if __name__ == "__main__":
    main()
