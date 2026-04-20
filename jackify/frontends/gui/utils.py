"""
GUI Utilities for Jackify Frontend
"""
import os
import re
from typing import Tuple, Optional, List
from PySide6.QtWidgets import QApplication, QWidget, QFileDialog
from PySide6.QtCore import QSize, QPoint, QUrl

ANSI_COLOR_MAP = {
    '30': 'black', '31': 'red', '32': 'green', '33': 'yellow', '34': 'blue', '35': 'magenta', '36': 'cyan', '37': 'white',
    '90': 'gray', '91': 'lightcoral', '92': 'lightgreen', '93': 'khaki', '94': 'lightblue', '95': 'violet', '96': 'lightcyan', '97': 'white'
}
ANSI_RE = re.compile(r'\x1b\[(\d+)(;\d+)?m')

# Pattern to match terminal control codes (cursor movement, line clearing, etc.)
ANSI_CONTROL_RE = re.compile(
    r'\x1b\['  # CSI sequence start
    r'[0-9;]*'  # Parameters
    r'[A-Za-z]'  # Command letter
)

def strip_ansi_control_codes(text):
    """Remove ALL ANSI escape sequences including control codes.

    This is useful for Hoolamike output which uses terminal control codes
    for progress bars that don't render well in QTextEdit.
    """
    return ANSI_CONTROL_RE.sub('', text)

def ansi_to_html(text):
    """Convert ANSI color codes to HTML"""
    result = ''
    last_end = 0
    color = None
    for match in ANSI_RE.finditer(text):
        start, end = match.span()
        code = match.group(1)
        if start > last_end:
            chunk = text[last_end:start]
            if color:
                result += f'<span style="color:{color}">{chunk}</span>'
            else:
                result += chunk
        if code == '0':
            color = None
        elif code in ANSI_COLOR_MAP:
            color = ANSI_COLOR_MAP[code]
        last_end = end
    if last_end < len(text):
        chunk = text[last_end:]
        if color:
            result += f'<span style="color:{color}">{chunk}</span>'
        else:
            result += chunk
    result = result.replace('\n', '<br>')
    return result


def get_screen_geometry(widget: Optional[QWidget] = None) -> Tuple[int, int, int, int]:
    """
    Get available screen geometry for a widget.
    
    Args:
        widget: Widget to get screen for (uses primary screen if None)
        
    Returns:
        Tuple of (x, y, width, height) for available screen geometry
    """
    app = QApplication.instance()
    if not app:
        return (0, 0, 1920, 1080)  # Fallback
    
    if widget:
        screen = None
        window_handle = widget.windowHandle()
        if window_handle and window_handle.screen():
            screen = window_handle.screen()
        else:
            try:
                global_pos = widget.mapToGlobal(widget.rect().center())
            except Exception:
                global_pos = QPoint(0, 0)
            if app:
                screen = app.screenAt(global_pos)
        if not screen and app:
            screen = app.primaryScreen()
    else:
        screen = app.primaryScreen()
    
    if screen:
        geometry = screen.availableGeometry()
        return (geometry.x(), geometry.y(), geometry.width(), geometry.height())
    
    return (0, 0, 1920, 1080)  # Fallback


def calculate_window_size(
    widget: Optional[QWidget] = None,
    width_ratio: float = 0.7,
    height_ratio: float = 0.6,
    min_width: int = 900,
    min_height: int = 500,
    max_width: Optional[int] = None,
    max_height: Optional[int] = None
) -> Tuple[int, int]:
    """
    Calculate appropriate window size based on screen geometry.
    
    Args:
        widget: Widget to calculate size for (uses primary screen if None)
        width_ratio: Fraction of screen width to use (0.0-1.0)
        height_ratio: Fraction of screen height to use (0.0-1.0)
        min_width: Minimum window width
        min_height: Minimum window height
        max_width: Maximum window width (None = no limit)
        max_height: Maximum window height (None = no limit)
        
    Returns:
        Tuple of (width, height)
    """
    _, _, screen_width, screen_height = get_screen_geometry(widget)
    
    # Calculate size based on ratios
    width = int(screen_width * width_ratio)
    height = int(screen_height * height_ratio)
    
    # Apply minimums
    width = max(width, min_width)
    height = max(height, min_height)
    
    # Apply maximums
    if max_width:
        width = min(width, max_width)
    if max_height:
        height = min(height, max_height)
    
    # Ensure we don't exceed screen bounds
    width = min(width, screen_width)
    height = min(height, screen_height)
    
    return (width, height)


