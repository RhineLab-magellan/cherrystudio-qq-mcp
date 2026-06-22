"""
QQ-MCP Bridge v3.0 - Automated Environment Setup
===================================================

Creates a Python virtual environment, installs all dependencies
(including Playwright Chromium browser), and verifies the setup.

Usage:
    python Install/setup_env.py                          # Full setup
    python Install/setup_env.py --clean                  # Remove existing venv and rebuild
    python Install/setup_env.py --skip-playwright        # Skip Chromium download
    python Install/setup_env.py --python C:\\path\\to\\python.exe  # Use specific Python
    python Install/setup_env.py --clean --skip-verify    # Clean rebuild, skip tests

Or simply run:
    Install\\install.bat              # Normal setup
    Install\\install.bat --clean      # Clean rebuild (recommended for fresh installs)
"""

import argparse
import logging
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

# ── Constants ────────────────────────────────────────────────────────────

MIN_PYTHON = (3, 10)
SCRIPT_DIR = Path(__file__).parent.resolve()
PROJECT_ROOT = SCRIPT_DIR.parent
VENV_DIR = PROJECT_ROOT / ".venv"

LOG_FILE = SCRIPT_DIR / "install.log"

# ── Logging ──────────────────────────────────────────────────────────────


def setup_logging():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
        handlers=[
            logging.FileHandler(str(LOG_FILE), mode="w", encoding="utf-8"),
            logging.StreamHandler(),
        ],
    )
    return logging.getLogger("setup")


# ── Console helpers ──────────────────────────────────────────────────────


def info(msg: str):
    print(f"  [INFO] {msg}")


def success(msg: str):
    print(f"  [ OK ] {msg}")


def warn(msg: str):
    print(f"  [WARN] {msg}")


def fail(msg: str):
    print(f"  [FAIL] {msg}")


# ── Utility functions ────────────────────────────────────────────────────


def find_python_executable() -> str | None:
    """Locate a Python interpreter >= MIN_PYTHON on this system."""

    # 1. py launcher (official Windows Python launcher)
    try:
        r = subprocess.run(
            ["py", "-3", "-c", "import sys; print(sys.executable)"],
            capture_output=True, text=True, timeout=10,
        )
        if r.returncode == 0:
            exe = r.stdout.strip()
            if _check_version(exe):
                return exe
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    # 2. PATH lookup
    for name in ("python", "python3"):
        try:
            r = subprocess.run(
                [name, "-c", "import sys; print(sys.executable)"],
                capture_output=True, text=True, timeout=10,
            )
            if r.returncode == 0:
                exe = r.stdout.strip()
                if _check_version(exe):
                    return exe
        except (FileNotFoundError, subprocess.TimeoutExpired):
            continue

    # 3. Known installation directories
    local_app = os.environ.get("LOCALAPPDATA", "")
    program_files = [
        os.environ.get("ProgramFiles", r"C:\Program Files"),
        os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
    ]

    search_dirs = []

    # python.org installer
    if local_app:
        base = os.path.join(local_app, "Programs", "Python")
        if os.path.isdir(base):
            search_dirs.extend(
                os.path.join(base, d)
                for d in sorted(os.listdir(base), reverse=True)
                if d.startswith("Python3")
            )

    # pythoncore standalone
    if local_app:
        pc = os.path.join(local_app, "Python")
        if os.path.isdir(pc):
            search_dirs.extend(
                os.path.join(pc, d)
                for d in sorted(os.listdir(pc), reverse=True)
                if d.startswith("pythoncore")
            )

    # Program Files
    for pf in program_files:
        if pf and os.path.isdir(pf):
            for d in os.listdir(pf):
                if d.lower().startswith("python"):
                    search_dirs.append(os.path.join(pf, d))

    for d in search_dirs:
        exe = os.path.join(d, "python.exe")
        if os.path.isfile(exe) and _check_version(exe):
            return exe

    # 4. Microsoft Store (App Execution Alias)
    wa = os.path.join(
        local_app,
        "Microsoft", "WindowsApps",
        "PythonSoftwareFoundation.Python.3.14_qbz5n2kfra8p0",
    )
    exe = os.path.join(wa, "python.exe")
    if os.path.isfile(exe) and _check_version(exe):
        return exe

    return None


