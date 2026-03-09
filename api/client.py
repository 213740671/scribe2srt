import os
import time
import threading
from typing import Optional, Any, Dict, Tuple, List, Callable

import requests
from requests_toolbelt.multipart.encoder import (
    MultipartEncoder,
    MultipartEncoderMonitor,
)
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from PySide6.QtCore import QObject, Signal, QRunnable

from core.config import sanitize_settings, update_settings_file
from core.ffmpeg_utils import get_media_info

# ==============================================================================
#  API Constants and Helpers
# ==============================================================================

ELEVENLABS_STT_API_URL = "https://api.elevenlabs.io/v1/speech-to-text"
ELEVENLABS_STT_PARAMS = {"allow_unauthenticated": "1"}
DEFAULT_STT_MODEL_ID = "scribe_v1"

# --- 连接配置 ---
# 连接超时：建立连接最多等待10秒，读取响应最多等待300秒（5分钟）
CONNECTION_TIMEOUT = (10, 300)
# 最大重试次数
MAX_RETRIES = 3
# 重试等待时间（指数退避基数）
RETRY_BASE_DELAY = 2  # 秒

# --- Header Configuration ---
# 使用固定的 User-Agent 避免被识别为异常流量
DEFAULT_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
DEFAULT_ACCEPT_LANGUAGE = "zh-CN,zh;q=0.9,en;q=0.8"

BASE_HEADERS = {
    "accept": "*/*",
    "accept-encoding": "gzip, deflate, br, zstd",
    "origin": "https://elevenlabs.io",
    "referer": "https://elevenlabs.io/",
    "sec-fetch-dest": "empty",
    "sec-fetch-mode": "cors",
    "sec-fetch-site": "same-site",
}

# 可重试的错误类型
RETRYABLE_EXCEPTIONS = (
    requests.exceptions.ConnectionError,
    requests.exceptions.Timeout,
    requests.exceptions.ChunkedEncodingError,
)


def is_retryable_error(error: Exception) -> bool:
    """判断错误是否可重试"""
    if isinstance(error, requests.exceptions.HTTPError):
        status_code = error.response.status_code
        return status_code in [429, 502, 503, 504]
    return isinstance(error, RETRYABLE_EXCEPTIONS)


def classify_error(error: Exception) -> Tuple[str, str, bool, bool]:
    error_msg = str(error).lower()

    if "429" in error_msg or "too many requests" in error_msg:
        return (
            "请求过于频繁",
            "当前 IP 的免费额度已用完。\n\n建议：\n1. 等待几分钟后重试\n2. 在设置中添加 API Key 获得更稳定的服务（每月 10k credits ≈ 45小时）",
            False,
            True,
        )

    if "401" in error_msg or "403" in error_msg or "unauthorized" in error_msg:
        return (
            "API Key 无效",
            "API Key 无效或已过期。\n\n请检查设置中的 API Key 是否正确，或访问 ElevenLabs 官网重新获取。",
            False,
            True,
        )

    if "insufficient_quota" in error_msg or "quota" in error_msg:
        return (
            "API Key 额度不足",
            "当前 API Key 的额度已用完。\n\n将尝试使用下一个可用的 API Key。",
            False,
            True,
        )

    if "connection" in error_msg or "remote end closed" in error_msg:
        return ("连接错误", "网络连接不稳定。正在自动重试...", True, False)

    if "timeout" in error_msg:
        return (
            "请求超时",
            "请求超时，可能是文件较大或网络较慢。正在重试...",
            True,
            False,
        )

    return ("处理失败", str(error), False, False)


