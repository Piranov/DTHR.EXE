import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from .storage import Fault

TERMINAL = {"completed", "failed", "cancelled"}


@dataclass
class Job:
    id: str
    status: str = "queued"
    progress: float = 0
    result: dict = field(default_factory=dict)
    error: dict | None = None
    cancel: threading.Event = field(default_factory=threading.Event)
    expired: bool = False
    future: object = None

    def snapshot(self):
        return dict(
            id=self.id,
            status=self.status,
            progress=self.progress,
            error=self.error,
            **self.result,
        )


class Jobs:
    def __init__(self, capacity=16, timeout=1800):
        self.gpu = ThreadPoolExecutor(1, "generation")
        self.cpu = ThreadPoolExecutor(2, "processing")
        self.lock = threading.RLock()
        self.jobs = {}
        self.capacity, self.timeout = capacity, timeout

    def submit(self, work, gpu=True):
        with self.lock:
            if (
                sum(j.status not in TERMINAL for j in self.jobs.values())
                >= self.capacity
            ):
                raise Fault("queue_full", "Job queue is full")
            if len(self.jobs) >= 256:
                del self.jobs[
                    next(k for k, j in self.jobs.items() if j.status in TERMINAL)
                ]
            job = Job(uuid.uuid4().hex)
            self.jobs[job.id] = job
            job.future = (self.gpu if gpu else self.cpu).submit(self.run, job, work)
            return dict(job_id=job.id, status_url="/v1/jobs/" + job.id, status="queued")

    def run(self, job, work):
        def update(state, fraction):
            with self.lock:
                if not job.cancel.is_set():
                    job.status, job.progress = state, fraction

        def expire():
            with self.lock:
                if job.status not in TERMINAL:
                    job.expired = True
                    self.cancel(job.id)

        timer = threading.Timer(self.timeout, expire)
        timer.daemon = True
        timer.start()
        try:
            result = work(update, job.cancel)
            with self.lock:
                if job.cancel.is_set():
                    raise Fault("cancelled", "Job cancelled")
                job.result, job.status, job.progress = result, "completed", 1
        except Exception as exc:
            with self.lock:
                code = "timeout" if job.expired else getattr(exc, "code", "job_failed")
                job.status = "cancelled" if code == "cancelled" else "failed"
                job.error = dict(
                    code=code,
                    message=str(exc)
                    if not job.expired
                    else "Job exceeded execution timeout",
                )
        finally:
            timer.cancel()

    def get(self, id):
        with self.lock:
            if id not in self.jobs:
                raise Fault("unknown_job", "Job ID not found")
            return self.jobs[id].snapshot()

    def cancel(self, id):
        with self.lock:
            self.get(id)
            job = self.jobs[id]
            if job.status not in TERMINAL:
                job.cancel.set()
                job.status = "cancelled" if job.future.cancel() else "cancelling"
            return job.snapshot()

    def close(self):
        with self.lock:
            for id in self.jobs:
                self.cancel(id)
        self.gpu.shutdown(wait=True, cancel_futures=True)
        self.cpu.shutdown(wait=True, cancel_futures=True)