def _check_version(exe: str) -> bool:
    """Return True if the Python at *exe* meets the minimum version."""
    if not exe or not os.path.isfile(exe):
        return False
    try:
        r = subprocess.run(
            [exe, "-c", "import sys; print(sys.version_info.major, sys.version_info.minor)"],
            capture_output=True, text=True, timeout=10,
        )
        if r.returncode != 0:
            return False
        parts = r.stdout.strip().split()
        major, minor = int(parts[0]), int(parts[1])
        return (major, minor) >= MIN_PYTHON
    except Exception:
        return False


def get_python_version(exe: str) -> str:
    """Return a human-readable version string for *exe*."""
    try:
        r = subprocess.run(
            [exe, "-c", "import sys; print(sys.version.split()[0])"],
            capture_output=True, text=True, timeout=10,
        )
        return r.stdout.strip() if r.returncode == 0 else "?"
    except Exception:
        return "?"


# ── Setup steps ──────────────────────────────────────────────────────────


def create_virtualenv(python_exe: str, logger: logging.Logger, clean: bool = False) -> bool:
    """Create (or verify) the .venv virtual environment.

    Args:
        python_exe: Path to the Python executable to use.
        logger: Logger instance.
        clean: If True, delete the existing venv before creating a new one.
    """
    venv_python = VENV_DIR / "Scripts" / "python.exe"

    # Also check for legacy 'venv' directory (without dot) for compatibility
    legacy_venv = PROJECT_ROOT / "venv"

    if clean:
        for vdir in [VENV_DIR, legacy_venv]:
            if vdir.exists():
                info(f"Removing existing virtual environment: {vdir}")
                try:
                    shutil.rmtree(vdir)
                    success(f"Removed: {vdir}")
                except Exception as e:
                    fail(f"Failed to remove {vdir}: {e}")
                    info("Please close any programs using the venv and try again.")
                    return False

    if venv_python.exists():
        info(f"Virtual environment already exists: {VENV_DIR}")
        info("Use --clean to remove and rebuild it from scratch.")
        return True

    info(f"Creating virtual environment at: {VENV_DIR}")
    r = subprocess.run(
        [python_exe, "-m", "venv", str(VENV_DIR)],
        capture_output=True, text=True, timeout=120,
    )

    if r.returncode != 0 or not venv_python.exists():
        fail(f"Failed to create venv: {r.stderr.strip()}")
        return False

    success("Virtual environment created")
    return True


def _venv_run(
    cmd: list[str],
    logger: logging.Logger,
    timeout: int = 600,
    env_extra: dict[str, str] | None = None,
) -> subprocess.CompletedProcess:
    """Run *cmd* with the venv's Python, inheriting environment."""
    venv_python = str(VENV_DIR / "Scripts" / "python.exe")
    full_cmd = [venv_python] + cmd

    env = os.environ.copy()
    env["VIRTUAL_ENV"] = str(VENV_DIR)
    # Ensure the venv's Scripts dir is first in PATH
    scripts_dir = str(VENV_DIR / "Scripts")
    env["PATH"] = scripts_dir + os.pathsep + env.get("PATH", "")

    # Merge extra env vars (e.g. PLAYWRIGHT_BROWSERS_PATH)
    if env_extra:
        env.update(env_extra)

    return subprocess.run(
        full_cmd,
        capture_output=True, text=True, timeout=timeout,
        cwd=str(PROJECT_ROOT), env=env,
    )


def upgrade_pip(logger: logging.Logger) -> bool:
    """Upgrade pip inside the venv."""
    info("Upgrading pip...")
    r = _venv_run(["-m", "pip", "install", "--upgrade", "pip"], logger, timeout=120)
    if r.returncode != 0:
        warn(f"pip upgrade returned non-zero (may still work): {r.stderr.strip()[:200]}")
    return True


