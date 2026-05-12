# -*- coding: utf-8 -*-
# asyncio 后台任务管理器
# 使用 asyncio.Queue + worker 协程实现任务队列

import asyncio
import traceback
import uuid
from typing import Any, Callable, Coroutine, Dict, Optional

from tools import utils


class TaskInfo:
    """单个任务的状态信息"""

    MAX_LOGS = 200

    def __init__(self, task_id: str, total: int = 0):
        self.task_id = task_id
        self.status = "pending"
        self.progress = 0.0
        self.total = total
        self.processed = 0
        self.message = ""
        self.result_file: Optional[str] = None
        self.error: Optional[str] = None
        self.logs: list = []
        self._cancelled = False
        self.single_result: Optional[Dict] = None
        self.result_data: Optional[list] = None   # 原始结果列表（用于 JSON 返回）
        self.comments_data: Optional[list] = None  # 评论数据列表（按作品分组）
        self.callback_url: Optional[str] = None    # 任务级回调地址

    @property
    def is_cancelled(self) -> bool:
        return self._cancelled

    def cancel(self):
        """标记任务为已取消"""
        self._cancelled = True
        self.status = "cancelled"
        self.message = "任务已被用户终止"

    def add_log(self, msg: str):
        """添加一条处理日志"""
        from datetime import datetime
        ts = datetime.now().strftime("%H:%M:%S")
        entry = f"[{ts}] {msg}"
        self.logs.append(entry)
        if len(self.logs) > self.MAX_LOGS:
            self.logs = self.logs[-self.MAX_LOGS:]

    def to_dict(self) -> Dict:
        return {
            "task_id": self.task_id,
            "status": self.status,
            "progress": self.progress,
            "total": self.total,
            "processed": self.processed,
            "message": self.message,
            "result_file": self.result_file,
            "logs": self.logs,
        }


class TaskManager:
    """
    基于 asyncio 的后台任务管理器。
    任务提交后立即返回 task_id，后台 worker 异步执行。
    """

    def __init__(self, max_workers: int = 1):
        self._queue: asyncio.Queue = asyncio.Queue()
        self._tasks: Dict[str, TaskInfo] = {}
        self._max_workers = max_workers
        self._workers: list = []

    async def start(self):
        """启动后台 worker 协程"""
        for i in range(self._max_workers):
            worker = asyncio.create_task(self._worker_loop(i))
            self._workers.append(worker)
        utils.logger.info(f"[TaskManager] 启动 {self._max_workers} 个 worker")

    async def stop(self):
        """停止所有 worker"""
        for w in self._workers:
            w.cancel()
        self._workers.clear()
        utils.logger.info("[TaskManager] 所有 worker 已停止")

    def submit(
        self,
        coro_factory: Callable[[TaskInfo], Coroutine],
        total: int = 0,
        prefix: str = "task",
    ) -> str:
        """
        提交一个异步任务。
        coro_factory: 接收 TaskInfo 参数的协程工厂函数
        prefix: 任务 ID 前缀，如 batch / file-xxx / mysql
        返回 task_id
        """
        short_id = str(uuid.uuid4())[:6]
        task_id = f"{prefix}-{short_id}"
        info = TaskInfo(task_id, total=total)
        self._tasks[task_id] = info
        self._queue.put_nowait((coro_factory, info))
        utils.logger.info(f"[TaskManager] 任务已提交: {task_id} total={total}")
        return task_id

    def get_task(self, task_id: str) -> Optional[TaskInfo]:
        return self._tasks.get(task_id)

    def remove_task(self, task_id: str) -> bool:
        """删除已终态的任务记录"""
        if task_id in self._tasks:
            del self._tasks[task_id]
            return True
        return False

    async def _worker_loop(self, worker_id: int):
        """worker 主循环，从队列取任务执行"""
        utils.logger.info(f"[TaskManager] Worker-{worker_id} 启动")
        while True:
            try:
                coro_factory, info = await self._queue.get()
                info.status = "running"
                info.message = "处理中..."
                try:
                    await coro_factory(info)
                    if info.status == "running":
                        info.status = "completed"
                        info.progress = 100.0
                        info.message = "处理完成"
                    # 任务完成后触发回调
                    if info.status == "completed":
                        try:
                            from api.callback import trigger_task_callback
                            await trigger_task_callback(info)
                        except Exception as cb_err:
                            utils.logger.warning(f"[TaskManager] 回调失败: {cb_err}")
                except Exception as e:
                    info.status = "failed"
                    info.error = str(e)
                    info.message = f"处理失败: {e}"
                    utils.logger.error(
                        f"[TaskManager] 任务 {info.task_id} 失败: "
                        f"{traceback.format_exc()}"
                    )
                finally:
                    self._queue.task_done()
            except asyncio.CancelledError:
                break
            except Exception as e:
                utils.logger.error(f"[TaskManager] Worker-{worker_id} 异常: {e}")
                await asyncio.sleep(1)


# 全局单例
task_manager = TaskManager(max_workers=1)
