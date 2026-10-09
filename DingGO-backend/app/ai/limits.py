"""对话限流与排队（单进程内存实现；服务只跑一个进程，不要多开 worker）。

- 每人同一时间只能有一个提问在进行（上一个没答完不能再问）；
- 每人每分钟最多 chat_per_minute 次；每人每天的上限按 chat_logs 统计（重启不清零，见 router）；
- 全局同时向大模型提问的人数最多 chat_max_concurrent，超出的排队，最多等 chat_queue_wait_seconds；
  排队的人数超过 chat_max_queue 直接拒绝，避免所有人一起卡死。
"""

import threading
import time
from collections import deque

from ..config import get_settings


class Rejected(Exception):
    """被限流或排队超时：message 直接给销售看，status 是 HTTP 状态码"""

    def __init__(self, message: str, status: int = 429):
        super().__init__(message)
        self.message, self.status = message, status


class ChatLimiter:
    def __init__(self) -> None:
        self.cond = threading.Condition()
        self.active_users: set[int] = set()
        self.running = 0
        self.waiting: deque[int] = deque()  # 排队的用户，先到先得
        self.recent: dict[int, deque[float]] = {}

    def check_rate(self, user_id: int) -> None:
        st, now = get_settings(), time.time()
        with self.cond:
            hits = self.recent.setdefault(user_id, deque())
            while hits and now - hits[0] > 60:
                hits.popleft()
            if len(hits) >= st.chat_per_minute:
                raise Rejected("提问太快了，请稍等一会儿再问", 429)
            hits.append(now)

    def acquire(self, user_id: int, on_wait=None) -> None:
        """拿到「提问名额」才能调用大模型；拿不到就排队。on_wait(排第几位) 用来给前端显示排队进度"""
        st = get_settings()
        deadline = time.time() + st.chat_queue_wait_seconds
        with self.cond:
            if user_id in self.active_users:
                raise Rejected("上一个问题还在回答中，请等它答完再问", 429)
            self.active_users.add(user_id)
            if self.running >= st.chat_max_concurrent and len(self.waiting) >= st.chat_max_queue:
                self.active_users.discard(user_id)
                raise Rejected("现在提问的人比较多，请稍后再试", 503)
            self.waiting.append(user_id)
            try:
                last_ahead = -1
                while not (self.running < st.chat_max_concurrent and self.waiting[0] == user_id):
                    ahead = list(self.waiting).index(user_id) + 1  # 排第几位
                    if on_wait and ahead != last_ahead:
                        last_ahead = ahead
                        on_wait(ahead)
                    left = deadline - time.time()
                    if left <= 0:
                        raise Rejected("排队时间太长了，请稍后再试", 503)
                    self.cond.wait(timeout=min(left, 1.0))
                self.running += 1
            except BaseException:
                self.active_users.discard(user_id)
                raise
            finally:
                if user_id in self.waiting:
                    self.waiting.remove(user_id)
                self.cond.notify_all()

    def release(self, user_id: int) -> None:
        with self.cond:
            self.running = max(0, self.running - 1)
            self.active_users.discard(user_id)
            self.cond.notify_all()


limiter = ChatLimiter()
