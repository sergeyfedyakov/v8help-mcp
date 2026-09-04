"""Асинхронное управление сборкой: один активный job в фоновой нити."""

from __future__ import annotations

import dataclasses
import threading
import time
import uuid
from dataclasses import dataclass, field

from v8help.config import Config
from v8help.indexer import BuildResult, run_build


_RESULT_FIELDS = (
    "pages", "links", "sources", "duration_sec", "db_path", "bin_dir",
    "chunks", "vectors", "embed_model", "embed_dims", "embed_chars",
    "chunk_size", "chunk_overlap", "threads",
)


def _result_fields(r: BuildResult | None) -> dict:
    """Поля результата сборки: None до завершения; skipped=False, пока нет результата."""
    if r is None:
        d = {k: None for k in _RESULT_FIELDS}
        d["skipped"] = False
        return d
    return {"skipped": r.skipped, **{k: getattr(r, k) for k in _RESULT_FIELDS}}


@dataclass
class Job:
    id: str
    status: str = "running"  # running | done | error
    stage: str = ""
    message: str = ""
    result: BuildResult | None = None
    error: str = ""
    started_at: float = field(default_factory=time.time)
    finished_at: float | None = None

    def as_dict(self) -> dict:
        d = {
            "id": self.id,
            "status": self.status,
            "stage": self.stage,
            "message": self.message,
        }
        d.update(_result_fields(self.result))
        d["error"] = self.error or None
        return d


class JobManager:
    """Держит не более одной активной сборки; хранит последнюю для опроса."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._active: Job | None = None
        self._last: Job | None = None

    def start(self, config: Config, force: bool, cleanup: bool | None = None) -> Job:
        with self._lock:
            if self._active is not None and self._active.status == "running":
                raise RuntimeError(f"Сборка уже выполняется (job {self._active.id})")
            job = Job(id=uuid.uuid4().hex[:12])
            self._active = job
            self._last = job

        cfg = _with_cleanup(config, cleanup) if cleanup is not None else config

        def target() -> None:
            try:
                job.result = run_build(cfg, force=force, on_progress=self._progress(job))
                job.status = "done"
            except Exception as exc:
                job.error = f"{type(exc).__name__}: {exc}"
                job.status = "error"
            finally:
                job.finished_at = time.time()

        threading.Thread(target=target, name="v8help-build", daemon=True).start()
        return job

    def _progress(self, job: Job):
        def cb(stage: str, message: str) -> None:
            job.stage = stage
            job.message = message

        return cb

    def status(self, job_id: str) -> Job | None:
        if self._active is not None and self._active.id == job_id:
            return self._active
        if self._last is not None and self._last.id == job_id:
            return self._last
        return None

    def busy(self) -> bool:
        return self._active is not None and self._active.status == "running"


def _with_cleanup(config: Config, cleanup: bool) -> Config:
    return dataclasses.replace(
        config,
        build=dataclasses.replace(config.build, cleanup=cleanup),
    )


_manager: JobManager | None = None


def get_manager() -> JobManager:
    global _manager
    if _manager is None:
        _manager = JobManager()
    return _manager
