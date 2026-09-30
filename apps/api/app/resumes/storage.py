"""Storage for uploaded files. Keys are generated server-side; user-supplied filenames are
never used as paths."""

from pathlib import Path
from typing import Protocol

from fastapi import Depends

from app.core.config import Settings, get_settings


class FileStorage(Protocol):
    def save(self, key: str, data: bytes) -> None: ...
    def delete(self, key: str) -> None: ...


class LocalFileStorage:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).resolve()

    def _path(self, key: str) -> Path:
        path = (self.root / key).resolve()
        if not path.is_relative_to(self.root):  # defence in depth; keys are generated
            raise ValueError("invalid storage key")
        return path

    def save(self, key: str, data: bytes) -> None:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def delete(self, key: str) -> None:
        self._path(key).unlink(missing_ok=True)


def get_storage(settings: Settings = Depends(get_settings)) -> FileStorage:
    return LocalFileStorage(settings.storage_dir)
