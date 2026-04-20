"""
Third-party tool registry.

Manages install, update, downgrade, and uninstall of independently-versioned
tools that Jackify either invokes directly (Tier 1) or makes available for users
to run from MO2 (Tier 2).

Each tool stores a manifest at:
  $jackify_data_dir/tools/<tool_id>/manifest.json

TTW_Linux_Installer is a special case: it has a pre-existing handler with its
own config keys.  The registry reads those keys for status display and delegates
install/update to the existing handler rather than managing storage itself.
"""

import json
import logging
import os
import re
import tarfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import requests

from jackify.shared.paths import get_jackify_data_dir

logger = logging.getLogger(__name__)

TOOLS_BASE_DIR = get_jackify_data_dir() / "tools"
GITHUB_API = "https://api.github.com/repos/{repo}/releases/{ref}"


@dataclass
class ToolDefinition:
    tool_id: str
    display_name: str
    description: str
    github_repo: str            # e.g. "SulfurNitride/CLF3"
    asset_patterns: List[str]   # ordered list of regex patterns to match release asset filename
    tier: int                   # 1 = Jackify invokes it, 2 = user runs it themselves
    executable_names: List[str] = field(default_factory=list)
    pinned_version: Optional[str] = None   # None = always use latest
    can_uninstall: bool = True             # False for tools Jackify hard-depends on


@dataclass
class ToolStatus:
    definition: ToolDefinition
    installed: bool
    installed_version: Optional[str]
    previous_version: Optional[str]
    binary_path: Optional[Path]
    latest_version: Optional[str] = None
    update_available: bool = False

    @property
    def can_downgrade(self) -> bool:
        prev_dir = TOOLS_BASE_DIR / self.definition.tool_id / "_previous"
        return self.previous_version is not None and prev_dir.exists()


# ---------------------------------------------------------------------------
# Tool catalogue
# ---------------------------------------------------------------------------

TOOL_DEFINITIONS: List[ToolDefinition] = [
    ToolDefinition(
        tool_id="ttw_installer",
        display_name="TTW Linux Installer",
        description="Automates Tale of Two Wastelands installation on Linux. Required for the TTW workflow.",
        github_repo="SulfurNitride/TTW_Linux_Installer",
        asset_patterns=[r"universal-mpi-installer.*\.(zip|tar\.gz)"],
        executable_names=["mpi_installer", "ttw_linux_gui"],
        tier=1,
        can_uninstall=False,
    ),
    ToolDefinition(
        tool_id="clf3",
        display_name="CLF3",
        description="Rust-based Wabbajack file handler. Planned as an experimental engine alternative.",
        github_repo="SulfurNitride/CLF3",
        asset_patterns=[r"clf3.*linux.*x86_64", r"clf3.*\.tar\.gz", r"clf3.*\.zip"],
        executable_names=["clf3"],
        tier=1,
        can_uninstall=True,
    ),
    ToolDefinition(
        tool_id="fluorine",
        display_name="Fluorine Manager",
        description="Linux-native MO2 port with FUSE-based VFS and built-in Rootbuilder support.",
        github_repo="SulfurNitride/Fluorine-Manager",
        asset_patterns=[r"fluorine.*\.appimage", r"fluorine.*\.tar\.gz", r"fluorine.*\.zip"],
        executable_names=["Fluorine", "fluorine"],
        tier=2,
    ),
    ToolDefinition(
        tool_id="bodyslide",
        display_name="BodySlide (Linux Port)",
        description="BodySlide and Outfit Studio ported to Linux. For body/outfit mesh conversion.",
        github_repo="SulfurNitride/BodySlide-and-Outfit-Studio-Linux-Port",
        asset_patterns=[r"bodyslide.*linux.*\.(appimage|tar\.gz|zip)", r".*bodyslide.*\.(tar\.gz|zip)"],
        executable_names=["BodySlide", "BodySlide_x64"],
        tier=2,
    ),
    ToolDefinition(
        tool_id="radium",
        display_name="Radium Textures",
        description="Rust alternative to VRAMr for Skyrim and Fallout 4 texture optimisation.",
        github_repo="SulfurNitride/Radium-Textures",
        asset_patterns=[r"radium.*linux.*x86_64", r"radium.*\.tar\.gz", r"radium.*\.zip"],
        executable_names=["radium", "radium-textures"],
        tier=2,
    ),
]

_TOOL_MAP: Dict[str, ToolDefinition] = {t.tool_id: t for t in TOOL_DEFINITIONS}


