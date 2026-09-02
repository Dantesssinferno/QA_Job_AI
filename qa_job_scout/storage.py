from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta

from .core import Vacancy


class Store:
    def __init__(self, path: str = "qa_jobs.sqlite3"):
        self.conn = sqlite3.connect(path)

        self.conn.execute(
            """CREATE TABLE IF NOT EXISTS vacancies (
                id TEXT PRIMARY KEY,
                source TEXT,
                title TEXT,
                url TEXT,
                payload TEXT,
                status TEXT,
                score INTEGER,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            )"""
        )

        self.conn.execute(
            """CREATE TABLE IF NOT EXISTS manual_decisions (
                id TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                reason TEXT NOT NULL,
                decided_at TEXT DEFAULT CURRENT_TIMESTAMP
            )"""
        )

        self.conn.execute(
            """CREATE TABLE IF NOT EXISTS source_runs (
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
            )"""
        )

        self.conn.commit()

    def save(self, vacancy: Vacancy) -> None:
        decision = self.conn.execute(
            "SELECT status, reason FROM manual_decisions WHERE id=?",
            (vacancy.id,),
        ).fetchone()

        if decision:
            vacancy.status = decision[0]
            vacancy.reasons = [
                f"Ручное решение: {decision[1]}"
            ]

        self.conn.execute(
            """
            INSERT INTO vacancies(
                id,
                source,
                title,
                url,
                payload,
                status,
                score
            )
            VALUES(?,?,?,?,?,?,?)

            ON CONFLICT(id) DO UPDATE SET
                payload=excluded.payload,
                status=excluded.status,
                score=excluded.score,
                updated_at=CURRENT_TIMESTAMP
            """,
            (
                vacancy.id,
                vacancy.source,
                vacancy.title,
                vacancy.url,
                json.dumps(
                    vacancy.__dict__,
                    ensure_ascii=False,
                ),
                vacancy.status,
                vacancy.score,
            ),
        )

        self.conn.commit()

    def reject(
        self,
        vacancy_id: str,
        reason: str,
    ) -> bool:
        vacancy = self.get(vacancy_id)

        if vacancy is None:
            return False

        vacancy.status = "rejected"

        vacancy.reasons = [
            f"Ручное решение: {reason}"
        ]

        self.conn.execute(
            """
            INSERT OR REPLACE INTO manual_decisions(
                id,
                status,
                reason
            )
            VALUES(?,?,?)
            """,
            (
                vacancy_id,
                vacancy.status,
                reason,
            ),
        )

        self.save(vacancy)

        return True

    def get(
        self,
        vacancy_id: str,
    ) -> Vacancy | None:
        row = self.conn.execute(
            "SELECT payload FROM vacancies WHERE id=?",
            (vacancy_id,),
        ).fetchone()

        if not row:
            return None

        return Vacancy(
            **json.loads(row[0])
        )

    def recommended(
        self,
        now: datetime | None = None,
        max_age_days: int = 5,
    ) -> list[Vacancy]:
        """
        Возвращает только актуальные recommended-вакансии.

        ВАЖНО:
        status='recommended' — это сохранённый исторический статус.

        Поэтому одной проверки status недостаточно.
        Вакансия, которая была рекомендована несколько дней назад,
        должна автоматически исчезнуть из отчёта после истечения
        допустимого возраста.
        """

        now = now or datetime.now(UTC)

        cutoff = now - timedelta(
            days=max_age_days
        )

        rows = self.conn.execute(
            """
            SELECT payload
            FROM vacancies
            WHERE status='recommended'
            ORDER BY score DESC
            """
        ).fetchall()

        fresh: list[Vacancy] = []

        for row in rows:
            vacancy = Vacancy(
                **json.loads(row[0])
            )

            # Без надёжной даты вакансию нельзя считать актуальной.
            if not vacancy.published_at:
                continue

            try:
                published_at = datetime.fromisoformat(
                    vacancy.published_at
                )

            except ValueError:
                continue

            # Если datetime записан без timezone,
            # считаем его UTC.
            if published_at.tzinfo is None:
                published_at = published_at.replace(
                    tzinfo=UTC
                )
            else:
                published_at = published_at.astimezone(
                    UTC
                )

            # Не допускаем:
            # 1. слишком старые вакансии;
            # 2. даты из будущего.
            if cutoff <= published_at <= now:
                fresh.append(vacancy)

        return fresh

    def record_source_run(
        self,
        run,
        statuses: dict[str, int],
    ) -> None:
        self.conn.execute(
            """
            INSERT INTO source_runs(
                source_key,
                source_name,
                listed,
                detailed,
                collected,
                recommended,
                needs_review,
                rejected,
                status,
                errors
            )
            VALUES(?,?,?,?,?,?,?,?,?,?)
            """,
            (
                run.source_key,
                run.source_name,
                run.listed,
                run.detailed,
                run.collected,
                statuses.get("recommended", 0),
                statuses.get("needs_review", 0),
                statuses.get("rejected", 0),
                run.status,
                json.dumps(
                    run.errors,
                    ensure_ascii=False,
                ),
            ),
        )

        self.conn.commit()