class APIKeyManager:
    def __init__(
        self,
        api_keys: Optional[str] = None,
        key_entries: Optional[List[Dict[str, Any]]] = None,
        on_keys_updated: Optional[Callable[[List[Dict[str, Any]]], None]] = None,
    ):
        self._key_entries: List[Dict[str, Any]] = []
        self._current_index: int = 0
        self._failed_keys: set[str] = set()
        self._lock = threading.Lock()
        self._on_keys_updated = on_keys_updated

        if key_entries is not None:
            self._set_entries(key_entries)
        elif api_keys:
            self._parse_keys(api_keys)

    @staticmethod
    def _normalize_entries(raw_entries: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        normalized: List[Dict[str, Any]] = []
        for item in raw_entries:
            if not isinstance(item, dict):
                continue
            key = str(item.get("key", "")).strip()
            if not key or key.startswith("#"):
                continue
            normalized.append(
                {
                    "key": key,
                    "active": bool(item.get("active", True)),
                    "inactive_reason": str(item.get("inactive_reason", "")).strip(),
                }
            )
        return normalized

    def _emit_update(self):
        if self._on_keys_updated:
            self._on_keys_updated(self.get_key_entries())

    def _set_entries(self, key_entries: List[Dict[str, Any]]):
        self._key_entries = self._normalize_entries(key_entries)
        self._current_index = 0
        self._failed_keys = set()

    def _parse_keys(self, keys_str: str):
        if not keys_str:
            self._set_entries([])
            return

        key_entries: List[Dict[str, Any]] = []
        for line in keys_str.strip().split("\n"):
            key = line.strip()
            if key and not key.startswith("#"):
                key_entries.append({"key": key, "active": True, "inactive_reason": ""})

        self._set_entries(key_entries)

    def set_keys(self, api_keys: str):
        with self._lock:
            self._parse_keys(api_keys)
        self._emit_update()

    def set_key_entries(self, key_entries: List[Dict[str, Any]]):
        with self._lock:
            self._set_entries(key_entries)
        self._emit_update()

    def get_key_entries(self) -> List[Dict[str, Any]]:
        with self._lock:
            return [dict(entry) for entry in self._key_entries]

    def get_all_keys(self) -> List[str]:
        with self._lock:
            return [entry["key"] for entry in self._key_entries]

    def _get_available_indices(self) -> List[int]:
        return [
            index
            for index, entry in enumerate(self._key_entries)
            if entry.get("active", True) and entry["key"] not in self._failed_keys
        ]

    def get_current_key(self) -> Optional[str]:
        with self._lock:
            available_indices = self._get_available_indices()
            if not available_indices:
                return None

            if self._current_index >= len(self._key_entries):
                self._current_index = 0

            start_index = self._current_index
            while self._current_index not in available_indices:
                self._current_index = (self._current_index + 1) % len(self._key_entries)
                if self._current_index == start_index:
                    return None

            return self._key_entries[self._current_index]["key"]

    def mark_key_failed(self, key: str):
        with self._lock:
            if any(entry["key"] == key for entry in self._key_entries):
                self._failed_keys.add(key)

    def set_key_active(self, key: str, active: bool, inactive_reason: str = "") -> bool:
        updated = False
        with self._lock:
            for entry in self._key_entries:
                if entry["key"] == key:
                    entry["active"] = active
                    entry["inactive_reason"] = "" if active else inactive_reason.strip()
                    if active:
                        self._failed_keys.discard(key)
                    else:
                        self._failed_keys.add(key)
                    updated = True
                    break

            if updated and self._current_index >= len(self._key_entries):
                self._current_index = 0

        if updated:
            self._emit_update()
        return updated

    def deactivate_key(self, key: str, inactive_reason: str = "manual") -> bool:
        return self.set_key_active(key, False, inactive_reason=inactive_reason)

    def activate_key(self, key: str) -> bool:
        return self.set_key_active(key, True)

    def switch_to_next_key(self) -> Optional[str]:
        with self._lock:
            if not self._key_entries:
                return None

            available_indices = self._get_available_indices()
            if not available_indices:
                return None

            self._current_index = (self._current_index + 1) % len(self._key_entries)
            start_index = self._current_index
            while self._current_index not in available_indices:
                self._current_index = (self._current_index + 1) % len(self._key_entries)
                if self._current_index == start_index:
                    return None

            return self._key_entries[self._current_index]["key"]

    def has_available_keys(self) -> bool:
        with self._lock:
            return bool(self._get_available_indices())

    def get_key_count(self) -> int:
        with self._lock:
            return len(self._key_entries)

    def get_available_key_count(self) -> int:
        with self._lock:
            return len(self._get_available_indices())

    def get_active_key_count(self) -> int:
        with self._lock:
            return len(
                [entry for entry in self._key_entries if entry.get("active", True)]
            )

    def reset_failed_keys(self):
        with self._lock:
            self._failed_keys = {
                entry["key"]
                for entry in self._key_entries
                if not entry.get("active", True)
            }
            self._current_index = 0

    @classmethod
    def from_settings(
        cls,
        settings: Dict[str, Any],
        on_keys_updated: Optional[Callable[[List[Dict[str, Any]]], None]] = None,
    ) -> "APIKeyManager":
        sanitized = sanitize_settings(settings)
        return cls(
            key_entries=sanitized.get("elevenlabs_api_keys", []),
            on_keys_updated=on_keys_updated,
        )

    @staticmethod
    def persist_key_entries_to_settings_file(
        settings_file: str, key_entries: List[Dict[str, Any]]
    ):
        normalized_entries = APIKeyManager._normalize_entries(key_entries)

        def apply_key_updates(settings: Dict[str, Any]):
            settings["elevenlabs_api_keys"] = normalized_entries
            settings["elevenlabs_api_key"] = "\n".join(
                entry["key"] for entry in normalized_entries
            )

        if settings_file:
            update_settings_file(apply_key_updates)


def create_retry_session(
    retries=MAX_RETRIES,
    backoff_factor=1.5,
    status_forcelist=(500, 502, 503, 504),
    allowed_methods=("HEAD", "GET", "POST"),
):
    """创建一个带自动重试机制的 requests Session"""
    session = requests.Session()

    retry = Retry(
        total=retries,
        read=retries,
        connect=retries,
        backoff_factor=backoff_factor,
        status_forcelist=status_forcelist,
        allowed_methods=allowed_methods,
        raise_on_status=False,
    )

    adapter = HTTPAdapter(max_retries=retry, pool_connections=10, pool_maxsize=10)

    session.mount("https://", adapter)
    session.mount("http://", adapter)

    return session


class UploaderSignals(QObject):
    """Defines the signals available from a running Uploader thread."""

    finished = Signal(dict)
    error = Signal(str)
    progress = Signal(int, int)
    log_message = Signal(str)  # 添加日志信号


class Uploader(QRunnable):
    def __init__(
        self,
        file_path: str,
        payload: Dict,
        headers: Dict,
        params: Optional[Dict] = None,
        max_retries: int = MAX_RETRIES,
        api_key_manager: Optional[APIKeyManager] = None,
    ):
        super().__init__()
        self.signals = UploaderSignals()
        self.file_path = file_path
        self.payload = payload
        self.headers = headers
        self.params = params or ELEVENLABS_STT_PARAMS
        self.max_retries = max_retries
        self.session = create_retry_session()
        self._is_cancelled = False
        self.api_key_manager = api_key_manager

    def _is_operation_cancelled(self, cancel_checker=None) -> bool:
        if self._is_cancelled:
            return True
        return bool(cancel_checker and cancel_checker())

    def _emit_log(self, message: str, log_handler=None):
        if log_handler:
            log_handler(message)
        else:
            self.signals.log_message.emit(message)

    def _sleep_with_cancellation(self, delay: float, cancel_checker=None):
        deadline = time.monotonic() + delay
        while time.monotonic() < deadline:
            if self._is_operation_cancelled(cancel_checker):
                raise RuntimeError("任务被用户取消。")
            time.sleep(min(0.1, max(0.0, deadline - time.monotonic())))

    def _perform_upload(self, progress_handler=None, cancel_checker=None):
        if self._is_operation_cancelled(cancel_checker):
            raise RuntimeError("任务被用户取消。")

        with open(self.file_path, "rb") as f_audio:
            self.payload["file"] = (
                os.path.basename(self.file_path),
                f_audio,
                self.payload["file"][2],
            )

            def on_progress(monitor):
                if self._is_operation_cancelled(cancel_checker):
                    raise IOError("Upload cancelled by user.")
                if progress_handler:
                    progress_handler(monitor.bytes_read, monitor.len)
                else:
                    self.signals.progress.emit(monitor.bytes_read, monitor.len)

            encoder = MultipartEncoder(fields=self.payload)
            monitor = MultipartEncoderMonitor(encoder, on_progress)
            headers = self.headers.copy()
            headers["Content-Type"] = monitor.content_type

            response = self.session.post(
                ELEVENLABS_STT_API_URL,
                params=self.params,
                headers=headers,
                data=monitor,
                timeout=CONNECTION_TIMEOUT,
            )
            response.raise_for_status()
            return response.json()

    def _upload_with_retries(
        self, progress_handler=None, cancel_checker=None, log_handler=None
    ):
        key_switch_count = 0

        while True:
            switched_key = False
            for attempt in range(self.max_retries):
                if self._is_operation_cancelled(cancel_checker):
                    raise RuntimeError("任务被用户取消。")

                try:
                    return self._perform_upload(progress_handler, cancel_checker)
                except Exception as e:
                    if self._is_operation_cancelled(cancel_checker):
                        raise RuntimeError("任务被用户取消。") from e

                    error_title, error_hint, is_retryable, is_key_error = (
                        classify_error(e)
                    )

                    if is_key_error and self.api_key_manager:
                        current_key = self.api_key_manager.get_current_key()
                        if current_key:
                            self.api_key_manager.deactivate_key(
                                current_key, inactive_reason="quota_exhausted"
                            )
                            self._emit_log(
                                "检测到当前 API Key 额度不足，已自动设为未激活。需要手动重新激活后才会再次使用。",
                                log_handler,
                            )

                        next_key = None
                        if self.api_key_manager.has_available_keys():
                            next_key = self.api_key_manager.switch_to_next_key()

                        if next_key:
                            key_switch_count += 1
                            self.headers["xi-api-key"] = next_key
                            self._emit_log(
                                f"已切换到下一个激活 Key（第 {key_switch_count} 次切换）",
                                log_handler,
                            )
                            switched_key = True
                            break

                        raise RuntimeError(
                            "所有激活的 API Key 都不可用。额度不足的 Key 已自动设为未激活，请手动激活可用 Key 或添加更多 Key 后再试。"
                        ) from e

                    if not is_retryable or attempt >= self.max_retries - 1:
                        raise RuntimeError(f"{error_title}: {error_hint}") from e

                    delay = RETRY_BASE_DELAY**attempt
                    self._emit_log(
                        f"{error_title}，{delay}秒后重试...（第{attempt + 1}/{self.max_retries}次）",
                        log_handler,
                    )
                    self._sleep_with_cancellation(delay, cancel_checker)

            if not switched_key:
                raise RuntimeError("上传失败，未能完成请求。")

    def execute_sync(
        self, progress_handler=None, cancel_checker=None, log_handler=None
    ):
        try:
            return self._upload_with_retries(
                progress_handler, cancel_checker, log_handler
            )
        finally:
            self.session.close()

    def run(self):
        try:
            result = self.execute_sync()
            if not self._is_cancelled:
                self.signals.finished.emit(result)
        except RuntimeError as e:
            if not self._is_cancelled:
                self.signals.error.emit(str(e))
        except Exception as e:
            if not self._is_cancelled:
                error_title, error_hint, _, _ = classify_error(e)
                self.signals.error.emit(f"{error_title}: {error_hint}")

    def progress_callback(self, monitor):
        if self._is_cancelled:
            raise IOError("Upload cancelled by user.")
        self.signals.progress.emit(monitor.bytes_read, monitor.len)

    def cancel(self):
        self._is_cancelled = True
        self.signals.error.emit("任务被用户取消。")
        self.session.close()


class ElevenLabsSTTClient:
    def __init__(
        self,
        signals_forwarder: Optional[Any] = None,
        ffmpeg_available: bool = False,
        api_key: Optional[str] = None,
        api_key_manager: Optional[APIKeyManager] = None,
    ):
        self._signals = signals_forwarder
        self.ffmpeg_available = ffmpeg_available
        self.api_key_manager = api_key_manager or APIKeyManager(api_key)

    def _log(self, message: str):
        if self._signals and hasattr(self._signals, "log_message"):
            self._signals.log_message.emit(f"{message}")

    def log_media_info(self, file_path: str) -> Optional[Dict[str, Any]]:
        """Logs file size and, if possible, media duration and codec."""
        try:
            file_size_mb = os.path.getsize(file_path) / (1024 * 1024)
            log_str = f"  文件大小: {file_size_mb:.2f} MB"

            media_info = get_media_info(file_path, self._log)
            if media_info:
                duration = media_info.get("duration")
                codec = media_info.get("codec")
                if duration:
                    minutes, seconds = divmod(duration, 60)
                    log_str += f" | 时长: {int(minutes):02d}分{int(seconds):02d}秒"
                if codec:
                    log_str += f" | 音频编码: {codec}"

            self._log(log_str)
            return media_info
        except Exception as e:
            self._log(f"  获取文件信息时出错: {e}")
            return None

    def prepare_upload_task(
        self, file_path: str, language_code: str, tag_audio_events: bool
    ) -> Optional[Uploader]:
        """Prepares an Uploader runnable task without starting it."""
        if not os.path.exists(file_path):
            self._log(f"错误：文件 '{file_path}' 未找到。")
            return None

        self._log(f"准备处理文件: {os.path.basename(file_path)}")
        self.log_media_info(file_path)

        mime_type = "application/octet-stream"
        ext = os.path.splitext(file_path)[1].lower()
        if ext in [".mp3", ".mp4", ".m4a", ".wav", ".flac", ".ogg", ".mov", ".aac"]:
            mime_type = (
                f"audio/{ext.replace('.', '')}"
                if ext not in [".mp4", ".mov"]
                else f"video/{ext.replace('.', '')}"
            )

        payload = {
            "model_id": DEFAULT_STT_MODEL_ID,
            "diarize": "true",
            "tag_audio_events": str(tag_audio_events).lower(),
            "file": (os.path.basename(file_path), None, mime_type),
        }
        if language_code and language_code.lower() != "auto":
            payload["language_code"] = language_code

        headers = BASE_HEADERS.copy()
        headers["user-agent"] = DEFAULT_USER_AGENT
        headers["accept-language"] = DEFAULT_ACCEPT_LANGUAGE

        # 根据是否有 API Key 决定使用认证模式还是未认证模式
        current_key = self.api_key_manager.get_current_key()
        if current_key:
            headers["xi-api-key"] = current_key
            params = {}
            key_count = self.api_key_manager.get_key_count()
            active_key_count = self.api_key_manager.get_active_key_count()
            if key_count > 1:
                self._log(
                    f"使用 API Key 认证模式（共 {key_count} 个 Key，当前激活 {active_key_count} 个）"
                )
            else:
                self._log("使用 API Key 认证模式")
        else:
            params = ELEVENLABS_STT_PARAMS
            if self.api_key_manager.get_key_count() > 0:
                self._log("当前没有激活的 API Key，已回退到未认证模式（免费额度）")
            else:
                self._log("使用未认证模式（免费额度）")

        return Uploader(
            file_path, payload, headers, params, api_key_manager=self.api_key_manager
        )
