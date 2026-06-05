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

CREATE TABLE IF NOT EXISTS scraped_profiles (
    handle TEXT PRIMARY KEY,
    niche TEXT NOT NULL,
    source TEXT NOT NULL,
    last_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    first_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_scraped_profiles_niche ON scraped_profiles(niche);
"""


class LeadRepository:
    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.database_path)
        self.connection.row_factory = sqlite3.Row

    def initialize(self) -> None:
        self.connection.executescript(SCHEMA)
        self._migrate_scraped_profiles_handle_unique()
        self._backfill_scraped_profiles_from_influencers()
        self.connection.commit()

    def exists_by_email(self, email: str) -> bool:
        row = self.connection.execute(
            "SELECT 1 FROM influencers WHERE email = ? LIMIT 1",
            (email,),
        ).fetchone()
        return row is not None

    def seen_handles(self) -> set[str]:
        rows = self.connection.execute(
            "SELECT handle FROM scraped_profiles UNION SELECT handle FROM influencers",
        ).fetchall()
        return {str(row["handle"]).removeprefix("@").lower() for row in rows}

    def record_scraped_profile(self, *, handle: str, niche: str, source: str) -> None:
        normalized_handle = handle.removeprefix("@").lower()
        self.connection.execute(
            """
            INSERT INTO scraped_profiles (handle, niche, source, last_seen_at)
            VALUES (?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(handle) DO UPDATE SET
                source = excluded.source,
                niche = excluded.niche,
                last_seen_at = CURRENT_TIMESTAMP
            """,
            (normalized_handle, niche, source),
        )
        self.connection.commit()

    def _migrate_scraped_profiles_handle_unique(self) -> None:
        table = self.connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'scraped_profiles'"
        ).fetchone()
        if table is None or "PRIMARY KEY (handle, niche)" not in str(table["sql"]):
            return

        self.connection.executescript(
            """
            ALTER TABLE scraped_profiles RENAME TO scraped_profiles_old;

            CREATE TABLE scraped_profiles (
                handle TEXT PRIMARY KEY,
                niche TEXT NOT NULL,
                source TEXT NOT NULL,
                last_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                first_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            INSERT INTO scraped_profiles (handle, niche, source, last_seen_at, first_seen_at)
            SELECT
                lower(replace(handle, '@', '')) AS handle,
                niche,
                source,
                max(last_seen_at) AS last_seen_at,
                min(last_seen_at) AS first_seen_at
            FROM scraped_profiles_old
            GROUP BY lower(replace(handle, '@', ''));

            DROP TABLE scraped_profiles_old;
            CREATE INDEX IF NOT EXISTS idx_scraped_profiles_niche ON scraped_profiles(niche);
            """
        )

    def _backfill_scraped_profiles_from_influencers(self) -> None:
        self.connection.execute(
            """
            INSERT INTO scraped_profiles (handle, niche, source, last_seen_at, first_seen_at)
            SELECT
                lower(replace(handle, '@', '')) AS handle,
                niche,
                source,
                COALESCE(updated_at, created_at, CURRENT_TIMESTAMP) AS last_seen_at,
                COALESCE(created_at, CURRENT_TIMESTAMP) AS first_seen_at
            FROM influencers
            WHERE handle IS NOT NULL AND handle != ''
            ON CONFLICT(handle) DO UPDATE SET
                last_seen_at = max(scraped_profiles.last_seen_at, excluded.last_seen_at)
            """
        )

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
