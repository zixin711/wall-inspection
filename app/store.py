"""任务存储：SQLite 存结果元数据，文件系统存图像资源。

不做账号体系 —— 任务用不可猜测的 id 标识，凭链接访问。
保留期到点自动清理，默认 30 天。
"""
from __future__ import annotations

import json
import secrets
import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Optional

_SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    id           TEXT PRIMARY KEY,
    created_at   TEXT NOT NULL,
    status       TEXT NOT NULL,
    level        INTEGER,
    result_json  TEXT,
    raw_llm_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_tasks_created ON tasks(created_at);
"""


def new_task_id() -> str:
    return secrets.token_urlsafe(12)


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Store:
    def __init__(self, root: Path, retention_days: int = 30) -> None:
        self.root = root
        self.retention_days = retention_days
        self.files = root / "tasks"
        self.files.mkdir(parents=True, exist_ok=True)
        self.db_path = root / "tasks.db"
        self._lock = threading.Lock()
        try:
            with self._conn() as c:
                c.executescript(_SCHEMA)
        except sqlite3.OperationalError as exc:
            # SQLite 需要文件锁，网络盘 / SMB / NFS / 容器共享挂载上常常拿不到，
            # 报出来的是一句没头没脑的 "disk I/O error"。这里换成能照着做的提示。
            raise RuntimeError(
                f"无法在 {self.db_path} 上初始化数据库：{exc}。\n"
                "如果 STORAGE_DIR 指向网络盘或共享挂载（NFS / SMB / 容器 bind mount），"
                "SQLite 无法加锁。请把 STORAGE_DIR 改到本地磁盘目录。"
            ) from exc

    def _conn(self) -> sqlite3.Connection:
        c = sqlite3.connect(self.db_path, timeout=10)
        c.row_factory = sqlite3.Row
        # 并发读写更稳；busy_timeout 避免多 worker 下的瞬时 database is locked
        try:
            c.execute("PRAGMA journal_mode=WAL")
            c.execute("PRAGMA busy_timeout=8000")
            c.execute("PRAGMA synchronous=NORMAL")
        except sqlite3.OperationalError:
            pass
        return c

    # ------------------------------------------------------------ files
    def task_dir(self, task_id: str) -> Path:
        d = self.files / task_id
        d.mkdir(parents=True, exist_ok=True)
        return d

    def put_asset(self, task_id: str, name: str, data: bytes) -> str:
        p = self.task_dir(task_id) / name
        p.write_bytes(data)
        return f"/api/v1/tasks/{task_id}/assets/{name}"

    def get_asset(self, task_id: str, name: str) -> Optional[Path]:
        # 防目录穿越：只接受纯文件名
        if "/" in name or "\\" in name or name.startswith("."):
            return None
        p = self.files / task_id / name
        return p if p.is_file() else None

    # ------------------------------------------------------------ records
    def create(self, task_id: str) -> None:
        with self._lock, self._conn() as c:
            c.execute("INSERT OR REPLACE INTO tasks(id, created_at, status) VALUES(?,?,?)",
                      (task_id, utcnow(), "running"))

    def finish(self, task_id: str, status: str, result: Dict[str, Any],
               raw_llm: Optional[Dict[str, Any]] = None) -> None:
        with self._lock, self._conn() as c:
            c.execute("UPDATE tasks SET status=?, level=?, result_json=?, raw_llm_json=? WHERE id=?",
                      (status,
                       (result.get("grade") or {}).get("level"),
                       json.dumps(result, ensure_ascii=False),
                       json.dumps(raw_llm, ensure_ascii=False) if raw_llm else None,
                       task_id))

    def get(self, task_id: str) -> Optional[Dict[str, Any]]:
        with self._conn() as c:
            row = c.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        if row is None:
            return None
        out = dict(row)
        if out.get("result_json"):
            out["result"] = json.loads(out["result_json"])
        return out

    # ------------------------------------------------------------ retention
    def purge_expired(self) -> int:
        if self.retention_days <= 0:
            return 0
        cutoff = (datetime.now(timezone.utc) - timedelta(days=self.retention_days)).isoformat()
        with self._lock, self._conn() as c:
            rows = c.execute("SELECT id FROM tasks WHERE created_at < ?", (cutoff,)).fetchall()
            ids = [r["id"] for r in rows]
            if ids:
                c.executemany("DELETE FROM tasks WHERE id=?", [(i,) for i in ids])
        for tid in ids:
            d = self.files / tid
            if d.is_dir():
                for f in d.iterdir():
                    try:
                        f.unlink()
                    except OSError:
                        pass
                try:
                    d.rmdir()
                except OSError:
                    pass
        return len(ids)

    def delete(self, task_id: str) -> bool:
        with self._lock, self._conn() as c:
            cur = c.execute("DELETE FROM tasks WHERE id=?", (task_id,))
            gone = cur.rowcount > 0
        d = self.files / task_id
        if d.is_dir():
            for f in d.iterdir():
                try:
                    f.unlink()
                except OSError:
                    pass
            try:
                d.rmdir()
            except OSError:
                pass
        return gone
