# 리더 선출: MySQL GET_LOCK을 전용 연결로 쥔다. 연결이 끊기면 MySQL이 자동 해제하고 10초 안에 다른 태스크가 넘겨받는다.
import logging
import threading

log = logging.getLogger("app.Server_Monitor_Bot.leader")


class Leader(threading.Thread):
    def __init__(self, db, stage: str, stop_event: threading.Event, interval: float = 10.0):
        super().__init__(name="server-bot-leader", daemon=True)
        self.db, self.name_, self.stop_event, self.interval = db, f"server_bot:{stage}:leader", stop_event, interval
        self.conn = None
        self.leading = threading.Event()

    def is_leader(self) -> bool:
        return self.leading.is_set()

    def step(self) -> None:
        try:
            if self.conn is None:
                self.conn = self.db.lock_connection()
                with self.conn.cursor() as cur:
                    cur.execute("SELECT GET_LOCK(%s, 0) AS got", (self.name_,))
                    got = bool(cur.fetchone()["got"])
                if got:
                    self.leading.set()
                    log.info("leader acquired: %s", self.name_)
                else:
                    self.conn.close()
                    self.conn = None
            else:
                with self.conn.cursor() as cur:
                    cur.execute("SELECT IS_USED_LOCK(%s) = CONNECTION_ID() AS mine", (self.name_,))
                    if not cur.fetchone()["mine"]:
                        raise RuntimeError("lock lost")
        except Exception as exc:
            if self.leading.is_set():
                log.warning("leader lost: error=%s", type(exc).__name__)
            self.leading.clear()
            if self.conn is not None:
                try:
                    self.conn.close()
                except Exception:
                    pass
            self.conn = None

    def run(self) -> None:
        while not self.stop_event.is_set():
            self.step()
            self.stop_event.wait(self.interval)
        self.leading.clear()
        if self.conn is not None:
            self.conn.close()
