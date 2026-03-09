# -*- coding: utf-8 -*-

"""
主窗口模块，负责UI的显示、事件处理和与核心逻辑的交互。
"""

import sys
import os
import json
import ctypes
from typing import List, Optional

from PySide6.QtWidgets import (
    QMainWindow,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QPushButton,
    QLabel,
    QTextEdit,
    QFileDialog,
    QMessageBox,
    QComboBox,
)
from PySide6.QtCore import QThread, Qt, QTimer

# --- 从重构后的模块中导入 ---
from core.config import (
    LANGUAGES,
    CODEC_EXTENSION_MAP,
    DEFAULT_AUDIO_EXTENSION,
    load_settings_file,
    save_settings_file,
    update_settings_file,
)
from core.worker import Worker
from core.ffmpeg_utils import is_ffmpeg_available, extract_audio, get_media_info
from core.srt_processor import create_srt_from_json, create_word_level_srt_from_json
from .widgets import CustomCheckBox
from .settings_dialog import SettingsDialog
from .async_settings_dialog import AsyncSettingsDialog
from .segmented_progress_bar import SegmentedProgressBar
from .api_key_dialog import ApiKeyDialog
from .processing_controllers import BatchController, RetryController
from api.client import APIKeyManager
from core.worker_state import WorkerState


