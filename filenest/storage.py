import json
import sqlite3
import uuid
from contextlib import contextmanager
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from .models import Rule, default_rules
from .rules import validate


class Store:
    """每次连接独立，允许界面线程与后台任务分别读取历史。"""

    def __init__(self, directory):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path = self.directory / "history.sqlite3"
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS batches (
                    id TEXT PRIMARY KEY, created TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS operations (
                    id INTEGER PRIMARY KEY, batch_id TEXT NOT NULL,
                    source TEXT NOT NULL, destination TEXT NOT NULL,
                    action TEXT NOT NULL, status TEXT NOT NULL,
                    reason TEXT NOT NULL, snapshot TEXT NOT NULL,
                    after_snapshot TEXT, undo_status TEXT NOT NULL DEFAULT '',
                    undo_reason TEXT NOT NULL DEFAULT '');
            """)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def recover(self):
        with self.connect() as db:
            db.execute("UPDATE operations SET status='uncertain', reason=? WHERE status='running'",
                       ("上次执行被中断，请人工核对原路径与目标路径；不自动撤销",))
            db.execute("UPDATE operations SET status='cancelled', reason=? WHERE status='pending'",
                       ("上次任务中断，此项尚未执行",))
            db.execute("UPDATE operations SET undo_status='uncertain', undo_reason=? WHERE undo_status='running'",
                       ("撤销被中断，请人工核对文件位置",))

    def begin(self, items):
        batch = uuid.uuid4().hex
        ids = []
        with self.connect() as db:
            db.execute("INSERT INTO batches VALUES (?, ?)", (batch, datetime.now().isoformat(timespec="seconds")))
            for item in items:
                cur = db.execute("""INSERT INTO operations
                    (batch_id, source, destination, action, status, reason, snapshot)
                    VALUES (?, ?, ?, ?, ?, ?, ?)""", (batch, item.source, item.destination,
                    item.action, "pending" if item.status == "ready" else item.status,
                    item.reason, json.dumps(item.snapshot)))
                ids.append(cur.lastrowid)
        return batch, ids

    def update(self, row_id, **fields):
        allowed = {"status", "reason", "destination", "after_snapshot", "undo_status", "undo_reason"}
        if not fields or not fields.keys() <= allowed:
            raise ValueError("无效历史字段")
        with self.connect() as db:
            db.execute("UPDATE operations SET " + ", ".join(f"{k}=?" for k in fields) + " WHERE id=?",
                       [*fields.values(), row_id])

    def batches(self):
        with self.connect() as db:
            return [dict(r) for r in db.execute("""SELECT b.id, b.created, count(o.id) AS total,
                sum(o.status='success') AS success, sum(o.status='failed') AS failed,
                sum(o.undo_status='success') AS undone FROM batches b
                LEFT JOIN operations o ON b.id=o.batch_id GROUP BY b.id ORDER BY b.created DESC, b.rowid DESC""")]

    def operations(self, batch):
        with self.connect() as db:
            return [dict(r) for r in db.execute("SELECT * FROM operations WHERE batch_id=? ORDER BY id", (batch,))]

    def load_settings(self):
        path = self.directory / "settings.json"
        if not path.exists():
            return {}, default_rules()
        data = json.loads(path.read_text(encoding="utf-8"))
        rules = [Rule(**r) for r in data.pop("rules")]
        for rule in rules:
            validate(rule)
        return data, rules

    def save_settings(self, settings, rules):
        path = self.directory / "settings.json"
        temp = path.with_suffix(".tmp")
        temp.write_text(json.dumps({**settings, "rules": [asdict(r) for r in rules]},
                                   ensure_ascii=False, indent=2), encoding="utf-8")
        temp.replace(path)
