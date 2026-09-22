"""任务执行引擎。

要点：
* 每个任务跑在独立的 Python 线程里，通过 Qt 信号（跨线程自动排队）回主线程刷新界面；
* ffmpeg 的 `-progress pipe:1` 输出在 stdout，逐行解析出进度/速度/剩余时间；
* stderr 单独线程消费，避免管道写满造成死锁，同时保留最后若干行用于报错提示。
"""

from __future__ import annotations

import re
import subprocess
import threading
import time
from collections import deque
from pathlib import Path

from PySide6.QtCore import QObject, Signal

from .commands import cleanup_passlogs
from .ffmpeg import FFmpegEnv
from .task import Task, TaskStatus
from .utils import ffmpeg_install_hint, is_windows, human_duration

CREATE_NO_WINDOW = 0x08000000 if is_windows() else 0

_ERR_HINTS = [
    (re.compile(r"Invalid data found when processing input", re.I),
     "源文件损坏或不是 ffmpeg 支持的格式"),
    (re.compile(r"No such file or directory", re.I), "找不到输入或输出文件路径"),
    (re.compile(r"Permission denied", re.I), "没有写入权限，换个输出目录试试"),
    (re.compile(r"Unknown encoder '([^']+)'", re.I),
     f"当前 ffmpeg 没有编译该编码器（{ffmpeg_install_hint()}）"),
    (re.compile(r"Error initializing output stream", re.I),
     "输出参数不被该容器接受，试着换 MKV 容器或改用 AAC 音频"),
    (re.compile(r"moov atom not found", re.I), "MP4 文件不完整（缺少 moov），无法处理"),
    (re.compile(r"height not divisible by 2", re.I),
     "分辨率必须为偶数，请把缩放高度改为偶数"),
    (re.compile(r"Conversion failed", re.I), "编码失败，详情见下方日志"),
    (re.compile(r"Invalid argument", re.I), "参数无效，可能源文件缺少可用音视频轨"),
]


def friendly_error(log: str) -> str:
    for pattern, hint in _ERR_HINTS:
        if pattern.search(log):
            return hint
    # 退回到最后一条 Error 行
    for line in reversed(log.splitlines()):
        if "error" in line.lower() or "invalid" in line.lower():
            return line.strip()[:200]
    return "编码失败，详情见日志"


class _Bridge(QObject):
    """工作线程 -> 主线程的信号桥。"""

    progress = Signal(str)          # task_id
    logLine = Signal(str, str)      # task_id, line
    state = Signal(str)             # task_id


class _Canceled(Exception):
    pass


