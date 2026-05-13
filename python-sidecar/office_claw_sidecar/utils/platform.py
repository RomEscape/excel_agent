"""OS detection and platform-specific helpers."""

import platform
import sys


def get_target_triple() -> str:
    """Get the Tauri-compatible target triple for the current platform."""
    system = platform.system()
    machine = platform.machine()

    triples = {
        ("Windows", "AMD64"): "x86_64-pc-windows-msvc",
        ("Windows", "x86_64"): "x86_64-pc-windows-msvc",
        ("Darwin", "x86_64"): "x86_64-apple-darwin",
        ("Darwin", "arm64"): "aarch64-apple-darwin",
        ("Linux", "x86_64"): "x86_64-unknown-linux-gnu",
        ("Linux", "aarch64"): "aarch64-unknown-linux-gnu",
    }

    # Normalize Windows machine name
    if system == "Windows" and machine == "x86_64":
        machine = "AMD64"

    return triples.get((system, machine), f"unknown-{system}-{machine}")


def is_frozen() -> bool:
    """Check if running as a PyInstaller bundle."""
    return getattr(sys, "frozen", False)