def install_dependencies(logger: logging.Logger) -> bool:
    """Install all project dependencies from pyproject.toml."""
    info("Installing project dependencies (this may take a minute)...")
    r = _venv_run(["-m", "pip", "install", "-e", "."], logger, timeout=300)

    if r.returncode != 0:
        fail(f"pip install failed:\n{r.stderr}")
        return False

    # Summarize installed packages
    r2 = _venv_run(["-m", "pip", "list", "--format=columns"], logger, timeout=30)
    if r2.returncode == 0:
        pkg_lines = [l for l in r2.stdout.strip().splitlines() if l and not l.startswith("Package")]
        info(f"Installed {len(pkg_lines)} packages")

    success("Dependencies installed")
    return True


def install_playwright(logger: logging.Logger, max_retries: int = 3) -> bool:
    """Download Playwright's bundled Chromium browser into project Browsers/ dir."""
    browsers_dir = PROJECT_ROOT / "Browsers"
    info(f"Installing Playwright Chromium into {browsers_dir} ...")
    info("This may take a few minutes depending on your network speed.")

    # 设置 PLAYWRIGHT_BROWSERS_PATH 使浏览器下载到项目目录
    env_extra = {"PLAYWRIGHT_BROWSERS_PATH": str(browsers_dir)}

    for attempt in range(1, max_retries + 1):
        if attempt > 1:
            info(f"Retry {attempt}/{max_retries}...")

        t0 = time.time()
        r = _venv_run(
            ["-m", "playwright", "install", "chromium"],
            logger,
            timeout=600,
            env_extra=env_extra,
        )
        elapsed = time.time() - t0

        if r.returncode == 0:
            success(f"Playwright Chromium installed into Browsers/ ({elapsed:.0f}s)")
            return True

        warn(f"Attempt {attempt} failed ({elapsed:.0f}s): {r.stderr.strip()[:200]}")

    fail("Playwright Chromium installation failed after all retries")
    info("You can install it later manually:")
    info(f"  set PLAYWRIGHT_BROWSERS_PATH={browsers_dir}")
    info(f"  {VENV_DIR / 'Scripts' / 'python.exe'} -m playwright install chromium")
    return False


def verify_installation(logger: logging.Logger) -> dict[str, bool]:
    """Run a quick smoke test to verify the environment works."""
    info("Verifying installation...")
    results = {}

    # 1. Basic import check
    r = _venv_run(
        ["-c", "import markdown, aiohttp, websockets, pydantic; print('core imports OK')"],
        logger, timeout=15,
    )
    results["core_imports"] = r.returncode == 0 and "OK" in r.stdout
    _report("Core imports", results["core_imports"])

    # 2. Playwright import
    r = _venv_run(["-c", "from playwright.async_api import async_playwright; print('OK')"], logger, timeout=15)
    results["playwright_import"] = r.returncode == 0 and "OK" in r.stdout
    _report("Playwright import", results["playwright_import"])

    # 3. Playwright Chromium launch test (使用项目本地 Browsers/ 目录)
    test_code = (
        "import asyncio, os\n"
        f"os.environ['PLAYWRIGHT_BROWSERS_PATH'] = r'{PROJECT_ROOT / 'Browsers'}'\n"
        "from playwright.async_api import async_playwright\n"
        "async def _t():\n"
        "    async with async_playwright() as p:\n"
        "        b = await p.chromium.launch(headless=True)\n"
        "        v = b.version\n"
        "        await b.close()\n"
        "        print(f'OK:{v}')\n"
        "asyncio.run(_t())\n"
    )
    r = _venv_run(["-c", test_code], logger, timeout=30)
    ok = r.returncode == 0 and "OK:" in r.stdout
    results["chromium_launch"] = ok
    version = r.stdout.strip().replace("OK:", "") if ok else ""
    _report(f"Chromium launch{' (' + version + ')' if version else ''}", ok)
    if not ok and r.stderr:
        info(f"  -> {r.stderr.strip()[:200]}")

    # 4. End-to-end md_to_image test
    test_code2 = (
        "import asyncio\n"
        "import sys; sys.path.insert(0, '.')\n"
        "from modules.md_to_image import md_to_image\n"
        "async def _t():\n"
        "    p = await md_to_image('# Test\\nHello World')\n"
        "    print(f'OK:{p}')\n"
        "asyncio.run(_t())\n"
    )
    r = _venv_run(["-c", test_code2], logger, timeout=30)
    ok = r.returncode == 0 and "OK:" in r.stdout
    results["md_to_image"] = ok
    _report("md_to_image E2E", ok)

    return results


