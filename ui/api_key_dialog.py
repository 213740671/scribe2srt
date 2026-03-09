# -*- coding: utf-8 -*-

import webbrowser

import requests
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from core.config import update_settings_file
from .widgets import CustomCheckBox


class ApiKeyDialog(QDialog):
    def __init__(self, current_api_keys: list[dict] | None = None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("ElevenLabs API 设置")
        self.setMinimumWidth(900)
        self.setMinimumHeight(560)

        self.api_key_entries: list[dict[str, str | bool]] = [
            self._normalize_entry(dict(entry)) for entry in (current_api_keys or [])
        ]
        self.setup_ui()
        self.refresh_key_table()

    def setup_ui(self):
        layout = QVBoxLayout()
        layout.setSpacing(15)
        layout.setContentsMargins(20, 20, 20, 20)

        api_key_group = QGroupBox("API Key 管理")
        api_key_layout = QVBoxLayout()

        info_label = QLabel(
            "保留所有 Key；可手动切换激活/未激活。额度不足时会自动设为未激活，需手动重新激活。"
        )
        info_label.setWordWrap(True)
        api_key_layout.addWidget(info_label)

        self.summary_label = QLabel("")
        api_key_layout.addWidget(self.summary_label)

        add_layout = QHBoxLayout()
        self.new_key_input = QLineEdit()
        self.new_key_input.setPlaceholderText("输入新的 ElevenLabs API Key")
        add_layout.addWidget(self.new_key_input)

        self.add_key_btn = QPushButton("添加 Key")
        self.add_key_btn.clicked.connect(self.add_key)
        add_layout.addWidget(self.add_key_btn)
        api_key_layout.addLayout(add_layout)

        self.key_table = QTableWidget(0, 4)
        self.key_table.setHorizontalHeaderLabels(["启用", "状态", "API Key", "操作"])
        header = self.key_table.horizontalHeader()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self.key_table.verticalHeader().setVisible(False)
        self.key_table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        self.key_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        api_key_layout.addWidget(self.key_table)

        batch_button_layout = QHBoxLayout()
        self.activate_all_btn = QPushButton("全部激活")
        self.activate_all_btn.clicked.connect(self.activate_all_keys)
        batch_button_layout.addWidget(self.activate_all_btn)

        self.deactivate_all_btn = QPushButton("全部停用")
        self.deactivate_all_btn.clicked.connect(self.deactivate_all_keys)
        batch_button_layout.addWidget(self.deactivate_all_btn)

        self.reactivate_auto_btn = QPushButton("恢复自动停用")
        self.reactivate_auto_btn.clicked.connect(self.reactivate_auto_disabled_keys)
        batch_button_layout.addWidget(self.reactivate_auto_btn)

        self.verify_btn = QPushButton("验证全部 Key")
        self.verify_btn.clicked.connect(self.verify_api_keys)
        batch_button_layout.addWidget(self.verify_btn)

        self.clear_btn = QPushButton("清空全部")
        self.clear_btn.clicked.connect(self.clear_keys)
        batch_button_layout.addWidget(self.clear_btn)
        batch_button_layout.addStretch()
        api_key_layout.addLayout(batch_button_layout)

        api_key_group.setLayout(api_key_layout)
        layout.addWidget(api_key_group)

        info_group = QGroupBox("说明")
        info_layout = QVBoxLayout()

        info_text = QTextEdit()
        info_text.setReadOnly(True)
        info_text.setHtml(
            """
        <p><b>API Key 状态说明</b></p>
        <ul>
            <li><b>可用</b>：当前为激活状态，转录时允许使用。</li>
            <li><b>手动停用</b>：您手动关闭，程序不会使用。</li>
            <li><b>额度不足自动停用</b>：程序检测到额度耗尽后自动停用，需手动恢复。</li>
        </ul>
        <p>如果没有任何激活的 Key，程序会回退到未认证模式（免费额度）。</p>
        """
        )
        info_text.setMaximumHeight(180)
        info_layout.addWidget(info_text)
        info_group.setLayout(info_layout)
        layout.addWidget(info_group)

        button_layout = QHBoxLayout()
        self.get_key_btn = QPushButton("获取免费 API Key")
        self.get_key_btn.clicked.connect(self.open_get_key_page)
        button_layout.addWidget(self.get_key_btn)

        button_layout.addStretch()

        self.save_btn = QPushButton("保存")
        self.save_btn.clicked.connect(self.save_settings)
        button_layout.addWidget(self.save_btn)

        self.cancel_btn = QPushButton("取消")
        self.cancel_btn.clicked.connect(self.reject)
        button_layout.addWidget(self.cancel_btn)

        layout.addLayout(button_layout)
        self.setLayout(layout)

    def _normalize_entry(self, entry: dict[str, str | bool]) -> dict[str, str | bool]:
        return {
            "key": str(entry.get("key", "")).strip(),
            "active": bool(entry.get("active", True)),
            "inactive_reason": str(entry.get("inactive_reason", "")).strip(),
        }

    def _mask_key(self, key: str) -> str:
        if len(key) <= 12:
            return key
        return f"{key[:6]}...{key[-4:]}"

    def _status_text(self, entry: dict[str, str | bool]) -> str:
        if bool(entry.get("active", True)):
            return "可用"

        reason = str(entry.get("inactive_reason", "")).strip()
        if reason == "quota_exhausted":
            return "额度不足自动停用"
        if reason == "manual":
            return "手动停用"
        return "未激活"

    def _update_summary(self):
        total = len(self.api_key_entries)
        active_count = len([entry for entry in self.api_key_entries if entry.get("active", True)])
        inactive_count = total - active_count
        auto_disabled_count = len(
            [
                entry
                for entry in self.api_key_entries
                if not entry.get("active", True)
                and entry.get("inactive_reason", "") == "quota_exhausted"
            ]
        )
        self.summary_label.setText(
            f"共 {total} 个 Key ｜ 激活 {active_count} ｜ 未激活 {inactive_count} ｜ 自动停用 {auto_disabled_count}"
        )

    def _build_status_checkbox(self, row_index: int, is_active: bool) -> QWidget:
        container = QWidget()
        layout = QHBoxLayout(container)
        layout.setContentsMargins(6, 0, 6, 0)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        checkbox = CustomCheckBox("激活")
        checkbox.setChecked(is_active)
        checkbox.stateChanged.connect(
            lambda state, index=row_index: self.on_key_active_changed(index, state)
        )
        layout.addWidget(checkbox)
        return container

    def _build_action_buttons(self, row_index: int) -> QWidget:
        container = QWidget()
        layout = QHBoxLayout(container)
        layout.setContentsMargins(6, 0, 6, 0)
        layout.setSpacing(6)

        verify_btn = QPushButton("验证")
        verify_btn.clicked.connect(lambda _, index=row_index: self.verify_single_key(index))
        layout.addWidget(verify_btn)

        remove_btn = QPushButton("删除")
        remove_btn.clicked.connect(lambda _, index=row_index: self.remove_key(index))
        layout.addWidget(remove_btn)
        return container

    def refresh_key_table(self):
        self.key_table.setRowCount(len(self.api_key_entries))

        for row_index, entry in enumerate(self.api_key_entries):
            status_widget = self._build_status_checkbox(
                row_index, bool(entry.get("active", True))
            )
            self.key_table.setCellWidget(row_index, 0, status_widget)

            status_item = QTableWidgetItem(self._status_text(entry))
            status_item.setTextAlignment(
                Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignCenter
            )
            self.key_table.setItem(row_index, 1, status_item)

            key_item = QTableWidgetItem(self._mask_key(str(entry.get("key", ""))))
            key_item.setToolTip(str(entry.get("key", "")))
            key_item.setTextAlignment(
                Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft
            )
            self.key_table.setItem(row_index, 2, key_item)

            action_widget = self._build_action_buttons(row_index)
            self.key_table.setCellWidget(row_index, 3, action_widget)

        self._update_summary()
        if self.api_key_entries:
            self.key_table.resizeRowsToContents()

    def add_key(self):
        key = self.new_key_input.text().strip()
        if not key:
            QMessageBox.warning(self, "警告", "请输入 API Key")
            return

        existing_keys = {str(entry.get("key", "")) for entry in self.api_key_entries}
        if key in existing_keys:
            QMessageBox.information(self, "提示", "该 API Key 已存在，将不会重复添加。")
            return

        self.api_key_entries.append(
            {"key": key, "active": True, "inactive_reason": ""}
        )
        self.new_key_input.clear()
        self.refresh_key_table()

    def remove_key(self, index: int):
        if index < 0 or index >= len(self.api_key_entries):
            return
        self.api_key_entries.pop(index)
        self.refresh_key_table()

    def clear_keys(self):
        self.api_key_entries = []
        self.refresh_key_table()

    def activate_all_keys(self):
        auto_disabled_count = len(
            [
                entry
                for entry in self.api_key_entries
                if entry.get("inactive_reason", "") == "quota_exhausted"
            ]
        )
        if auto_disabled_count > 0:
            result = QMessageBox.question(
                self,
                "确认全部激活",
                f"当前有 {auto_disabled_count} 个 Key 是因额度不足被自动停用。\n全部激活会一并恢复它们。是否继续？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if result != QMessageBox.StandardButton.Yes:
                return

        for entry in self.api_key_entries:
            entry["active"] = True
            entry["inactive_reason"] = ""
        self.refresh_key_table()

    def deactivate_all_keys(self):
        for entry in self.api_key_entries:
            entry["active"] = False
            entry["inactive_reason"] = "manual"
        self.refresh_key_table()

    def reactivate_auto_disabled_keys(self):
        restored_count = 0
        for entry in self.api_key_entries:
            if entry.get("inactive_reason", "") == "quota_exhausted":
                entry["active"] = True
                entry["inactive_reason"] = ""
                restored_count += 1
        self.refresh_key_table()
        if restored_count > 0:
            QMessageBox.information(self, "完成", f"已恢复 {restored_count} 个自动停用的 Key。")
        else:
            QMessageBox.information(self, "提示", "当前没有因额度不足而自动停用的 Key。")

    def on_key_active_changed(self, index: int, state: int):
        if index < 0 or index >= len(self.api_key_entries):
            return
        is_active = state == Qt.CheckState.Checked.value
        self.api_key_entries[index]["active"] = is_active
        self.api_key_entries[index]["inactive_reason"] = "" if is_active else "manual"
        self.refresh_key_table()

    def verify_single_key(self, index: int):
        if index < 0 or index >= len(self.api_key_entries):
            return

        entry = self.api_key_entries[index]
        key = str(entry.get("key", ""))
        try:
            response = requests.get(
                "https://api.elevenlabs.io/v1/models",
                headers={"xi-api-key": key},
                timeout=10,
            )
            if response.status_code == 200:
                QMessageBox.information(self, "验证结果", f"Key {index + 1}: 验证通过")
            elif response.status_code == 401:
                QMessageBox.warning(self, "验证结果", f"Key {index + 1}: 无效")
            else:
                QMessageBox.information(
                    self,
                    "验证结果",
                    f"Key {index + 1}: 状态码 {response.status_code}",
                )
        except requests.exceptions.RequestException as e:
            QMessageBox.warning(self, "验证结果", f"Key {index + 1}: 请求失败 - {str(e)}")

    def verify_api_keys(self):
        if not self.api_key_entries:
            QMessageBox.warning(self, "警告", "请先添加 API Key")
            return

        results = []
        for i, entry in enumerate(self.api_key_entries, 1):
            key = str(entry.get("key", ""))
            status_text = self._status_text(entry)
            try:
                response = requests.get(
                    "https://api.elevenlabs.io/v1/models",
                    headers={"xi-api-key": key},
                    timeout=10,
                )

                if response.status_code == 200:
                    results.append(f"Key {i} ({status_text}): 验证通过")
                elif response.status_code == 401:
                    results.append(f"Key {i} ({status_text}): 无效")
                else:
                    results.append(
                        f"Key {i} ({status_text}): 状态码 {response.status_code}"
                    )

            except requests.exceptions.RequestException as e:
                results.append(f"Key {i} ({status_text}): 请求失败 - {str(e)}")

        QMessageBox.information(self, "验证结果", "\n".join(results))

    def open_get_key_page(self):
        webbrowser.open("https://elevenlabs.io/app/settings/api-keys")

    def save_settings(self):
        try:
            normalized_entries = [self._normalize_entry(dict(entry)) for entry in self.api_key_entries]

            def apply_key_updates(settings: dict):
                settings["elevenlabs_api_keys"] = normalized_entries
                settings["elevenlabs_api_key"] = "\n".join(
                    str(entry.get("key", "")) for entry in normalized_entries
                )

            update_settings_file(apply_key_updates)
            QMessageBox.information(self, "成功", "设置已保存")
            self.accept()

        except Exception as e:
            QMessageBox.critical(self, "错误", f"保存设置失败: {str(e)}")

    def get_api_key_entries(self) -> list[dict[str, str | bool]]:
        return [self._normalize_entry(dict(entry)) for entry in self.api_key_entries]