class QueueManager(QObject):
    """队列调度 + 并发控制。"""

    taskAdded = Signal(str)
    taskUpdated = Signal(str)
    taskRemoved = Signal(str)
    queueEmpty = Signal()

    def __init__(self, env_provider, config, parent=None):
        super().__init__(parent)
        self._env_provider = env_provider          # callable -> FFmpegEnv
        self._config = config                      # callable -> AppConfig
        self._tasks: list[Task] = []
        self._threads: dict[str, threading.Thread] = {}
        self._lock = threading.RLock()
        self._paused = False

        self.bridge = _Bridge()
        self.bridge.progress.connect(self._on_progress)
        self.bridge.logLine.connect(self._on_log)
        self.bridge.state.connect(self._on_state)

    # ------------------------------------------------------------------ #
    # 队列操作
    # ------------------------------------------------------------------ #
    @property
    def tasks(self) -> list[Task]:
        return list(self._tasks)

    @property
    def paused(self) -> bool:
        return self._paused

    def add(self, task: Task) -> Task:
        with self._lock:
            self._tasks.append(task)
        self.taskAdded.emit(task.id)
        self._dispatch()
        return task

    def add_many(self, tasks: list[Task]) -> None:
        with self._lock:
            self._tasks.extend(tasks)
        for t in tasks:
            self.taskAdded.emit(t.id)
        self._dispatch()

    def remove(self, task_id: str) -> None:
        task = self.get(task_id)
        if task and task.is_active:
            self.cancel(task_id)
        with self._lock:
            self._tasks = [t for t in self._tasks if t.id != task_id]
        self.taskRemoved.emit(task_id)

    def clear_finished(self) -> None:
        with self._lock:
            keep = [t for t in self._tasks if not t.is_finished]
            gone = [t.id for t in self._tasks if t.is_finished]
            self._tasks = keep
        for tid in gone:
            self.taskRemoved.emit(tid)

    def clear_all(self) -> None:
        for t in self.tasks:
            if t.is_active:
                self.cancel(t.id)
        with self._lock:
            gone = [t.id for t in self._tasks]
            self._tasks = []
        for tid in gone:
            self.taskRemoved.emit(tid)

    def get(self, task_id: str) -> Task | None:
        for t in self._tasks:
            if t.id == task_id:
                return t
        return None

    def start(self) -> None:
        self._paused = False
        self._dispatch()

    def pause(self) -> None:
        """暂停派发新任务（已运行的任务会跑完，避免产生半截文件）。"""
        self._paused = True

    def cancel(self, task_id: str) -> None:
        task = self.get(task_id)
        if not task:
            return
        task.cancel_requested = True
        proc = task.process
        if proc is not None and proc.poll() is None:
            _terminate(proc)
        elif task.status == TaskStatus.WAITING:
            task.status = TaskStatus.CANCELED
            task.message = "已取消"
            task.finished = time.time()
            self.taskUpdated.emit(task.id)

    def retry(self, task_id: str) -> None:
        task = self.get(task_id)
        if not task or not task.is_finished:
            return
        task.status = TaskStatus.WAITING
        task.progress = 0.0
        task.speed = ""
        task.eta = -1.0
        task.error = ""
        task.message = ""
        task.log.clear()
        task.started = task.finished = 0.0
        task.cancel_requested = False
        self.taskUpdated.emit(task.id)
        self._dispatch()

    def running_count(self) -> int:
        return sum(1 for t in self._tasks if t.status == TaskStatus.RUNNING)

    def waiting_count(self) -> int:
        return sum(1 for t in self._tasks if t.status == TaskStatus.WAITING)

    # ------------------------------------------------------------------ #
    # 调度
    # ------------------------------------------------------------------ #
    def _dispatch(self) -> None:
        cfg = self._config()
        limit = max(1, int(cfg.concurrency))
        with self._lock:
            if self._paused:
                return
            while self.running_count() < limit:
                nxt = next((t for t in self._tasks if t.status == TaskStatus.WAITING
                            and not t.cancel_requested), None)
                if nxt is None:
                    break
                nxt.status = TaskStatus.RUNNING
                nxt.started = time.time()
                nxt.message = "启动中…"
                th = threading.Thread(target=self._run_task, args=(nxt,), daemon=True)
                self._threads[nxt.id] = th
                self.taskUpdated.emit(nxt.id)
                th.start()

    def _on_task_finished(self, task: Task) -> None:
        with self._lock:
            self._threads.pop(task.id, None)
        self.taskUpdated.emit(task.id)
        self._dispatch()
        if not any(t.is_active for t in self._tasks):
            self.queueEmpty.emit()

    # ------------------------------------------------------------------ #
    # 执行
    # ------------------------------------------------------------------ #
    def _run_task(self, task: Task) -> None:
        env: FFmpegEnv = self._env_provider()
        try:
            if not env.ready:
                raise RuntimeError("未找到 ffmpeg，请在「设置」中指定路径")
            if task.cancel_requested:
                raise _Canceled()

            weights = [s.weight for s in task.steps] or [1.0]
            total_weight = sum(weights)
            done_weight = 0.0

            for step in task.steps:
                if task.cancel_requested:
                    raise _Canceled()
                task.message = step.label or "处理中"
                self.bridge.state.emit(task.id)
                code = self._run_step(task, step, done_weight, total_weight)
                if task.cancel_requested:
                    raise _Canceled()
                if code != 0:
                    raise RuntimeError(friendly_error("\n".join(task.log)))
                done_weight += step.weight

            # 收尾
            if task.dst.exists():
                task.out_size = task.dst.stat().st_size
            task.progress = 1.0
            task.status = TaskStatus.DONE
            task.message = "已完成"
            task.eta = 0.0

        except _Canceled:
            task.status = TaskStatus.CANCELED
            task.message = "已取消"
            _safe_unlink(task.dst)
        except Exception as exc:  # noqa: BLE001
            task.status = TaskStatus.FAILED
            task.error = str(exc)
            task.message = "失败"
            _safe_unlink(task.dst)
        finally:
            task.finished = time.time()
            task.process = None
            cleanup_passlogs(task.work_dir)
            self._on_task_finished(task)

    def _run_step(self, task: Task, step, done_weight: float, total_weight: float) -> int:
        cmd = [str(a) for a in step.args]
        task.append_log(f"$ {' '.join(cmd)}")
        self.bridge.logLine.emit(task.id, f"$ {' '.join(_short(cmd))}")

        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                stdin=subprocess.DEVNULL,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                creationflags=CREATE_NO_WINDOW,
            )
        except FileNotFoundError as exc:
            raise RuntimeError(f"无法启动 ffmpeg：{exc}") from exc

        task.process = proc

        # 取消请求可能落在 Popen 尚未返回的窗口期（此时 task.process 还是 None，
        # cancel() 无从下手），所以进程创建完成后必须再补查一次
        if task.cancel_requested:
            _terminate(proc)

        err_buf: deque[str] = deque(maxlen=400)
        err_thread = threading.Thread(
            target=_drain_stderr, args=(task, proc, err_buf), daemon=True
        )
        err_thread.start()

        duration = task.duration
        last_emit = 0.0
        timeline = 0.0
        speed_val = 0.0

        assert proc.stdout is not None
        for raw in proc.stdout:
            line = raw.strip()
            if not line:
                continue
            key, _, value = line.partition("=")
            if key == "out_time_us" or key == "out_time_ms":
                try:
                    secs = int(value) / 1_000_000
                except ValueError:
                    continue
                timeline = max(timeline, secs)
                if duration > 0:
                    frac = min(1.0, timeline / duration)
                    task.progress = min(1.0, (done_weight + step.weight * frac) / total_weight)
                if speed_val > 0 and duration > 0:
                    remain = max(0.0, duration - timeline) / speed_val
                    task.eta = remain
                now = time.time()
                if now - last_emit > 0.12:
                    last_emit = now
                    self.bridge.progress.emit(task.id)
            elif key == "speed":
                m = re.match(r"([\d.]+)", value.strip())
                if m:
                    try:
                        speed_val = float(m.group(1))
                        task.speed = f"{speed_val:.2f}x"
                    except ValueError:
                        speed_val = 0.0
            elif key == "total_size":
                pass
            elif key == "progress" and value.strip() == "end":
                if duration > 0:
                    task.progress = min(
                        1.0, (done_weight + step.weight) / total_weight)
                self.bridge.progress.emit(task.id)

        code = proc.wait()
        err_thread.join(timeout=3)
        task.process = None
        for entry in list(err_buf)[-6:]:
            self.bridge.logLine.emit(task.id, entry)
        return code

    # ------------------------------------------------------------------ #
    # 信号槽
    # ------------------------------------------------------------------ #
    def _on_progress(self, task_id: str) -> None:
        self.taskUpdated.emit(task_id)

    def _on_log(self, task_id: str, line: str) -> None:
        task = self.get(task_id)
        if task:
            task.append_log(line)
            self.taskUpdated.emit(task_id)

    def _on_state(self, task_id: str) -> None:
        self.taskUpdated.emit(task_id)


# --------------------------------------------------------------------------- #
# 辅助
# --------------------------------------------------------------------------- #

def _drain_stderr(task: Task, proc, buf: deque) -> None:
    """持续读取 stderr，防止管道写满；同时保留最后若干行。"""
    stream = proc.stderr
    if stream is None:
        return
    try:
        for raw in stream:
            line = raw.rstrip()
            if line:
                buf.append(line)
    except (ValueError, OSError):
        pass


def _terminate(proc) -> None:
    try:
        proc.terminate()
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()
    except Exception:
        pass


def _safe_unlink(path: Path) -> None:
    """取消/失败时清掉半成品。"""
    try:
        if path.exists() and path.is_file():
            path.unlink()
    except OSError:
        pass


def _short(cmd: list[str]) -> list[str]:
    """日志里省略超长参数。"""
    out = []
    for part in cmd:
        out.append(part if len(part) < 90 else part[:87] + "…")
    return out
