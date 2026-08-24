import json
import sqlite3
from pathlib import Path

from .core import Vacancy


class Store:
    def __init__(self, path: str = "qa_jobs.sqlite3"):
        self.conn = sqlite3.connect(path)
        self.conn.execute("""CREATE TABLE IF NOT EXISTS vacancies (
            id TEXT PRIMARY KEY, source TEXT, title TEXT, url TEXT, payload TEXT,
            status TEXT, score INTEGER, updated_at TEXT DEFAULT CURRENT_TIMESTAMP)""")
        self.conn.execute("""CREATE TABLE IF NOT EXISTS manual_decisions (
            id TEXT PRIMARY KEY, status TEXT NOT NULL, reason TEXT NOT NULL,
            decided_at TEXT DEFAULT CURRENT_TIMESTAMP)""")
        self.conn.execute("""CREATE TABLE IF NOT EXISTS source_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            scanned_at TEXT DEFAULT CURRENT_TIMESTAMP,
            source_key TEXT NOT NULL,
            source_name TEXT NOT NULL,
            listed INTEGER NOT NULL,
            detailed INTEGER NOT NULL,
            collected INTEGER NOT NULL,
            recommended INTEGER NOT NULL,
            needs_review INTEGER NOT NULL,
            rejected INTEGER NOT NULL,
            status TEXT NOT NULL,
            errors TEXT NOT NULL
        )""")
        self.conn.commit()

    def save(self, vacancy: Vacancy) -> None:
        decision = self.conn.execute("SELECT status, reason FROM manual_decisions WHERE id=?", (vacancy.id,)).fetchone()
        if decision:
            vacancy.status = decision[0]
            vacancy.reasons = [f"Ручное решение: {decision[1]}"]
        self.conn.execute(
            """INSERT INTO vacancies(id,source,title,url,payload,status,score) VALUES(?,?,?,?,?,?,?)
            ON CONFLICT(id) DO UPDATE SET payload=excluded.payload,status=excluded.status,
            score=excluded.score,updated_at=CURRENT_TIMESTAMP""",
            (vacancy.id, vacancy.source, vacancy.title, vacancy.url,
             json.dumps(vacancy.__dict__, ensure_ascii=False), vacancy.status, vacancy.score),
        )
        self.conn.commit()

    def reject(self, vacancy_id: str, reason: str) -> bool:
        vacancy = self.get(vacancy_id)
        if vacancy is None:
            return False
        vacancy.status = "rejected"
        vacancy.reasons = [f"Ручное решение: {reason}"]
        self.conn.execute(
            "INSERT OR REPLACE INTO manual_decisions(id,status,reason) VALUES(?,?,?)",
            (vacancy_id, vacancy.status, reason),
        )
        self.save(vacancy)
        return True

    def get(self, vacancy_id: str) -> Vacancy | None:
        row = self.conn.execute("SELECT payload FROM vacancies WHERE id=?", (vacancy_id,)).fetchone()
        return Vacancy(**json.loads(row[0])) if row else None

    def recommended(self) -> list[Vacancy]:
        rows = self.conn.execute("SELECT payload FROM vacancies WHERE status='recommended' ORDER BY score DESC").fetchall()
        return [Vacancy(**json.loads(row[0])) for row in rows]

    def record_source_run(self, run, statuses: dict[str, int]) -> None:
        self.conn.execute(
            """INSERT INTO source_runs(source_key,source_name,listed,detailed,collected,
               recommended,needs_review,rejected,status,errors) VALUES(?,?,?,?,?,?,?,?,?,?)""",
            (run.source_key, run.source_name, run.listed, run.detailed, run.collected,
             statuses.get("recommended", 0), statuses.get("needs_review", 0),
             statuses.get("rejected", 0), run.status, json.dumps(run.errors, ensure_ascii=False)),
        )
        self.conn.commit()
