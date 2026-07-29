from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from .store import KnowledgeStore, sha256_file


SUPPORTED_SOURCE_EXTENSIONS = {
    ".csv",
    ".docx",
    ".htm",
    ".html",
    ".jpeg",
    ".jpg",
    ".md",
    ".pdf",
    ".png",
    ".txt",
    ".webp",
    ".xlsx",
}


@dataclass(frozen=True)
class RegisteredSource:
    path: Path
    document_id: str
    version_id: str
    document_type: str
    checksum: str


def slugify(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return normalized or "source"


def title_from_path(path: Path) -> str:
    title = re.sub(r"[_-]+", " ", path.stem)
    return re.sub(r"\s+", " ", title).strip()


def iter_source_files(source_root: str | Path) -> list[Path]:
    root = Path(source_root).resolve()
    if not root.is_dir():
        raise FileNotFoundError(f"Source directory does not exist: {root}")
    return sorted(
        (
            path
            for path in root.rglob("*")
            if path.is_file()
            and not path.name.startswith(".")
            and path.suffix.lower() in SUPPORTED_SOURCE_EXTENSIONS
        ),
        key=lambda path: path.as_posix().lower(),
    )


def register_source_tree(
    store: KnowledgeStore,
    source_root: str | Path,
) -> list[RegisteredSource]:
    root = Path(source_root).resolve()
    registered: list[RegisteredSource] = []

    for path in iter_source_files(root):
        relative = path.relative_to(root)
        document_type = relative.parts[0].lower() if len(relative.parts) > 1 else "other"
        identity_path = relative.with_suffix("").as_posix()
        document_id = slugify(identity_path)
        checksum = sha256_file(path)

        store.register_document(
            document_id=document_id,
            file_name=path.name,
            title=title_from_path(path),
            document_type=document_type,
        )
        version_id = store.register_document_version(
            document_id=document_id,
            checksum=checksum,
            local_path=str(path),
            status="registered",
        )
        registered.append(
            RegisteredSource(
                path=path,
                document_id=document_id,
                version_id=version_id,
                document_type=document_type,
                checksum=checksum,
            )
        )

    return registered

