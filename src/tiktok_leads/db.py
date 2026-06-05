from __future__ import annotations

import sqlite3
from pathlib import Path

from tiktok_leads.models import Lead


SCHEMA = """
CREATE TABLE IF NOT EXISTS influencers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    handle TEXT NOT NULL,
    profile_url TEXT NOT NULL,
    niche TEXT NOT NULL,
    email TEXT NOT NULL UNIQUE,
    followers_count INTEGER NOT NULL,
    average_views INTEGER NOT NULL,
    source TEXT NOT NULL,
    notified_at TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_influencers_niche ON influencers(niche);
CREATE INDEX IF NOT EXISTS idx_influencers_handle ON influencers(handle);
"""


class LeadRepository:
    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.database_path)
        self.connection.row_factory = sqlite3.Row

    def initialize(self) -> None:
        self.connection.executescript(SCHEMA)
        self.connection.commit()

    def exists_by_email(self, email: str) -> bool:
        row = self.connection.execute(
            "SELECT 1 FROM influencers WHERE email = ? LIMIT 1",
            (email,),
        ).fetchone()
        return row is not None

    def insert_lead(self, lead: Lead) -> bool:
        try:
            self.connection.execute(
                """
                INSERT INTO influencers (
                    handle, profile_url, niche, email, followers_count,
                    average_views, source, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    lead.handle,
                    lead.profile_url,
                    lead.niche,
                    lead.email,
                    lead.followers_count,
                    lead.average_views,
                    lead.source,
                    lead.created_at.isoformat(),
                    lead.created_at.isoformat(),
                ),
            )
            self.connection.commit()
            return True
        except sqlite3.IntegrityError:
            return False

    def mark_notified(self, email: str) -> None:
        self.connection.execute(
            "UPDATE influencers SET notified_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP WHERE email = ?",
            (email,),
        )
        self.connection.commit()

    def close(self) -> None:
        self.connection.close()