# ---------------------------------------------------------------------------
# Manifest helpers
# ---------------------------------------------------------------------------

def _manifest_path(tool_id: str) -> Path:
    return TOOLS_BASE_DIR / tool_id / "manifest.json"


def _read_manifest(tool_id: str) -> dict:
    mp = _manifest_path(tool_id)
    if mp.exists():
        try:
            return json.loads(mp.read_text())
        except Exception:
            pass
    return {}


def _write_manifest(tool_id: str, data: dict) -> None:
    mp = _manifest_path(tool_id)
    mp.parent.mkdir(parents=True, exist_ok=True)
    mp.write_text(json.dumps(data, indent=2))


# ---------------------------------------------------------------------------
# TTW bridge - reads existing config keys written by TTWInstallerHandler
# ---------------------------------------------------------------------------

def _ttw_status_from_config() -> Tuple[bool, Optional[str], Optional[Path]]:
    """Return (installed, version, binary_path) by reading TTWInstallerHandler config."""
    try:
        from jackify.backend.handlers.config_handler import ConfigHandler
        cfg = ConfigHandler()
        version = cfg.get("ttw_installer_version")
        install_path_str = cfg.get("ttw_installer_install_path")
        if not install_path_str:
            return False, None, None
        install_dir = Path(install_path_str)
        for exe_name in ["mpi_installer", "ttw_linux_gui"]:
            exe = install_dir / exe_name
            if exe.is_file():
                return True, str(version) if version else None, exe
        return False, None, None
    except Exception as e:
        logger.debug("TTW config read failed: %s", e)
        return False, None, None


# ---------------------------------------------------------------------------
# GitHub release fetching
# ---------------------------------------------------------------------------

def fetch_latest_release_info(github_repo: str, pinned_version: Optional[str] = None) -> Optional[dict]:
    """Fetch release metadata from GitHub API. Returns parsed JSON or None on failure."""
    if pinned_version:
        tags = [pinned_version, f"v{pinned_version}"] if not pinned_version.startswith("v") else [pinned_version]
        for tag in tags:
            url = GITHUB_API.format(repo=github_repo, ref=f"tags/{tag}")
            try:
                resp = requests.get(url, timeout=10, verify=True)
                if resp.status_code == 200:
                    return resp.json()
            except Exception as e:
                logger.debug("GitHub fetch error for %s@%s: %s", github_repo, tag, e)
        return None
    url = GITHUB_API.format(repo=github_repo, ref="latest")
    try:
        resp = requests.get(url, timeout=10, verify=True)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        logger.debug("GitHub fetch error for %s: %s", github_repo, e)
        return None


def _find_asset(release_data: dict, asset_patterns: List[str]) -> Optional[dict]:
    assets = release_data.get("assets", [])
    for pattern in asset_patterns:
        for asset in assets:
            if re.search(pattern, asset.get("name", ""), re.IGNORECASE):
                return asset
    return None


# ---------------------------------------------------------------------------
# Core install logic (shared across all non-TTW tools)
# ---------------------------------------------------------------------------

def _download_and_extract(tool_id: str, asset: dict, target_dir: Path) -> Tuple[bool, str]:
    """Download a release asset and extract it into target_dir."""
    from jackify.backend.handlers.filesystem_handler import FileSystemHandler
    fs = FileSystemHandler()

    asset_name = asset.get("name", "")
    download_url = asset.get("browser_download_url", "")
    if not download_url:
        return False, "Asset has no download URL"

    temp_path = target_dir / asset_name
    logger.info("Downloading %s", asset_name)
    if not fs.download_file(download_url, temp_path, overwrite=True, quiet=True):
        return False, f"Download failed: {asset_name}"

    try:
        name_lower = asset_name.lower()
        is_archive = False
        if name_lower.endswith(".tar.gz") or name_lower.endswith(".tgz"):
            is_archive = True
            with tarfile.open(temp_path, "r:gz") as tf:
                tf.extractall(path=target_dir)
        elif name_lower.endswith(".zip"):
            is_archive = True
            with zipfile.ZipFile(temp_path, "r") as zf:
                zf.extractall(path=target_dir)
        elif name_lower.endswith(".appimage"):
            temp_path.chmod(0o755)
        else:
            return False, f"Unsupported archive format: {asset_name}"
    finally:
        if is_archive:
            try:
                temp_path.unlink(missing_ok=True)
            except Exception:
                pass

    return True, ""


