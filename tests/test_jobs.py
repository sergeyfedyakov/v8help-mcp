import time

from v8help.config import Config
from v8help.jobs import JobManager


def test_job_lifecycle(tmp_path):
    cfg = Config()
    cfg.corpus_dir = tmp_path / "corpus"
    cfg.db_path = tmp_path / "test.db"
    cfg.books = []

    mgr = JobManager()
    job = mgr.start(cfg, force=True)
    assert job.id

    deadline = time.time() + 10
    while time.time() < deadline:
        status = mgr.status(job.id)
        if status.status != "running":
            break
        time.sleep(0.01)

    status = mgr.status(job.id)
    assert status is not None
    assert status.status == "done"
    assert status.error == ""
    assert status.result is not None
