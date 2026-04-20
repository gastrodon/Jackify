"""
Third Party Tools screen.

Lists independently-managed tools with install status, version info,
and Install / Update / Downgrade / Uninstall actions per tool.
Version checks run in a background thread so the screen loads instantly.
"""

import logging
from typing import Dict, Optional

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QPushButton,
    QScrollArea, QSizePolicy, QVBoxLayout, QWidget,
)

from jackify.backend.services.tool_registry import TOOL_DEFINITIONS, ToolRegistry, ToolStatus
from jackify.frontends.gui.mixins.thread_lifecycle_mixin import ThreadLifecycleMixin
from jackify.frontends.gui.services.message_service import MessageService
from jackify.frontends.gui.shared_theme import JACKIFY_COLOR_BLUE
from jackify.frontends.gui.utils import set_responsive_minimum

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Colours
# ---------------------------------------------------------------------------
_BTN_INSTALL  = "#1a5fa8"
_BTN_UPDATE   = "#2a6e2a"
_BTN_DOWNGRADE = "#7a5a00"
_BTN_UNINSTALL = "#6b2020"
_BTN_DISABLED = "#333"

_BADGE_NOT_INSTALLED  = ("#555", "#ccc")   # bg, fg
_BADGE_UP_TO_DATE     = ("#1e4d1e", "#8fdc8f")
_BADGE_UPDATE_AVAIL   = ("#5a3d00", "#f0c040")
_BADGE_CHECKING       = ("#333", "#888")


def _btn_style(colour: str, disabled: bool = False) -> str:
    bg = _BTN_DISABLED if disabled else colour
    return f"""
        QPushButton {{
            background-color: {bg};
            color: {'#666' if disabled else 'white'};
            border: none; border-radius: 4px;
            font-size: 11px; font-weight: bold;
            padding: 4px 8px;
        }}
        QPushButton:hover {{ background-color: {'#444' if disabled else bg}; }}
        QPushButton:pressed {{ background-color: {bg}; }}
    """


# ---------------------------------------------------------------------------
# Background version-check thread
# ---------------------------------------------------------------------------

class _VersionCheckThread(QThread):
    version_ready = Signal(str, str)   # tool_id, latest_version_tag

    def run(self):
        registry = ToolRegistry()
        for defn in TOOL_DEFINITIONS:
            try:
                tag = registry.check_latest_version(defn.tool_id)
                if tag:
                    self.version_ready.emit(defn.tool_id, tag)
            except Exception as e:
                logger.debug("Version check failed for %s: %s", defn.tool_id, e)


# ---------------------------------------------------------------------------
# Background install/update/downgrade/uninstall thread
# ---------------------------------------------------------------------------

class _ToolActionThread(QThread):
    finished_signal = Signal(str, bool, str)   # tool_id, success, message

    def __init__(self, tool_id: str, action: str):
        super().__init__()
        self._tool_id = tool_id
        self._action = action

    def run(self):
        registry = ToolRegistry()
        try:
            if self._action == "install":
                ok, msg = registry.install(self._tool_id)
            elif self._action == "update":
                ok, msg = registry.update(self._tool_id)
            elif self._action == "downgrade":
                ok, msg = registry.downgrade(self._tool_id)
            elif self._action == "uninstall":
                ok, msg = registry.uninstall(self._tool_id)
            else:
                ok, msg = False, f"Unknown action: {self._action}"
        except Exception as e:
            ok, msg = False, str(e)
        self.finished_signal.emit(self._tool_id, ok, msg)


# ---------------------------------------------------------------------------
# Per-tool card widget
# ---------------------------------------------------------------------------