def _find_executable(tool_def: ToolDefinition, search_dir: Path) -> Optional[Path]:
    for exe_name in tool_def.executable_names:
        direct = search_dir / exe_name
        if direct.is_file():
            return direct
        for found in search_dir.rglob(exe_name):
            if found.is_file():
                return found
        # AppImage pattern
        for found in search_dir.rglob(f"{exe_name}*.AppImage"):
            if found.is_file():
                return found
    return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

class ToolRegistry:
    """Read/write interface to the managed tool store."""

    def get_status(self, tool_id: str) -> Optional[ToolStatus]:
        defn = _TOOL_MAP.get(tool_id)
        if defn is None:
            return None
        return self._build_status(defn)

    def get_all_statuses(self) -> List[ToolStatus]:
        return [self._build_status(d) for d in TOOL_DEFINITIONS]

    def check_latest_version(self, tool_id: str) -> Optional[str]:
        """Fetch latest tag from GitHub. Returns tag string or None."""
        defn = _TOOL_MAP.get(tool_id)
        if defn is None:
            return None
        data = fetch_latest_release_info(defn.github_repo, defn.pinned_version)
        if data:
            return data.get("tag_name") or data.get("name")
        return None

    def install(self, tool_id: str) -> Tuple[bool, str]:
        defn = _TOOL_MAP.get(tool_id)
        if defn is None:
            return False, f"Unknown tool: {tool_id}"

        if tool_id == "ttw_installer":
            return self._install_ttw()

        install_dir = TOOLS_BASE_DIR / tool_id
        install_dir.mkdir(parents=True, exist_ok=True)

        data = fetch_latest_release_info(defn.github_repo, defn.pinned_version)
        if not data:
            return False, f"Could not fetch release info for {defn.display_name}"

        asset = _find_asset(data, defn.asset_patterns)
        if not asset:
            all_names = [a.get("name", "") for a in data.get("assets", [])]
            return False, f"No matching asset found. Available: {', '.join(all_names)}"

        tag = data.get("tag_name") or data.get("name", "unknown")
        ok, err = _download_and_extract(tool_id, asset, install_dir)
        if not ok:
            return False, err

        exe_path = _find_executable(defn, install_dir)
        if exe_path:
            try:
                os.chmod(exe_path, 0o755)
            except Exception:
                pass

        manifest = _read_manifest(tool_id)
        _write_manifest(tool_id, {
            "installed_version": tag,
            "previous_version": manifest.get("installed_version"),
            "binary_path": str(exe_path) if exe_path else None,
            "install_dir": str(install_dir),
        })

        logger.info("Installed %s %s", defn.display_name, tag)
        return True, f"{defn.display_name} {tag} installed"

    def update(self, tool_id: str) -> Tuple[bool, str]:
        """Update to latest release. Saves current as previous for downgrade."""
        defn = _TOOL_MAP.get(tool_id)
        if defn is None:
            return False, f"Unknown tool: {tool_id}"

        if tool_id == "ttw_installer":
            return self._install_ttw()

        manifest = _read_manifest(tool_id)
        current_dir = TOOLS_BASE_DIR / tool_id
        prev_dir = TOOLS_BASE_DIR / tool_id / "_previous"

        # Back up current install before overwriting
        if current_dir.exists() and manifest.get("installed_version"):
            import shutil
            try:
                if prev_dir.exists():
                    shutil.rmtree(prev_dir)
                # Copy current files (excluding _previous subdir) to _previous
                prev_dir.mkdir(parents=True, exist_ok=True)
                for item in current_dir.iterdir():
                    if item.name == "_previous":
                        continue
                    dest = prev_dir / item.name
                    if item.is_file():
                        shutil.copy2(item, dest)
                    elif item.is_dir():
                        shutil.copytree(item, dest)
            except Exception as e:
                logger.warning("Could not back up previous version of %s: %s", tool_id, e)

        ok, msg = self.install(tool_id)
        if ok and manifest.get("installed_version"):
            # Preserve previous_version in manifest (install() sets it from current manifest)
            updated_manifest = _read_manifest(tool_id)
            updated_manifest["previous_version"] = manifest.get("installed_version")
            _write_manifest(tool_id, updated_manifest)
        return ok, msg

    def downgrade(self, tool_id: str) -> Tuple[bool, str]:
        """Swap current install with the backed-up previous version."""
        defn = _TOOL_MAP.get(tool_id)
        if defn is None:
            return False, f"Unknown tool: {tool_id}"
        if tool_id == "ttw_installer":
            return False, "Downgrade not supported for TTW Linux Installer via this interface"

        import shutil
        current_dir = TOOLS_BASE_DIR / tool_id
        prev_dir = TOOLS_BASE_DIR / tool_id / "_previous"

        if not prev_dir.exists():
            return False, f"No previous version stored for {defn.display_name}"

        manifest = _read_manifest(tool_id)
        current_version = manifest.get("installed_version")
        previous_version = manifest.get("previous_version")

        # Swap: move current out, move previous in
        swap_dir = TOOLS_BASE_DIR / tool_id / "_swap"
        try:
            if swap_dir.exists():
                shutil.rmtree(swap_dir)
            swap_dir.mkdir(parents=True)
            for item in current_dir.iterdir():
                if item.name in ("_previous", "_swap"):
                    continue
                shutil.move(str(item), str(swap_dir / item.name))
            for item in prev_dir.iterdir():
                shutil.move(str(item), str(current_dir / item.name))
            # Put what was current into _previous
            if prev_dir.exists():
                shutil.rmtree(prev_dir)
            prev_dir.mkdir()
            for item in swap_dir.iterdir():
                shutil.move(str(item), str(prev_dir / item.name))
            shutil.rmtree(swap_dir, ignore_errors=True)
        except Exception as e:
            return False, f"Downgrade failed: {e}"

        exe_path = _find_executable(defn, current_dir)
        if exe_path:
            try:
                os.chmod(exe_path, 0o755)
            except Exception:
                pass

        _write_manifest(tool_id, {
            "installed_version": previous_version,
            "previous_version": current_version,
            "binary_path": str(exe_path) if exe_path else None,
            "install_dir": str(current_dir),
        })
        logger.info("Downgraded %s from %s to %s", defn.display_name, current_version, previous_version)
        return True, f"{defn.display_name} downgraded to {previous_version}"

    def uninstall(self, tool_id: str) -> Tuple[bool, str]:
        defn = _TOOL_MAP.get(tool_id)
        if defn is None:
            return False, f"Unknown tool: {tool_id}"
        if not defn.can_uninstall:
            return False, f"{defn.display_name} cannot be uninstalled - Jackify depends on it"

        import shutil
        tool_dir = TOOLS_BASE_DIR / tool_id
        if tool_dir.exists():
            try:
                shutil.rmtree(tool_dir)
            except Exception as e:
                return False, f"Uninstall failed: {e}"

        logger.info("Uninstalled %s", defn.display_name)
        return True, f"{defn.display_name} uninstalled"

    def get_binary_path(self, tool_id: str) -> Optional[Path]:
        """Return the installed binary path for a Tier 1 tool, or None."""
        if tool_id == "ttw_installer":
            _, _, binary = _ttw_status_from_config()
            return binary
        manifest = _read_manifest(tool_id)
        bp = manifest.get("binary_path")
        if bp:
            p = Path(bp)
            if p.is_file():
                return p
        return None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_status(self, defn: ToolDefinition) -> ToolStatus:
        if defn.tool_id == "ttw_installer":
            installed, version, binary = _ttw_status_from_config()
            return ToolStatus(
                definition=defn,
                installed=installed,
                installed_version=version,
                previous_version=None,
                binary_path=binary,
            )
        manifest = _read_manifest(defn.tool_id)
        installed_version = manifest.get("installed_version")
        binary_path_str = manifest.get("binary_path")
        binary_path = Path(binary_path_str) if binary_path_str else None
        installed = installed_version is not None and (binary_path is None or binary_path.is_file())
        return ToolStatus(
            definition=defn,
            installed=installed,
            installed_version=installed_version,
            previous_version=manifest.get("previous_version"),
            binary_path=binary_path,
        )

    def _install_ttw(self) -> Tuple[bool, str]:
        """Delegate TTW install to the existing handler."""
        try:
            from jackify.backend.handlers.ttw_installer_handler import TTWInstallerHandler
            from jackify.backend.handlers.filesystem_handler import FileSystemHandler
            from jackify.backend.handlers.config_handler import ConfigHandler
            fs = FileSystemHandler()
            cfg = ConfigHandler()
            handler = TTWInstallerHandler(
                steamdeck=False, verbose=False,
                filesystem_handler=fs, config_handler=cfg,
            )
            return handler.install_ttw_installer()
        except Exception as e:
            return False, f"TTW install failed: {e}"
