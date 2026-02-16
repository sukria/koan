"""Systemd service PATH building and template rendering.

Provides testable logic for generating systemd service files with a
properly sanitized PATH that preserves the caller's environment.
"""

import glob
import os
import sys

ESSENTIAL_DIRS = [
    "/usr/local/sbin",
    "/usr/local/bin",
    "/usr/sbin",
    "/usr/bin",
    "/sbin",
    "/bin",
]


def build_safe_path(raw_path: str, home_dir: str) -> str:
    """Build a sanitized PATH for systemd services.

    Filters out home-directory entries for security, ensures essential
    system directories are present, and deduplicates while preserving order.
    """
    home = home_dir.rstrip("/")
    seen = set()
    result = []

    for entry in raw_path.split(":"):
        entry = entry.strip()
        if not entry:
            continue
        if entry == home or entry.startswith(home + "/"):
            continue
        if entry not in seen:
            seen.add(entry)
            result.append(entry)

    for d in ESSENTIAL_DIRS:
        if d not in seen:
            seen.add(d)
            result.append(d)

    return ":".join(result)


def render_service_template(
    template_path: str, koan_root: str, python_path: str, safe_path: str
) -> str:
    """Render a single .service.template file with placeholder substitution."""
    with open(template_path, "r") as f:
        content = f.read()
    content = content.replace("__KOAN_ROOT__", koan_root)
    content = content.replace("__PYTHON__", python_path)
    content = content.replace("__PATH__", safe_path)
    return content


def render_all_templates(
    template_dir: str, koan_root: str, python_path: str, safe_path: str
) -> dict:
    """Render all koan*.service.template files in a directory.

    Returns dict mapping service filename (without .template) to content.
    """
    pattern = os.path.join(template_dir, "koan*.service.template")
    results = {}
    for template_path in sorted(glob.glob(pattern)):
        service_name = os.path.basename(template_path).replace(".template", "")
        results[service_name] = render_service_template(
            template_path, koan_root, python_path, safe_path
        )
    return results


def main():
    """CLI entrypoint: render templates to an output directory.

    Usage: python -m app.systemd_service <koan_root> <python_path> <caller_path> <output_dir>
    """
    if len(sys.argv) != 5:
        print(
            "Usage: python -m app.systemd_service <koan_root> <python_path> <caller_path> <output_dir>",
            file=sys.stderr,
        )
        sys.exit(1)

    koan_root, python_path, caller_path, output_dir = sys.argv[1:5]

    home_dir = os.path.expanduser("~")
    sudo_user = os.environ.get("SUDO_USER")
    if sudo_user:
        home_dir = os.path.expanduser(f"~{sudo_user}")

    safe_path = build_safe_path(caller_path, home_dir)

    template_dir = os.path.join(os.path.dirname(__file__), "..", "systemd")
    rendered = render_all_templates(template_dir, koan_root, python_path, safe_path)

    os.makedirs(output_dir, exist_ok=True)
    for service_name, content in rendered.items():
        out_path = os.path.join(output_dir, service_name)
        with open(out_path, "w") as f:
            f.write(content)
        print(f"→ Generated {service_name}")


if __name__ == "__main__":
    main()