class _ToolCard(QFrame):
    action_requested = Signal(str, str)   # tool_id, action

    def __init__(self, status: ToolStatus, parent=None):
        super().__init__(parent)
        self._tool_id = status.definition.tool_id
        self._status = status
        self._busy = False

        self.setFrameShape(QFrame.StyledPanel)
        self.setStyleSheet("""
            QFrame {
                background-color: #2a2a2a;
                border: 1px solid #3a3a3a;
                border-radius: 6px;
            }
        """)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        outer = QHBoxLayout()
        outer.setContentsMargins(14, 10, 14, 10)
        outer.setSpacing(12)

        # --- Left: name + description ---
        info_col = QVBoxLayout()
        info_col.setSpacing(2)

        tier_tag = " [required]" if status.definition.tier == 1 else ""
        name_label = QLabel(f"<b>{status.definition.display_name}</b>{tier_tag}")
        name_label.setStyleSheet("color: #e0e0e0; font-size: 13px; background: transparent; border: none;")
        info_col.addWidget(name_label)

        desc_label = QLabel(status.definition.description)
        desc_label.setWordWrap(True)
        desc_label.setStyleSheet("color: #888; font-size: 11px; background: transparent; border: none;")
        info_col.addWidget(desc_label)

        info_widget = QWidget()
        info_widget.setLayout(info_col)
        info_widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        info_widget.setStyleSheet("background: transparent; border: none;")
        outer.addWidget(info_widget, stretch=3)

        # --- Centre: status badge + version ---
        centre_col = QVBoxLayout()
        centre_col.setSpacing(4)
        centre_col.setAlignment(Qt.AlignCenter)

        self._badge = QLabel()
        self._badge.setAlignment(Qt.AlignCenter)
        self._badge.setFixedWidth(130)
        self._badge.setStyleSheet("border-radius: 3px; padding: 2px 6px; font-size: 11px; font-weight: bold;")
        centre_col.addWidget(self._badge, alignment=Qt.AlignCenter)

        self._version_label = QLabel()
        self._version_label.setAlignment(Qt.AlignCenter)
        self._version_label.setStyleSheet("color: #777; font-size: 10px; background: transparent; border: none;")
        centre_col.addWidget(self._version_label, alignment=Qt.AlignCenter)

        centre_widget = QWidget()
        centre_widget.setLayout(centre_col)
        centre_widget.setFixedWidth(150)
        centre_widget.setStyleSheet("background: transparent; border: none;")
        outer.addWidget(centre_widget)

        # --- Right: action buttons ---
        btn_col = QVBoxLayout()
        btn_col.setSpacing(4)
        btn_col.setAlignment(Qt.AlignCenter)

        self._btn_primary = QPushButton()
        self._btn_primary.setFixedWidth(90)
        self._btn_primary.clicked.connect(self._on_primary)
        btn_col.addWidget(self._btn_primary)

        self._btn_downgrade = QPushButton("Downgrade")
        self._btn_downgrade.setFixedWidth(90)
        self._btn_downgrade.clicked.connect(lambda: self.action_requested.emit(self._tool_id, "downgrade"))
        btn_col.addWidget(self._btn_downgrade)

        self._btn_uninstall = QPushButton("Uninstall")
        self._btn_uninstall.setFixedWidth(90)
        self._btn_uninstall.clicked.connect(self._on_uninstall)
        btn_col.addWidget(self._btn_uninstall)

        btn_widget = QWidget()
        btn_widget.setLayout(btn_col)
        btn_widget.setFixedWidth(110)
        btn_widget.setStyleSheet("background: transparent; border: none;")
        outer.addWidget(btn_widget)

        self.setLayout(outer)
        self._refresh_ui(status)

    # ------------------------------------------------------------------

    def _refresh_ui(self, status: ToolStatus):
        self._status = status
        installed = status.installed
        update_avail = status.update_available
        can_downgrade = status.can_downgrade
        can_uninstall = status.definition.can_uninstall

        # Badge
        if not installed:
            bg, fg = _BADGE_NOT_INSTALLED
            badge_text = "Not Installed"
        elif update_avail:
            bg, fg = _BADGE_UPDATE_AVAIL
            badge_text = "Update Available"
        else:
            bg, fg = _BADGE_UP_TO_DATE
            badge_text = "Installed"
        self._badge.setText(badge_text)
        self._badge.setStyleSheet(
            f"background-color: {bg}; color: {fg}; border-radius: 3px; "
            f"padding: 2px 6px; font-size: 11px; font-weight: bold; border: none;"
        )

        # Version line
        installed_ver = status.installed_version or "-"
        latest_ver = status.latest_version or "checking..."
        if installed:
            self._version_label.setText(f"Installed: {installed_ver}\nLatest: {latest_ver}")
        else:
            self._version_label.setText(f"Latest: {latest_ver}")

        # Primary button
        if not installed:
            self._btn_primary.setText("Install")
            self._btn_primary.setStyleSheet(_btn_style(_BTN_INSTALL))
            self._btn_primary.setEnabled(True)
        elif update_avail:
            self._btn_primary.setText("Update")
            self._btn_primary.setStyleSheet(_btn_style(_BTN_UPDATE))
            self._btn_primary.setEnabled(True)
        else:
            self._btn_primary.setText("Reinstall")
            self._btn_primary.setStyleSheet(_btn_style(_BTN_INSTALL))
            self._btn_primary.setEnabled(True)

        # Downgrade button
        self._btn_downgrade.setStyleSheet(_btn_style(_BTN_DOWNGRADE, disabled=not can_downgrade))
        self._btn_downgrade.setEnabled(can_downgrade and not self._busy)

        # Uninstall button
        self._btn_uninstall.setVisible(can_uninstall)
        if can_uninstall:
            self._btn_uninstall.setStyleSheet(_btn_style(_BTN_UNINSTALL, disabled=not installed))
            self._btn_uninstall.setEnabled(installed and not self._busy)

        if self._busy:
            self._btn_primary.setEnabled(False)
            self._btn_primary.setStyleSheet(_btn_style(_BTN_DISABLED, disabled=True))

    def set_latest_version(self, tag: str):
        self._status.latest_version = tag
        if self._status.installed and self._status.installed_version:
            installed = self._status.installed_version.lstrip("v")
            latest = tag.lstrip("v")
            self._status.update_available = latest != installed
        self._refresh_ui(self._status)

    def set_busy(self, busy: bool, label: Optional[str] = None):
        self._busy = busy
        if busy and label:
            self._btn_primary.setText(label)
        self._refresh_ui(self._status)

    def mark_installed(self, version: str):
        self._status.installed = True
        self._status.installed_version = version
        self._status.update_available = False
        self._busy = False
        self._refresh_ui(self._status)

    def mark_uninstalled(self):
        self._status.installed = False
        self._status.installed_version = None
        self._status.update_available = False
        self._busy = False
        self._refresh_ui(self._status)

    # ------------------------------------------------------------------

    def _on_primary(self):
        if not self._status.installed:
            self.action_requested.emit(self._tool_id, "install")
        elif self._status.update_available:
            self.action_requested.emit(self._tool_id, "update")
        else:
            self.action_requested.emit(self._tool_id, "install")

    def _on_uninstall(self):
        confirmed = MessageService.question(
            self,
            "Uninstall Tool",
            f"Uninstall {self._status.definition.display_name}?\n\nThis will delete the installed files.",
        )
        if confirmed:
            self.action_requested.emit(self._tool_id, "uninstall")