def calculate_window_position(
    widget: QWidget,
    window_width: int,
    window_height: int,
    parent: Optional[QWidget] = None
) -> QPoint:
    """
    Calculate appropriate window position (centered on parent or screen).
    
    Args:
        widget: Widget to position
        window_width: Width of window to position
        window_height: Height of window to position
        parent: Parent widget to center on (centers on screen if None)
        
    Returns:
        QPoint with x, y coordinates
    """
    _, _, screen_width, screen_height = get_screen_geometry(widget)
    
    if parent:
        parent_geometry = parent.geometry()
        x = parent_geometry.x() + (parent_geometry.width() - window_width) // 2
        y = parent_geometry.y() + (parent_geometry.height() - window_height) // 2
    else:
        # Center on screen
        x = (screen_width - window_width) // 2
        y = (screen_height - window_height) // 2
    
    # Ensure window stays on screen
    x = max(0, min(x, screen_width - window_width))
    y = max(0, min(y, screen_height - window_height))
    
    return QPoint(x, y)


def set_responsive_minimum(window: Optional[QWidget], min_width: int = 960,
                           min_height: int = 520, margin: int = 32):
    """
    Apply minimum size constraints that respect the current screen bounds.
    
    Args:
        window: Target window
        min_width: Desired minimum width
        min_height: Desired minimum height
        margin: Pixels to subtract from available size to avoid full-screen overlap
    """
    if window is None:
        return
    
    _, _, screen_width, screen_height = get_screen_geometry(window)
    
    width_cap = min_width
    height_cap = min_height
    
    if screen_width:
        available_width = max(640, screen_width - margin)
        available_width = min(available_width, screen_width)
        width_cap = min(min_width, available_width)
    if screen_height:
        available_height = max(520, screen_height - margin)
        available_height = min(available_height, screen_height)
        height_cap = min(min_height, available_height)
    
    window.setMinimumSize(QSize(width_cap, height_cap))

def load_saved_window_size(window: QWidget) -> Optional[Tuple[int, int]]:
    """
    Load saved window size from config if available.
    Only returns sizes that are reasonable (compact menu size, not expanded).
    
    Args:
        window: Window widget (used to validate size against screen)
        
    Returns:
        Tuple of (width, height) if saved size exists and is valid, None otherwise
    """
    try:
        from ...backend.handlers.config_handler import ConfigHandler
        config_handler = ConfigHandler()
        
        saved_width = config_handler.get('window_width')
        saved_height = config_handler.get('window_height')
        
        if saved_width and saved_height:
            # Validate saved size is reasonable (not too small, fits on screen)
            _, _, screen_width, screen_height = get_screen_geometry(window)
            min_width = 1200
            min_height = 500
            max_height = int(screen_height * 0.6)  # Reject sizes larger than 60% of screen (expanded state)
            
            # Ensure saved size is within reasonable bounds (compact menu size)
            # Reject expanded sizes that are too tall
            if (min_width <= saved_width <= screen_width and 
                min_height <= saved_height <= max_height):
                return (saved_width, saved_height)
    except Exception:
        pass
    
    return None


def save_window_size(window: QWidget):
    """
    Save current window size to config.
    
    Args:
        window: Window widget to save size for
    """
    try:
        from ...backend.handlers.config_handler import ConfigHandler
        config_handler = ConfigHandler()
        
        size = window.size()
        config_handler.set('window_width', size.width())
        config_handler.set('window_height', size.height())
        config_handler.save_config()
    except Exception:
        pass