class MainWindow(QMainWindow):
    """
    应用程序的主窗口。
    管理UI交互，并将处理任务委托给后台Worker。
    """

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Scribe -> SRT (Powered by ElevenLabs)")
        self.setGeometry(100, 100, 750, 600)
        self.setAcceptDrops(True)
        self._apply_dark_mode_title_bar()

        self.selected_file_paths: List[str] = []
        self.batch_controller = BatchController()
        self.retry_controller = RetryController()
        self.task_thread: Optional[QThread] = None
        self.active_worker: Optional[Worker] = None
        self.temp_audio_file: Optional[str] = None
        self.upload_complete_logged = False

        self.load_settings()
        self.setup_ui()

        self.ffmpeg_available = self._check_ffmpeg()
        self._connect_signals()

    def setup_ui(self):
        """初始化和布局UI控件。"""
        container = QWidget()
        main_layout = QVBoxLayout(container)
        main_layout.setSpacing(15)
        main_layout.setContentsMargins(20, 20, 20, 20)

        # --- 文件拖放区域 ---
        self.file_drop_label = QLabel("将音视频或JSON文件拖拽到此处\n\n或")
        self.file_drop_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.file_drop_label.setObjectName("FileDropLabel")

        self.select_button = QPushButton("点击选择文件")
        self.word_srt_button = QPushButton("批量转词级SRT")

        file_layout = QVBoxLayout()
        file_layout.addWidget(self.file_drop_label)

        file_button_layout = QHBoxLayout()
        file_button_layout.setSpacing(10)
        file_button_layout.addWidget(self.select_button)
        file_button_layout.addWidget(self.word_srt_button)
        file_layout.addLayout(file_button_layout)
        main_layout.addLayout(file_layout)

        # --- 选项区域 ---
        options_layout = QHBoxLayout()
        options_layout.setSpacing(10)

        self.lang_label = QLabel("源语言:")
        self.lang_combo = QComboBox()
        self.lang_combo.addItems(list(LANGUAGES.keys()))
        self.lang_combo.setCurrentText("自动检测")

        self.audio_events_checkbox = CustomCheckBox("识别声音事件")
        self.audio_events_checkbox.setChecked(False)

        self.async_settings_button = QPushButton("并发处理设置")
        self.settings_button = QPushButton("字幕设置")
        self.api_key_button = QPushButton("API 设置")

        options_layout.addWidget(self.lang_label)
        options_layout.addWidget(self.lang_combo)
        options_layout.addSpacing(20)
        options_layout.addWidget(self.audio_events_checkbox)
        options_layout.addStretch(1)
        options_layout.addWidget(self.api_key_button)
        options_layout.addWidget(self.async_settings_button)
        options_layout.addWidget(self.settings_button)
        main_layout.addLayout(options_layout)

        # --- 进度条和标签 ---
        self.progress_label = QLabel("")
        self.progress_label.setVisible(False)
        self.progress_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        # 使用新的分段进度条
        self.segmented_progress_bar = SegmentedProgressBar()
        self.segmented_progress_bar.setVisible(False)

        main_layout.addWidget(self.progress_label)
        main_layout.addWidget(self.segmented_progress_bar)

        # --- 操作按钮 ---
        action_layout = QHBoxLayout()
        self.start_button = QPushButton("生成字幕")
        self.start_button.setObjectName("StartButton")
        self.start_button.setEnabled(False)

        self.cancel_button = QPushButton("取消任务")
        self.cancel_button.setVisible(False)

        action_layout.addWidget(self.start_button)
        action_layout.addWidget(self.cancel_button)
        main_layout.addLayout(action_layout)

        # --- 日志区域 ---
        self.log_area = QTextEdit()
        self.log_area.setReadOnly(True)
        self.log_area.setPlaceholderText("处理日志将在这里显示...")
        main_layout.addWidget(self.log_area)

        self.setCentralWidget(container)

    def _connect_signals(self):
        """连接所有UI控件的信号到槽函数。"""
        self.select_button.clicked.connect(self.select_files)
        self.word_srt_button.clicked.connect(self.batch_convert_json_to_word_srt)
        self.start_button.clicked.connect(self.start_process)
        self.cancel_button.clicked.connect(self.cancel_process)
        self.async_settings_button.clicked.connect(self.open_async_settings_dialog)
        self.settings_button.clicked.connect(self.open_settings_dialog)
        self.api_key_button.clicked.connect(self.open_api_key_dialog)

    def _apply_dark_mode_title_bar(self):
        """(仅Windows) 尝试设置窗口标题栏为暗色模式。"""
        if sys.platform == "win32":
            try:
                HWND = self.winId()
                if HWND:
                    DWMWA_USE_IMMERSIVE_DARK_MODE = 20
                    value = ctypes.c_int(1)
                    ctypes.windll.dwmapi.DwmSetWindowAttribute(
                        HWND,
                        DWMWA_USE_IMMERSIVE_DARK_MODE,
                        ctypes.byref(value),
                        ctypes.sizeof(value),
                    )
            except (AttributeError, TypeError, OSError) as e:
                print(f"无法设置暗色标题栏: {e}")

    def _check_ffmpeg(self) -> bool:
        """检查FFmpeg是否可用并记录日志。"""
        available = is_ffmpeg_available()
        if available:
            self.log_area.append("✅ FFmpeg 已找到，将启用视频文件处理。")
        else:
            self.log_area.append("⚠️ 未找到 FFmpeg。处理视频时将尝试直接上传原始文件。")
            self.log_area.append(
                "   为获得最佳体验，推荐安装 FFmpeg 并将其添加到系统 PATH。"
            )
        return available

    # --- 设置管理 ---
    def load_settings(self):
        """从文件加载设置，如果文件不存在则使用默认值。"""
        self.settings = load_settings_file()

        self.max_subtitle_duration = self.settings["max_subtitle_duration"]
        self.split_duration_min = self.settings["split_duration_min"]
        self.save_settings()

    def save_settings(self):
        """保存当前设置到文件。"""
        self.settings = save_settings_file(self.settings)

    def open_settings_dialog(self):
        """打开设置对话框并处理结果。"""
        dialog = SettingsDialog(self.settings, self)
        if dialog.exec():
            new_settings = dialog.get_settings()

            # 更新所有设置
            self.settings.update(new_settings)

            # 为了向后兼容，更新这些属性（移除pause_threshold）
            self.max_subtitle_duration = new_settings["max_subtitle_duration"]
            self.split_duration_min = new_settings["split_duration_min"]

            self.save_settings()
            self.log_area.append("字幕生成设置已更新。")

    def open_async_settings_dialog(self):
        """打开并发处理设置对话框并处理结果。"""
        dialog = AsyncSettingsDialog(self.settings, self)
        if dialog.exec():
            new_settings = dialog.get_settings()

            # 更新异步处理设置
            self.settings.update(new_settings)

            # 为了向后兼容，更新这些属性
            self.split_duration_min = new_settings["split_duration_min"]

            self.save_settings()
            self.log_area.append("并发处理设置已更新。")

    def _on_api_keys_updated(self, key_entries: List[dict]):
        normalized_entries = [dict(entry) for entry in key_entries]

        def apply_key_updates(settings: dict):
            settings["elevenlabs_api_keys"] = normalized_entries
            settings["elevenlabs_api_key"] = "\n".join(
                str(entry.get("key", "")) for entry in normalized_entries
            )

        self.settings = update_settings_file(apply_key_updates)

    def open_api_key_dialog(self):
        dialog = ApiKeyDialog(
            current_api_keys=self.settings.get("elevenlabs_api_keys", []), parent=self
        )
        if dialog.exec():
            key_entries = dialog.get_api_key_entries()
            self._on_api_keys_updated(key_entries)
            active_count = len([entry for entry in key_entries if entry.get("active", True)])
            total_count = len(key_entries)
            if total_count:
                self.log_area.append(
                    f"API Key 已更新（共 {total_count} 个，激活 {active_count} 个）。"
                )
            else:
                self.log_area.append("API Key 已清除，将使用免费模式。")

    # --- 文件处理与UI状态 ---
    def set_files(self, file_paths: Optional[List[str]]):
        """设置当前要处理的文件并更新UI。"""
        self.selected_file_paths = []
        self.batch_controller.current_file = None

        if file_paths:
            valid_paths = [path for path in file_paths if path and os.path.exists(path)]
            self.selected_file_paths = valid_paths

        if self.selected_file_paths:
            if len(self.selected_file_paths) == 1:
                file_name = os.path.basename(self.selected_file_paths[0])
                self.file_drop_label.setText(f"已选择:\n{file_name}")
            else:
                first_name = os.path.basename(self.selected_file_paths[0])
                self.file_drop_label.setText(
                    f"已选择 {len(self.selected_file_paths)} 个文件\n首个: {first_name}"
                )
            self.file_drop_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.start_button.setEnabled(True)
            self.log_area.clear()
        else:
            self.file_drop_label.setText("将音视频或JSON文件拖拽到此处\n\n或")
            self.file_drop_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.start_button.setEnabled(False)

    def select_files(self):
        """打开文件选择对话框。"""
        dialog_title = "选择文件"
        dialog_filter = (
            "支持的文件 (*.mp3 *.wav *.flac *.m4a *.aac *.mp4 *.mov *.mkv *.json);;"
            "所有文件 (*)"
        )
        file_paths, _ = QFileDialog.getOpenFileNames(
            self, dialog_title, "", dialog_filter
        )
        self.set_files(file_paths)

    def batch_convert_json_to_word_srt(self):
        dialog_title = "选择转录JSON文件"
        dialog_filter = "JSON 文件 (*.json);;所有文件 (*)"
        file_paths, _ = QFileDialog.getOpenFileNames(
            self, dialog_title, "", dialog_filter
        )
        if not file_paths:
            return

        self.log_area.clear()
        self.log_area.append("=" * 50)
        self.log_area.append(f"开始批量转换词级SRT，共 {len(file_paths)} 个 JSON 文件。")
        self.set_ui_enabled(False)

        success_items: list[tuple[str, str]] = []
        error_items: list[tuple[str, str]] = []

        try:
            for index, json_path in enumerate(file_paths, 1):
                display_name = os.path.basename(json_path)
                self.file_drop_label.setText(
                    f"词级SRT转换中 ({index}/{len(file_paths)}):\n{display_name}"
                )
                self.file_drop_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
                self.log_area.append("=" * 50)
                self.log_area.append(
                    f"正在转换第 {index}/{len(file_paths)} 个文件: {display_name}"
                )

                try:
                    with open(json_path, "r", encoding="utf-8") as f:
                        json_data = json.load(f)

                    srt_data = create_word_level_srt_from_json(json_data)
                    if not srt_data:
                        raise ValueError("JSON文件中未找到可用的词级时间戳数据。")

                    output_srt_path = os.path.splitext(json_path)[0] + ".srt"
                    with open(output_srt_path, "w", encoding="utf-8") as f:
                        f.write(srt_data)

                    self.log_area.append(f"✅ 词级SRT已保存到:\n{output_srt_path}")
                    success_items.append((json_path, output_srt_path))
                except Exception as e:
                    message = str(e)
                    self.log_area.append(f"❌ 转换失败: {display_name} -> {message}")
                    error_items.append((json_path, message))

            summary_parts = [f"成功 {len(success_items)}"]
            if error_items:
                summary_parts.append(f"失败 {len(error_items)}")
            summary_text = "，".join(summary_parts)

            self.log_area.append("=" * 50)
            self.log_area.append(f"词级SRT批量转换完成：{summary_text}")
            if error_items:
                self.log_area.append("失败列表：")
                for file_path, message in error_items:
                    self.log_area.append(
                        f" - {os.path.basename(file_path)}: {message}"
                    )

            QMessageBox.information(
                self,
                "词级SRT转换完成",
                f"批量转换完成，共 {len(file_paths)} 个文件。\n{summary_text}",
            )
        finally:
            self.reset_ui_after_task()

    def set_ui_enabled(self, enabled: bool):
        """启用或禁用UI控件以防止在处理期间进行交互。"""
        self.start_button.setVisible(enabled)
        self.cancel_button.setVisible(not enabled)
        self.start_button.setEnabled(enabled and bool(self.selected_file_paths))
        self.select_button.setEnabled(enabled)
        self.word_srt_button.setEnabled(enabled)
        self.lang_combo.setEnabled(enabled)
        self.audio_events_checkbox.setEnabled(enabled)
        self.async_settings_button.setEnabled(enabled)
        self.settings_button.setEnabled(enabled)
        self.setAcceptDrops(enabled)

    def reset_ui_after_task(self):
        """任务完成后重置UI到初始状态。"""
        self.set_ui_enabled(True)
        self.segmented_progress_bar.setVisible(False)
        self.progress_label.setVisible(False)
        self.set_files([])

    # --- 核心处理流程 ---
    def start_process(self):
        """开始处理选定的文件。"""
        if not self.selected_file_paths:
            QMessageBox.warning(self, "警告", "请先选择至少一个文件！")
            return

        key_entries = list(self.settings.get("elevenlabs_api_keys", []))
        self.batch_controller.start(
            self.selected_file_paths,
            key_entries,
            on_keys_updated=self._on_api_keys_updated,
        )
        self.temp_audio_file = None
        self.upload_complete_logged = False

        if self.batch_controller.is_batch_mode:
            self.log_area.append("=" * 50)
            self.log_area.append(
                f"开始批量处理，共 {len(self.batch_controller.queue)} 个文件。"
            )

        self.set_ui_enabled(False)
        self._process_next_batch_file()

    def _process_next_batch_file(self):
        """处理待处理队列中的下一个文件。"""
        file_path = self.batch_controller.advance()

        if not file_path:
            self._finish_batch_processing()
            return

        display_name = os.path.basename(file_path)

        if self.batch_controller.is_batch_mode:
            self.log_area.append("=" * 50)
            self.log_area.append(
                f"正在处理第 {self.batch_controller.current_index + 1}/{len(self.batch_controller.queue)} 个文件: {display_name}"
            )
            self.file_drop_label.setText(
                f"批量处理中 ({self.batch_controller.current_index + 1}/{len(self.batch_controller.queue)}):\n{display_name}"
            )
        else:
            self.log_area.clear()
            self.log_area.append("=" * 50)
            self.log_area.append(f"开始处理文件: {display_name}")
            self.file_drop_label.setText(f"已选择:\n{display_name}")

        self.file_drop_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        # 重置进度显示
        self.segmented_progress_bar.reset()
        self.segmented_progress_bar.setVisible(False)
        self.progress_label.setVisible(False)
        self.progress_label.setText("")
        self.upload_complete_logged = False
        self.temp_audio_file = None

        _, ext = os.path.splitext(file_path)
        if ext.lower() == ".json":
            self._process_json_file_directly(
                file_path, from_batch=self.batch_controller.is_batch_mode
            )
        else:
            self._begin_media_processing(file_path)

    def _begin_media_processing(self, source_file: str):
        """准备并启动音视频文件的处理流程。"""
        self.segmented_progress_bar.setVisible(True)
        self.segmented_progress_bar.set_single_file_mode(source_file)
        self.progress_label.setText("准备中...")
        self.progress_label.setVisible(True)

        file_to_process = source_file
        _, ext = os.path.splitext(source_file)

        video_extensions = [".mp4", ".mkv", ".mov", ".avi", ".flv", ".webm"]
        if ext.lower() in video_extensions:
            if self.ffmpeg_available:
                self.log_area.append("检测到视频文件，正在分析音频流...")

                media_info = get_media_info(source_file, self.log_area.append)
                codec = media_info.get("codec") if media_info else None

                if not codec:
                    error_msg = "无法检测到视频中的音频编码，无法继续提取。"
                    if self.batch_controller.is_batch_mode:
                        self.log_area.append(f"\n❌ {error_msg}")
                        self._record_batch_result("error", error_msg)
                        self._finalize_current_batch_step()
                    else:
                        self.on_task_error(error_msg)
                        self.set_ui_enabled(True)
                        self.segmented_progress_bar.setVisible(False)
                        self.progress_label.setVisible(False)
                        self.progress_label.setText("")
                    return

                extension = CODEC_EXTENSION_MAP.get(codec, DEFAULT_AUDIO_EXTENSION)
                self.log_area.append(
                    f"检测到音频编码: {codec}。将使用 '{extension}' 容器进行提取。"
                )

                base_name, _ = os.path.splitext(os.path.basename(source_file))
                temp_audio_path = os.path.join(
                    os.path.dirname(source_file), f"temp_audio_{base_name}{extension}"
                )

                self.log_area.append("正在提取音频...")
                if not extract_audio(
                    source_file, temp_audio_path, self.log_area.append
                ):
                    error_msg = "音频提取失败。"
                    if self.batch_controller.is_batch_mode:
                        self.log_area.append(f"\n❌ {error_msg}")
                        self._record_batch_result("error", error_msg)
                        self._finalize_current_batch_step()
                    else:
                        self.on_task_error(error_msg)
                        self.set_ui_enabled(True)
                        self.segmented_progress_bar.setVisible(False)
                        self.progress_label.setVisible(False)
                        self.progress_label.setText("")
                    return

                self.temp_audio_file = temp_audio_path
                file_to_process = temp_audio_path
            else:
                warning_msg = "检测到视频文件但未找到 FFmpeg。\n将尝试直接上传原始文件，但这可能失败。"
                if not self.batch_controller.is_batch_mode:
                    QMessageBox.warning(self, "功能限制", warning_msg)
                self.log_area.append("⚠️ 未找到 FFmpeg，尝试直接上传视频文件。")

        self._execute_transcription_task(file_to_process, source_file)

    def _process_json_file_directly(self, json_path: str, from_batch: bool = False):
        """直接从JSON文件生成SRT，不进行API调用。"""
        self.set_ui_enabled(False)
        if not from_batch:
            self.log_area.clear()
        self.log_area.append("=" * 50)
        self.log_area.append("检测到JSON文件，直接生成SRT...")

        success = False
        message = ""

        try:
            with open(json_path, "r", encoding="utf-8") as f:
                json_data = json.load(f)

            srt_data = create_srt_from_json(
                json_data,
                max_subtitle_duration=self.max_subtitle_duration,
                subtitle_settings=self.settings,
            )
            if not srt_data and not json_data.get("words"):
                raise ValueError("JSON文件可能为空或不包含'words'数据。")

            output_srt_path = os.path.splitext(json_path)[0] + ".srt"
            with open(output_srt_path, "w", encoding="utf-8") as f:
                f.write(srt_data)

            message = f"SRT字幕文件已保存到:\n{output_srt_path}"
            self.log_area.append(message)
            if not from_batch:
                QMessageBox.information(self, "成功", "JSON文件处理成功！")
            success = True
        except Exception as e:
            message = f"处理JSON文件时出错: {e}"
            if from_batch:
                self.log_area.append(f"\n❌ {message}")
            else:
                self.on_task_error(message)
        finally:
            if from_batch:
                status = "success" if success else "error"
                self._record_batch_result(status, message)
                self._finalize_current_batch_step()
            else:
                self.reset_ui_after_task()

    def _record_batch_result(self, status: str, message: str):
        """记录当前文件的批量处理结果。"""
        self.batch_controller.record_result(status, message)

    def _finalize_current_batch_step(self):
        """在当前文件处理完成后调度下一步。"""
        if not self.batch_controller.is_batch_mode:
            self.reset_ui_after_task()
            return

        if self.batch_controller.cancelled:
            self._finish_batch_processing()
            return

        if self.batch_controller.has_more_files():
            QTimer.singleShot(400, self._process_next_batch_file)
        else:
            self._finish_batch_processing()

    def _finish_batch_processing(self):
        """结束批量处理并输出总结。"""
        if not self.batch_controller.is_batch_mode:
            return

        summary = self.batch_controller.build_summary()

        self.log_area.append("\n" + "=" * 50)
        self.log_area.append(
            f"批量处理完成（共 {summary.total} 个文件）：{summary.summary_text}"
        )

        if summary.error_items:
            self.log_area.append("失败列表：")
            for item in summary.error_items:
                self.log_area.append(
                    f" - {os.path.basename(item.file)}: {item.message}"
                )

        if summary.cancelled_items:
            self.log_area.append("已取消：")
            for item in summary.cancelled_items:
                self.log_area.append(
                    f" - {os.path.basename(item.file)}: {item.message}"
                )

        if summary.remaining:
            self.log_area.append(f"尚有 {summary.remaining} 个文件未处理。")

        title = "批量处理完成" if not self.batch_controller.cancelled else "批量处理已取消"
        QMessageBox.information(
            self,
            title,
            f"批量处理完成，共 {summary.total} 个文件。\n{summary.summary_text}",
        )

        self.batch_controller.reset()
        self.reset_ui_after_task()

    def _execute_transcription_task(
        self,
        file_to_process: str,
        original_file: str,
        restore_state: Optional[WorkerState] = None,
    ):
        """创建并启动后台Worker线程来执行转录任务。"""
        if self.task_thread and self.task_thread.isRunning():
            QMessageBox.warning(self, "提示", "一个任务已经在运行中。")
            return

        # 只在非重试模式下设置UI状态（重试时已在 _setup_retry_ui 中设置）
        if not restore_state:
            self.upload_complete_logged = False
            self.set_ui_enabled(False)
            self.log_area.append("开始执行转录任务...")
        else:
            # 重试模式下，只重置上传完成标志（UI状态已在 _setup_retry_ui 中设置）
            self.upload_complete_logged = False

        self.task_thread = QThread()
        shared_api_key_manager = (
            self.batch_controller.api_key_manager
            if self.batch_controller.is_batch_mode
            else None
        )

        standalone_api_key_manager = None
        if not shared_api_key_manager:
            standalone_api_key_manager = APIKeyManager(
                key_entries=self.settings.get("elevenlabs_api_keys", []),
                on_keys_updated=self._on_api_keys_updated,
            )

        worker = Worker(
            file_path=file_to_process,
            language_code=LANGUAGES.get(self.lang_combo.currentText(), "auto"),
            tag_audio_events=self.audio_events_checkbox.isChecked(),
            original_file_path=original_file,
            max_subtitle_duration=self.max_subtitle_duration,
            split_duration_min=self.split_duration_min,
            ffmpeg_available=self.ffmpeg_available,
            restore_state=restore_state,
            subtitle_settings=self.settings,
            enable_async_processing=self.settings.get("enable_async_processing", True),
            max_concurrent_chunks=self.settings.get("max_concurrent_chunks", 3),
            max_retries=self.settings.get("max_retries", 3),
            api_rate_limit_per_minute=self.settings.get(
                "api_rate_limit_per_minute", 30
            ),
            api_key=self.settings.get("elevenlabs_api_key", ""),
            api_key_manager=shared_api_key_manager or standalone_api_key_manager,
        )
        self.active_worker = worker
        worker.moveToThread(self.task_thread)

        worker.finished.connect(self.on_task_finished)
        worker.error.connect(self.on_task_error)
        worker.log_message.connect(self.log_area.append)
        worker.progress_updated.connect(self.update_progress)
        worker.chunk_progress.connect(self.update_chunk_progress)
        worker.chunks_ready.connect(self.on_chunks_ready)

        self.task_thread.finished.connect(self._handle_task_completion)
        self.task_thread.started.connect(worker.run)

        self.task_thread.start()

    def cancel_process(self):
        """请求取消当前正在运行的任务。"""
        self.log_area.append("\n正在请求取消任务...")
        self.retry_controller.clear()

        if self.batch_controller.is_batch_mode:
            self.batch_controller.mark_cancelled()
            self.log_area.append("批量模式：后续文件将被跳过。")

        # 取消时清理临时文件
        self._cleanup_temp_audio_file()

        if self.active_worker:
            self.active_worker.request_cancellation()

    # --- 信号槽函数 ---
    def on_task_finished(self, message: str):
        """任务成功完成时的处理。"""
        display_name = (
            os.path.basename(self.batch_controller.current_file)
            if self.batch_controller.current_file
            else ""
        )

        if self.batch_controller.is_batch_mode:
            if display_name:
                self.log_area.append(f"\n✅ {display_name}: {message}")
            else:
                self.log_area.append(f"\n✅ {message}")
            self._record_batch_result("success", message)
        else:
            QMessageBox.information(self, "成功", message)
            self.log_area.append(f"\n✅ {message}")

        self.retry_controller.clear()
        self._cleanup_temp_audio_file()

        if self.task_thread:
            self.task_thread.quit()

    def on_task_error(self, message: str):
        """任务失败时的处理，提供重试选项。"""
        display_name = (
            os.path.basename(self.batch_controller.current_file)
            if self.batch_controller.current_file
            else ""
        )
        is_cancelled = "用户取消" in message or "cancelled" in message.lower()

        if self.batch_controller.is_batch_mode:
            status = "cancelled" if is_cancelled else "error"
            if display_name:
                self.log_area.append(f"\n❌ {display_name}: {message}")
            else:
                self.log_area.append(f"\n❌ 任务失败: {message}")
            if status == "cancelled":
                self.batch_controller.mark_cancelled()
            self.retry_controller.clear()
            self._record_batch_result(status, message)
            # 批量模式下不保留临时文件
            self._cleanup_temp_audio_file()
        else:
            self.log_area.append(f"\n❌ 任务失败: {message}")

            if is_cancelled:
                self.retry_controller.clear()
            else:
                msg_box = QMessageBox(self)
                msg_box.setIcon(QMessageBox.Icon.Critical)
                msg_box.setWindowTitle("错误")
                msg_box.setText("任务执行失败。")
                msg_box.setInformativeText(message)
                retry_button = msg_box.addButton(
                    "重试", QMessageBox.ButtonRole.AcceptRole
                )
                msg_box.addButton("关闭", QMessageBox.ButtonRole.RejectRole)

                msg_box.exec()

                if msg_box.clickedButton() == retry_button:
                    if self.active_worker:
                        self.retry_controller.set(self.active_worker.get_retry_state())
                else:
                    self.retry_controller.clear()

        if self.task_thread:
            self.task_thread.quit()

    def update_progress(self, bytes_sent: int, total_bytes: int):
        """更新上传进度条。"""
        if self.active_worker and self.active_worker.total_chunks > 1:
            chunk_index = getattr(self.active_worker, "current_chunk_index", 0)
            self.segmented_progress_bar.update_segment_progress(
                chunk_index, bytes_sent, total_bytes
            )

            # 多片段模式：不显示重复的文字进度，分段进度条已经提供了可视化信息
            # 只在上传完成时更新状态
            if (
                not self.upload_complete_logged
                and bytes_sent >= total_bytes
                and total_bytes > 0
            ):
                self.upload_complete_logged = True
                self.progress_label.setText("上传完成，正在处理...")
        else:
            # 单文件模式：使用兼容的进度更新
            self.segmented_progress_bar.update_single_progress(bytes_sent, total_bytes)

            # 单文件模式：显示简洁的状态信息
            if (
                not self.upload_complete_logged
                and bytes_sent >= total_bytes
                and total_bytes > 0
            ):
                self.upload_complete_logged = True
                self.progress_label.setText("上传完成，正在处理...")
            elif not self.upload_complete_logged:
                # 只有在上传未完成时才显示"正在上传..."
                self.progress_label.setText("正在上传...")
            # 如果已经完成上传，保持"上传完成，正在处理..."状态

    def update_chunk_progress(self, chunk_index: int, status: str, message: str):
        """更新片段处理进度。"""
        self.segmented_progress_bar.update_chunk_status(chunk_index, status)
        if message:
            self.log_area.append(message)

    def on_chunks_ready(self, chunk_paths: List[str]):
        """当音频切分完成，设置分段进度条。"""
        self.segmented_progress_bar.set_segments(chunk_paths)
        self.log_area.append(f"分段进度条已设置，共 {len(chunk_paths)} 个片段")

    def _handle_task_completion(self):
        """处理任务完成后的清理工作。"""
        if (
            not self.retry_controller.has_pending()
            and self.temp_audio_file
            and os.path.exists(self.temp_audio_file)
        ):
            try:
                os.remove(self.temp_audio_file)
                self.log_area.append(
                    f"已清理临时文件: {os.path.basename(self.temp_audio_file)}"
                )
                self.temp_audio_file = None
            except OSError as e:
                self.log_area.append(f"清理临时文件失败: {e}")

        if self.retry_controller.has_pending():
            QTimer.singleShot(1000, self._execute_retry)
        else:
            if self.batch_controller.is_batch_mode:
                self._finalize_current_batch_step()
            else:
                self.reset_ui_after_task()

        thread = self.task_thread
        self.task_thread = None
        self.active_worker = None
        if thread:
            thread.deleteLater()


    def _execute_retry(self):
        """执行重试逻辑。"""
        restore_state = self.retry_controller.pop()
        if restore_state:
            self.log_area.append("\n🔄 正在重试...")

            self._setup_retry_ui(restore_state)

            file_to_process = restore_state.file_path
            original_file = restore_state.original_file_path

            extracted_audio = restore_state.extracted_audio_file
            if restore_state.was_single_file_mode and isinstance(extracted_audio, str):
                if os.path.exists(extracted_audio):
                    file_to_process = extracted_audio
                    self.log_area.append(
                        f"重试时使用已提取的音频文件: {os.path.basename(extracted_audio)}"
                    )
                else:
                    self.log_area.append("提取的音频文件不存在，将重新提取...")

            self._execute_transcription_task(
                file_to_process, original_file, restore_state
            )

    def _setup_retry_ui(self, restore_state: WorkerState):
        """设置重试时的UI状态"""
        # 禁用UI控件
        self.set_ui_enabled(False)

        # 显示进度条和标签
        self.segmented_progress_bar.setVisible(True)
        self.progress_label.setVisible(True)
        self.progress_label.setText("重试中...")

        # 重置上传完成标志
        self.upload_complete_logged = False

        if restore_state.was_single_file_mode:
            file_path = restore_state.extracted_audio_file or restore_state.file_path
            self.segmented_progress_bar.set_single_file_mode(file_path)
            self.log_area.append("重试：设置单文件进度条模式")
        else:
            temp_chunks = restore_state.temp_chunks
            if temp_chunks:
                self.segmented_progress_bar.set_segments(temp_chunks)
                self.log_area.append(
                    f"重试：设置多片段进度条模式，共 {len(temp_chunks)} 个片段"
                )

    def _cleanup_temp_audio_file(self):
        """清理临时音频文件。"""
        if self.temp_audio_file and os.path.exists(self.temp_audio_file):
            try:
                os.remove(self.temp_audio_file)
                self.log_area.append(
                    f"已清理临时文件: {os.path.basename(self.temp_audio_file)}"
                )
            except OSError as e:
                self.log_area.append(f"清理临时文件失败: {e}")
            finally:
                self.temp_audio_file = None

    # --- 拖放功能 ---
    def dragEnterEvent(self, event):
        """处理拖拽进入事件。"""
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):
        """处理文件拖放事件。"""
        urls = event.mimeData().urls()
        if not urls:
            return

        file_paths: List[str] = []
        for url in urls:
            local_path = url.toLocalFile()
            if local_path and os.path.isfile(local_path):
                file_paths.append(local_path)

        if file_paths:
            self.set_files(file_paths)
