"""Workspace folders, safe moves, note files, run lock."""

from __future__ import annotations

import os
from pathlib import Path

FOLDERS = ("inbox", "processing", "approved", "flagged", "needs_approval", "rejected")


class RunLocked(Exception):
    pass


class Workspace:
    def __init__(self, root: Path) -> None:
        self.root = root
        for name in FOLDERS:
            (root / name).mkdir(parents=True, exist_ok=True)

    def dir(self, name: str) -> Path:
        return self.root / name

    @property
    def db_path(self) -> Path:
        return self.root / "invoices.db"

    @property
    def log_path(self) -> Path:
        return self.root / "action_log.jsonl"

    def rel(self, path: Path) -> str:
        return str(path.resolve().relative_to(self.root.resolve()))

    def abs(self, rel: str) -> Path:
        p = (self.root / rel).resolve()
        if not p.is_relative_to(self.root.resolve()):
            raise ValueError("path escapes workspace")
        return p

    def safe_move(self, src: Path, dest_folder: str) -> Path:
        """Atomic move into a folder; adds -1, -2 ... on name collision."""
        dest_dir = self.dir(dest_folder)
        target = dest_dir / src.name
        n = 0
        while target.exists():
            n += 1
            target = dest_dir / f"{src.stem}-{n}{src.suffix}"
        os.replace(src, target)
        return target

    @staticmethod
    def write_note(pdf_path: Path, text: str) -> Path:
        note = pdf_path.with_name(pdf_path.name + ".note.txt")
        note.write_text(text + "\n", encoding="utf-8")
        return note

    def move_with_note(self, src: Path, dest_folder: str) -> Path:
        """Move a file and its note (if any) together."""
        note = src.with_name(src.name + ".note.txt")
        new = self.safe_move(src, dest_folder)
        if note.exists():
            os.replace(note, new.with_name(new.name + ".note.txt"))
        return new

    def counts(self) -> dict[str, int]:
        return {f: len(list(self.dir(f).glob("*.pdf"))) for f in FOLDERS}

    def inbox_files(self) -> list[Path]:
        return sorted(p for p in self.dir("inbox").iterdir() if p.suffix.lower() == ".pdf" and p.is_file())

    # --- run lock -------------------------------------------------------
    @property
    def lock_path(self) -> Path:
        return self.root / ".run.lock"

    def acquire_lock(self) -> None:
        for _ in range(2):
            try:
                fd = os.open(self.lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(fd, str(os.getpid()).encode())
                os.close(fd)
                return
            except FileExistsError:
                if _pid_alive(self._lock_pid()):
                    raise RunLocked(f"another run is active (pid {self._lock_pid()})") from None
                self.lock_path.unlink(missing_ok=True)
        raise RunLocked("could not acquire run lock")

    def release_lock(self) -> None:
        self.lock_path.unlink(missing_ok=True)

    def _lock_pid(self) -> int | None:
        try:
            return int(self.lock_path.read_text().strip())
        except (OSError, ValueError):
            return None


def _pid_alive(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True
