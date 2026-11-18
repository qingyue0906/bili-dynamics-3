from __future__ import annotations

import time

from bili3.config import load_config
from bili3.db import Database
from bili3.tasks import TaskManager


def wait_for(tm: TaskManager, job_id: int, timeout: float = 5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        db = Database(tm.db_path, init=False)
        try:
            job = db.get_job(job_id)
        finally:
            db.close()
        if job and job["status"] not in ("queued", "running", "cancelling"):
            return job
        time.sleep(0.05)
    raise AssertionError("job timeout")


def test_task_success_and_progress(tmp_path):
    config = load_config(base_dir=tmp_path, data_dir=tmp_path / "data")
    tm = TaskManager(config)

    def job(ctx):
        ctx.update(total=3, done=1, message="一半")
        ctx.update(done=3, message="全部完成")

    job_id = tm.submit("test", "target", job)
    row = wait_for(tm, job_id)
    assert row["status"] == "succeeded"
    assert row["done"] == 3
    assert row["total"] == 3
    assert row["message"] == "全部完成"
    assert row["log_path"]


def test_task_cancel(tmp_path):
    config = load_config(base_dir=tmp_path, data_dir=tmp_path / "data")
    tm = TaskManager(config)

    def job(ctx):
        ctx.update(total=10, message="循环中")
        for _ in range(100):
            time.sleep(0.02)
            ctx.check_cancel()

    job_id = tm.submit("test", "cancel", job)
    deadline = time.time() + 3
    while time.time() < deadline:
        db = Database(tm.db_path, init=False)
        try:
            status = db.get_job(job_id)["status"]
        finally:
            db.close()
        if status == "running":
            break
        time.sleep(0.02)
    assert tm.cancel(job_id)
    row = wait_for(tm, job_id)
    assert row["status"] == "cancelled"
