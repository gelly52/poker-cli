"""异步 JSONL writer：保证 main 线程 write 永不阻塞。

目标项目调 `write(record)` 仅做 `queue.put_nowait`：
- 队列未满 → 立即返回，后台线程异步刷盘
- 队列已满 → 静默丢弃，绝不抛栈到目标项目（observer 不能影响目标稳定性）

后台线程持续从队列取记录并 `f.write + f.flush`；写盘异常也吞掉。
`close()` 通知后台线程把队列残留刷完后退出，最长等 timeout 秒。
"""
import json
import queue
import threading
from pathlib import Path
from typing import Any


class AsyncJsonlWriter:
    """非阻塞 JSONL writer。"""

    def __init__(
        self,
        file_path: Path,
        max_queue: int = 1000,
        join_timeout: float = 2.0,
    ) -> None:
        self._path = Path(file_path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._queue: "queue.Queue[dict[str, Any] | None]" = queue.Queue(maxsize=max_queue)
        self._stop = threading.Event()
        self._join_timeout = join_timeout
        self._dropped = 0
        self._thread = threading.Thread(target=self._run, name="poker-observer-writer", daemon=True)
        self._thread.start()

    @property
    def file_path(self) -> Path:
        return self._path

    @property
    def dropped(self) -> int:
        """因队列满被丢弃的记录数（observability 用）。"""
        return self._dropped

    def write(self, record: dict) -> None:
        """非阻塞投递；队满静默丢弃，不抛栈。"""
        try:
            self._queue.put_nowait(record)
        except queue.Full:
            self._dropped += 1
        except Exception:
            # 任何意外都吞掉 —— observer 不能影响目标项目
            self._dropped += 1

    def close(self) -> None:
        """请求停止 + 等后台线程把队列刷完；超时也不抛。"""
        self._stop.set()
        try:
            # 推一条 sentinel 让后台线程从 get(block) 立即苏醒
            self._queue.put_nowait(None)
        except queue.Full:
            pass
        try:
            self._thread.join(timeout=self._join_timeout)
        except Exception:
            pass

    def _run(self) -> None:
        try:
            f = self._path.open("a", encoding="utf-8")
        except OSError:
            return
        try:
            while True:
                if self._stop.is_set() and self._queue.empty():
                    return
                try:
                    record = self._queue.get(timeout=0.5)
                except queue.Empty:
                    continue
                if record is None:  # sentinel
                    if self._stop.is_set() and self._queue.empty():
                        return
                    continue
                try:
                    f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
                    f.flush()
                except Exception:
                    # 写盘失败也不抛
                    continue
        finally:
            try:
                f.close()
            except Exception:
                pass
