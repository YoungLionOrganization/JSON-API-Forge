from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from framework.config import load_config  # noqa: E402
from framework.domain import expand_feature_packs  # noqa: E402

if __name__ == "__main__":
    config = load_config(ROOT / "app")
    print(f"Forge projects: {len(config.projects)}")
    for project in config.projects:
        expand_feature_packs(project)
        print(
            f"- {project.slug}: prefix={project.api_prefix} databases={len(project.databases)} resources={len(project.resources)} media={project.media.enabled}"
        )