# ---------------------------------------------------------------------------
# Main screen
# ---------------------------------------------------------------------------

class ThirdPartyToolsScreen(ThreadLifecycleMixin, QWidget):
    """Third Party Tools management screen."""

    def __init__(self, stacked_widget=None, main_menu_index: int = 0, parent=None):
        super().__init__(parent)
        self.stacked_widget = stacked_widget
        self.main_menu_index = main_menu_index

        self._cards: Dict[str, _ToolCard] = {}
        self._action_thread: Optional[_ToolActionThread] = None
        self._version_thread: Optional[_VersionCheckThread] = None

        self._setup_ui()

    def _setup_ui(self):
        root = QVBoxLayout()
        root.setContentsMargins(30, 24, 30, 24)
        root.setSpacing(0)
        self.setLayout(root)

        # Header
        title = QLabel("<b>Third Party Tools</b>")
        title.setStyleSheet(f"font-size: 20px; color: {JACKIFY_COLOR_BLUE};")
        title.setAlignment(Qt.AlignHCenter)
        root.addWidget(title)

        root.addSpacing(6)

        desc = QLabel(
            "Install and manage independently-updated tools used by Jackify workflows or run via MO2.\n"
            "Tools marked [required] are needed by existing Jackify workflows."
        )
        desc.setWordWrap(True)
        desc.setStyleSheet("color: #aaa; font-size: 12px;")
        desc.setAlignment(Qt.AlignHCenter)
        root.addWidget(desc)

        root.addSpacing(10)

        sep = QLabel()
        sep.setFixedHeight(1)
        sep.setStyleSheet("background: #444;")
        root.addWidget(sep)

        root.addSpacing(12)

        # Scrollable tool list
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        list_widget = QWidget()
        list_widget.setStyleSheet("background: transparent;")
        self._list_layout = QVBoxLayout()
        self._list_layout.setContentsMargins(0, 0, 0, 0)
        self._list_layout.setSpacing(8)
        list_widget.setLayout(self._list_layout)

        registry = ToolRegistry()
        for status in registry.get_all_statuses():
            card = _ToolCard(status)
            card.action_requested.connect(self._on_action)
            self._cards[status.definition.tool_id] = card
            self._list_layout.addWidget(card)

        self._list_layout.addStretch()
        scroll.setWidget(list_widget)
        root.addWidget(scroll, stretch=1)

        root.addSpacing(12)

        # Back button
        back_row = QHBoxLayout()
        back_row.addStretch()
        back_btn = QPushButton("Back to Main Menu")
        back_btn.setFixedSize(160, 34)
        back_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: #4a5568; color: white;
                border: none; border-radius: 5px;
                font-size: 12px; font-weight: bold;
            }}
            QPushButton:hover {{ background-color: #5a6578; }}
            QPushButton:pressed {{ background-color: {JACKIFY_COLOR_BLUE}; }}
        """)
        back_btn.clicked.connect(self._go_back)
        back_row.addWidget(back_btn)
        back_row.addStretch()
        root.addLayout(back_row)

    # ------------------------------------------------------------------
    # Version check on show
    # ------------------------------------------------------------------

    def showEvent(self, event):
        super().showEvent(event)
        try:
            main_window = self.window()
            if main_window:
                set_responsive_minimum(main_window, min_width=960, min_height=520)
        except Exception:
            pass
        self._start_version_check()

    def _start_version_check(self):
        if self._version_thread and self._version_thread.isRunning():
            return
        self._version_thread = _VersionCheckThread()
        self._version_thread.version_ready.connect(self._on_version_ready)
        self._version_thread.start()

    def _on_version_ready(self, tool_id: str, tag: str):
        card = self._cards.get(tool_id)
        if card:
            card.set_latest_version(tag)

    # ------------------------------------------------------------------
    # Action dispatch
    # ------------------------------------------------------------------

    def _on_action(self, tool_id: str, action: str):
        if self._action_thread and self._action_thread.isRunning():
            MessageService.information(self, "Busy", "Another operation is already running. Please wait.")
            return

        card = self._cards.get(tool_id)
        if card:
            label_map = {"install": "Installing...", "update": "Updating...",
                         "downgrade": "Downgrading...", "uninstall": "Removing..."}
            card.set_busy(True, label_map.get(action, "Working..."))

        self._action_thread = _ToolActionThread(tool_id, action)
        self._action_thread.finished_signal.connect(self._on_action_finished)
        self._action_thread.start()

    def _on_action_finished(self, tool_id: str, success: bool, message: str):
        self._action_thread = None

        card = self._cards.get(tool_id)
        if success:
            registry = ToolRegistry()
            status = registry.get_status(tool_id)
            if status and status.installed and card:
                card.mark_installed(status.installed_version or "")
                if status.latest_version:
                    card.set_latest_version(status.latest_version)
            elif card:
                card.mark_uninstalled()
            MessageService.information(self, "Done", message)
        else:
            if card:
                card.set_busy(False)
            MessageService.warning(self, "Failed", message)

    # ------------------------------------------------------------------

    def _go_back(self):
        if self.stacked_widget:
            self.stacked_widget.setCurrentIndex(self.main_menu_index)

    def cleanup_processes(self):
        self._park_all_threads()