def apply_window_size_and_position(
    window: QWidget,
    width_ratio: float = 0.7,
    height_ratio: float = 0.6,
    min_width: int = 900,
    min_height: int = 500,
    max_width: Optional[int] = None,
    max_height: Optional[int] = None,
    parent: Optional[QWidget] = None,
    preserve_position: bool = False,
    use_saved_size: bool = True
):
    """
    Apply dynamic window sizing and positioning based on screen geometry.
    Optionally uses saved window size if user has manually resized before.
    
    Args:
        window: Window widget to size/position
        width_ratio: Fraction of screen width to use (if no saved size)
        height_ratio: Fraction of screen height to use (if no saved size)
        min_width: Minimum window width
        min_height: Minimum window height
        max_width: Maximum window width (None = no limit)
        max_height: Maximum window height (None = no limit)
        parent: Parent widget to center on (centers on screen if None)
        preserve_position: If True, preserve current size and position (only set minimums)
        use_saved_size: If True, check for saved window size first
    """
    # Set minimum size first
    window.setMinimumSize(QSize(min_width, min_height))
    
    # If preserve_position is True, don't resize - just ensure minimums are set
    if preserve_position:
        # Only ensure current size meets minimums, don't change size
        current_size = window.size()
        if current_size.width() < min_width:
            window.resize(min_width, current_size.height())
        if current_size.height() < min_height:
            window.resize(window.size().width(), min_height)
        return
    
    # Check for saved window size first
    width = None
    height = None
    
    if use_saved_size:
        saved_size = load_saved_window_size(window)
        if saved_size:
            width, height = saved_size
    
    # If no saved size, calculate dynamically
    if width is None or height is None:
        width, height = calculate_window_size(
            window, width_ratio, height_ratio, min_width, min_height, max_width, max_height
        )

    # Calculate and set position
    pos = calculate_window_position(window, width, height, parent)
    window.resize(width, height)
    window.move(pos)


def _get_sidebar_urls() -> List[QUrl]:
    """Return QUrl list for home dir plus any mounted volumes under /run/media/."""
    urls = [QUrl.fromLocalFile(os.path.expanduser("~"))]
    run_media = "/run/media"
    if os.path.isdir(run_media):
        try:
            for entry in os.scandir(run_media):
                if entry.is_dir():
                    # /run/media/<user>/<volume> - add the user subdir and all volumes
                    try:
                        for vol in os.scandir(entry.path):
                            if vol.is_dir():
                                urls.append(QUrl.fromLocalFile(vol.path))
                    except PermissionError:
                        urls.append(QUrl.fromLocalFile(entry.path))
        except PermissionError:
            pass
    return urls


def browse_directory(parent: QWidget, title: str, start_path: str = "") -> str:
    """Open a directory browser dialog with SD card sidebar entries on Steam Deck."""
    dialog = QFileDialog(parent, title, start_path or os.path.expanduser("~"))
    dialog.setFileMode(QFileDialog.Directory)
    dialog.setOption(QFileDialog.ShowDirsOnly, True)
    dialog.setSidebarUrls(_get_sidebar_urls())
    if dialog.exec():
        selected = dialog.selectedFiles()
        return os.path.realpath(selected[0]) if selected else ""
    return ""


def browse_file(parent: QWidget, title: str, start_path: str = "", file_filter: str = "") -> str:
    """Open a file browser dialog with SD card sidebar entries on Steam Deck."""
    dialog = QFileDialog(parent, title, start_path or os.path.expanduser("~"))
    dialog.setFileMode(QFileDialog.ExistingFile)
    if file_filter:
        dialog.setNameFilter(file_filter)
    dialog.setSidebarUrls(_get_sidebar_urls())
    if dialog.exec():
        selected = dialog.selectedFiles()
        return os.path.realpath(selected[0]) if selected else ""
    return ""