def _report(label: str, ok: bool):
    if ok:
        success(label)
    else:
        warn(label)


# ── Main ─────────────────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(
        description="QQ-MCP Bridge v3.0 - Automated Environment Setup"
    )
    parser.add_argument(
        "--python", "-p",
        help="Path to a Python executable (>= 3.10) to use for the venv",
    )
    parser.add_argument(
        "--skip-playwright",
        action="store_true",
        help="Skip downloading Playwright Chromium (not recommended)",
    )
    parser.add_argument(
        "--skip-verify",
        action="store_true",
        help="Skip the post-install verification step",
    )
    parser.add_argument(
        "--clean",
        action="store_true",
        help="Remove the existing virtual environment before rebuilding (recommended for clean installs)",
    )
    args = parser.parse_args()

    logger = setup_logging()
    logger.info(f"Setup started. Project root: {PROJECT_ROOT}")

    # ── Banner ──
    print()
    print("  ================================================")
    print("    QQ-MCP Bridge v3.0 - Environment Setup")
    print("  ================================================")
    print()

    # ── Step 1: Find Python ──
    print("  -- Step 1: Locate Python --")
    if args.python:
        python_exe = args.python
        if not _check_version(python_exe):
            fail(f"Specified Python does not meet requirement (>= {MIN_PYTHON[0]}.{MIN_PYTHON[1]}): {python_exe}")
            return 1
    else:
        python_exe = find_python_executable()
        if not python_exe:
            fail(f"Python >= {MIN_PYTHON[0]}.{MIN_PYTHON[1]} not found on this system.")
            info("Please install Python from https://www.python.org/downloads/")
            info("Make sure to check 'Add Python to PATH' during installation.")
            return 1

    ver = get_python_version(python_exe)
    success(f"Found Python {ver}: {python_exe}")
    print()

    # ── Step 2: Virtual environment ──
    print("  -- Step 2: Virtual Environment --")
    if not create_virtualenv(python_exe, logger, clean=args.clean):
        return 1
    print()

    # ── Step 3: Dependencies ──
    print("  -- Step 3: Install Dependencies --")
    upgrade_pip(logger)
    if not install_dependencies(logger):
        return 1
    print()

    # ── Step 4: Playwright Chromium ──
    if not args.skip_playwright:
        print("  -- Step 4: Playwright Chromium --")
        install_playwright(logger)
        print()

    # ── Step 5: Verify ──
    if not args.skip_verify:
        print("  -- Step 5: Verification --")
        results = verify_installation(logger)
        print()
    else:
        results = {}

    # ── Summary ──
    venv_python = VENV_DIR / "Scripts" / "python.exe"
    print("  ================================================")
    all_ok = all(results.values()) if results else True
    if all_ok:
        print("    Setup complete! All checks passed.")
    else:
        passed = sum(1 for v in results.values() if v)
        total = len(results)
        print(f"    Setup finished. {passed}/{total} checks passed.")
        print("    Review warnings above for any issues.")
    print("  ================================================")
    print()
    print("  Virtual Environment:")
    print(f"    Path         : {VENV_DIR}")
    print(f"    Python       : {venv_python}")
    print()
    print("  Next steps:")
    print(f"    Start bridge : {PROJECT_ROOT / 'start_bridge.bat'}")
    print(f"    Manual start : {venv_python} server.py")
    print(f"    Run tests    : {venv_python} -m pytest")
    if args.skip_playwright:
        browsers_dir = PROJECT_ROOT / "Browsers"
        print(f"    Install Chromium: set PLAYWRIGHT_BROWSERS_PATH={browsers_dir}")
        print(f"                      {venv_python} -m playwright install chromium")
    print()

    return 0


if __name__ == "__main__":
    sys.exit(main())
