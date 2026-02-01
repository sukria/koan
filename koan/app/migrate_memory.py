#!/usr/bin/env python3
"""
Migrate instance/memory/ from flat to hybrid structure.
Run once when moving from single-project to multi-project setup.

Usage:
    python3 koan/migrate_memory.py
"""

import os
import shutil
from pathlib import Path

KOAN_ROOT = Path(os.environ["KOAN_ROOT"])
INSTANCE = KOAN_ROOT / "instance"
MEMORY = INSTANCE / "memory"


def migrate():
    """Migrate memory structure to support multi-project."""
    if not MEMORY.exists():
        print("❌ No memory directory found. Nothing to migrate.")
        print(f"   Expected: {MEMORY}")
        return

    print("🔄 Migrating memory structure to support multi-project...")
    print()

    # Create new structure
    global_dir = MEMORY / "global"
    default_project = MEMORY / "projects" / "default"

    global_dir.mkdir(exist_ok=True)
    default_project.mkdir(parents=True, exist_ok=True)

    # Move global files
    global_files = ["human-preferences.md", "strategy.md", "genese.md", "draft-bot.md"]
    for filename in global_files:
        src = MEMORY / filename
        if src.exists():
            dst = global_dir / filename
            print(f"📁 Moving {filename} → global/")
            shutil.move(str(src), str(dst))

    # Move project-specific files to default/
    project_files = ["learnings.md", "context.md"]
    for filename in project_files:
        src = MEMORY / filename
        if src.exists():
            dst = default_project / filename
            print(f"📁 Moving {filename} → projects/default/")
            shutil.move(str(src), str(dst))
        elif filename == "learnings.md":
            # Create empty learnings.md if it doesn't exist
            (default_project / filename).write_text("# Learnings\n\nProject-specific learnings and discoveries.\n")
            print(f"📝 Created projects/default/{filename}")

    # summary.md stays at root
    summary_path = MEMORY / "summary.md"
    if summary_path.exists():
        print(f"✓ Keeping summary.md at root")
    else:
        summary_path.write_text("# Session Summary\n\nRolling summary of past sessions. Updated by Kōan after each run.\n")
        print(f"📝 Created summary.md at root")

    print()
    print("✅ Migration complete!")
    print()
    print(f"   📄 summary.md: {MEMORY / 'summary.md'}")
    print(f"   🌍 Global context: {global_dir}/")
    print(f"   📦 Default project: {default_project}/")
    print()
    print("💡 To add a new project, create: memory/projects/<project-name>/")


if __name__ == "__main__":
    migrate()
