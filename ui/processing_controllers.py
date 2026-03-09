from dataclasses import dataclass, field
from typing import Any, Callable, List, Optional

from api.client import APIKeyManager
from core.worker_state import WorkerState


@dataclass
class BatchResult:
    file: str
    status: str
    message: str


@dataclass
class BatchSummary:
    total: int
    success_items: List[BatchResult]
    error_items: List[BatchResult]
    cancelled_items: List[BatchResult]
    remaining: int

    @property
    def summary_text(self) -> str:
        summary_parts: List[str] = []
        if self.success_items:
            summary_parts.append(f"成功 {len(self.success_items)}")
        if self.error_items:
            summary_parts.append(f"失败 {len(self.error_items)}")
        if self.cancelled_items:
            summary_parts.append(f"取消 {len(self.cancelled_items)}")
        if self.remaining:
            summary_parts.append(f"未处理 {self.remaining}")
        return "，".join(summary_parts) if summary_parts else "无任务执行"


@dataclass
class BatchController:
    queue: List[str] = field(default_factory=list)
    results: List[BatchResult] = field(default_factory=list)
    current_index: int = -1
    current_file: Optional[str] = None
    cancelled: bool = False
    api_key_manager: Optional[APIKeyManager] = None

    def start(
        self,
        file_paths: List[str],
        key_entries: List[dict[str, Any]],
        on_keys_updated: Optional[Callable[[List[dict[str, Any]]], None]] = None,
    ):
        self.queue = list(file_paths)
        self.results = []
        self.current_index = -1
        self.current_file = None
        self.cancelled = False
        self.api_key_manager = (
            APIKeyManager(key_entries=key_entries, on_keys_updated=on_keys_updated)
            if self.is_batch_mode and key_entries
            else None
        )

    @property
    def is_batch_mode(self) -> bool:
        return len(self.queue) > 1

    def advance(self) -> Optional[str]:
        self.current_index += 1
        if self.current_index >= len(self.queue):
            self.current_file = None
            return None
        self.current_file = self.queue[self.current_index]
        return self.current_file

    def record_result(self, status: str, message: str):
        if not self.is_batch_mode or not self.current_file:
            return
        self.results.append(BatchResult(self.current_file, status, message))

    def mark_cancelled(self):
        self.cancelled = True

    def has_more_files(self) -> bool:
        return self.current_index < len(self.queue) - 1

    def build_summary(self) -> BatchSummary:
        total = len(self.queue)
        success_items = [item for item in self.results if item.status == "success"]
        error_items = [item for item in self.results if item.status == "error"]
        cancelled_items = [item for item in self.results if item.status == "cancelled"]
        remaining = max(0, total - (self.current_index + 1))
        return BatchSummary(
            total=total,
            success_items=success_items,
            error_items=error_items,
            cancelled_items=cancelled_items,
            remaining=remaining,
        )

    def reset(self):
        self.queue = []
        self.results = []
        self.current_index = -1
        self.current_file = None
        self.cancelled = False
        self.api_key_manager = None


@dataclass
class RetryController:
    pending_state: Optional[WorkerState] = None

    def set(self, state: Optional[WorkerState]):
        self.pending_state = state

    def clear(self):
        self.pending_state = None

    def has_pending(self) -> bool:
        return self.pending_state is not None

    def pop(self) -> Optional[WorkerState]:
        state = self.pending_state
        self.pending_state = None
        return state
