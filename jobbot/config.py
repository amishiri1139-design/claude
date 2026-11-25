"""Load config.yaml / profile.yaml and the resume text."""
from __future__ import annotations

import random
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent


def _load_yaml(name: str) -> dict[str, Any]:
    path = ROOT / name
    if not path.exists():
        example = ROOT / name.replace(".yaml", ".example.yaml")
        raise SystemExit(
            f"Missing {name}. Copy {example.name} to {name} and fill it in:\n"
            f"    cp {example.name} {name}"
        )
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def read_resume_text(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        from pypdf import PdfReader

        return "\n".join(page.extract_text() or "" for page in PdfReader(str(path)).pages)
    if suffix == ".docx":
        import docx

        return "\n".join(p.text for p in docx.Document(str(path)).paragraphs)
    return path.read_text(encoding="utf-8", errors="ignore")


@dataclass
class Settings:
    config: dict[str, Any]
    profile: dict[str, Any]
    resume_path: Path
    resume_text: str

    @property
    def dry_run(self) -> bool:
        return bool(self.config.get("dry_run", True))

    def get(self, *keys: str, default: Any = None) -> Any:
        node: Any = self.config
        for k in keys:
            if not isinstance(node, dict) or k not in node:
                return default
            node = node[k]
        return node

    def pause(self, kind: str = "action_delay") -> None:
        lo, hi = self.get("pacing", kind, default=[1.5, 4.0])
        time.sleep(random.uniform(lo, hi))


def load_settings(dry_run_override: bool | None = None) -> Settings:
    config = _load_yaml("config.yaml")
    profile = _load_yaml("profile.yaml")
    if dry_run_override is not None:
        config["dry_run"] = dry_run_override

    resume_path = (ROOT / profile.get("resume_file", "resumes/resume.pdf")).resolve()
    if not resume_path.exists():
        raise SystemExit(f"Resume not found at {resume_path}. Set resume_file in profile.yaml.")
    return Settings(config, profile, resume_path, read_resume_text(resume_path))
