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
    skip_reason TEXT,
    last_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    first_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_scraped_profiles_niche ON scraped_profiles(niche);

CREATE TABLE IF NOT EXISTS discovered_hashtags (
    hashtag TEXT NOT NULL,
    niche TEXT NOT NULL,
    times_seen INTEGER NOT NULL DEFAULT 1,
    last_scraped_at TEXT,
    first_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (hashtag, niche)
);

CREATE INDEX IF NOT EXISTS idx_discovered_hashtags_niche ON discovered_hashtags(niche);
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
        self._migrate_add_scraped_profiles_skip_reason()
        self._backfill_scraped_profiles_from_influencers()
        self.connection.commit()

    def _migrate_add_scraped_profiles_skip_reason(self) -> None:
        columns = {
            row["name"]
            for row in self.connection.execute("PRAGMA table_info(scraped_profiles)").fetchall()
        }
        if "skip_reason" not in columns:
            self.connection.execute("ALTER TABLE scraped_profiles ADD COLUMN skip_reason TEXT")
        # Created here (not in SCHEMA) so it runs only after the column exists.
        self.connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_scraped_profiles_recheck "
            "ON scraped_profiles(skip_reason, last_seen_at)"
        )

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

    def record_scraped_profile(
        self,
        *,
        handle: str,
        niche: str,
        source: str,
        skip_reason: str | None = None,
    ) -> None:
        normalized_handle = handle.removeprefix("@").lower()
        self.connection.execute(
            """
            INSERT INTO scraped_profiles (handle, niche, source, skip_reason, last_seen_at)
            VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(handle) DO UPDATE SET
                source = excluded.source,
                niche = excluded.niche,
                skip_reason = excluded.skip_reason,
                last_seen_at = CURRENT_TIMESTAMP
            """,
            (normalized_handle, niche, source, skip_reason),
        )
        self.connection.commit()

    def excluded_handles(self, recheck_after_days: int) -> set[str]:
        """Handles to skip on crawls: confirmed leads, plus anything evaluated
        within the recheck window. Stale non-leads fall out so they can be
        re-evaluated when a feed surfaces them again."""
        rows = self.connection.execute(
            """
            SELECT handle FROM influencers
            UNION
            SELECT handle FROM scraped_profiles
            WHERE last_seen_at >= datetime('now', ?)
            """,
            (f"-{recheck_after_days} days",),
        ).fetchall()
        return {str(row["handle"]).removeprefix("@").lower() for row in rows}

    def handles_due_for_recheck(
        self,
        *,
        after_days: int,
        limit: int,
        reasons: tuple[str, ...] = ("no_email",),
    ) -> list[tuple[str, str]]:
        """Stale, non-lead profiles worth re-checking for a newly-added email.
        Returns (handle, niche) ordered oldest-first."""
        placeholders = ",".join("?" for _ in reasons)
        rows = self.connection.execute(
            f"""
            SELECT sp.handle AS handle, sp.niche AS niche
            FROM scraped_profiles sp
            LEFT JOIN influencers i ON i.handle = sp.handle
            WHERE i.handle IS NULL
              AND sp.skip_reason IN ({placeholders})
              AND sp.last_seen_at < datetime('now', ?)
            ORDER BY sp.last_seen_at ASC
            LIMIT ?
            """,
            (*reasons, f"-{after_days} days", limit),
        ).fetchall()
        return [(str(row["handle"]), str(row["niche"])) for row in rows]

    def record_discovered_hashtags(self, hashtags: list[str], *, niche: str) -> None:
        for raw in hashtags:
            tag = raw.removeprefix("#").strip().lower()
            if not tag:
                continue
            self.connection.execute(
                """
                INSERT INTO discovered_hashtags (hashtag, niche, times_seen, last_seen_at)
                VALUES (?, ?, 1, CURRENT_TIMESTAMP)
                ON CONFLICT(hashtag, niche) DO UPDATE SET
                    times_seen = times_seen + 1,
                    last_seen_at = CURRENT_TIMESTAMP
                """,
                (tag, niche),
            )
        self.connection.commit()

    def due_hashtags(
        self,
        *,
        niche: str,
        limit: int,
        min_times_seen: int = 2,
        not_scraped_within_days: int = 7,
    ) -> list[str]:
        """Harvested hashtags worth crawling: seen enough times and not scraped
        recently. Ordered by how often we've seen them (strongest signal first)."""
        rows = self.connection.execute(
            """
            SELECT hashtag FROM discovered_hashtags
            WHERE niche = ?
              AND times_seen >= ?
              AND (last_scraped_at IS NULL OR last_scraped_at < datetime('now', ?))
            ORDER BY times_seen DESC, last_seen_at DESC
            LIMIT ?
            """,
            (niche, min_times_seen, f"-{not_scraped_within_days} days", limit),
        ).fetchall()
        return [str(row["hashtag"]) for row in rows]

    def mark_hashtag_scraped(self, hashtag: str, *, niche: str) -> None:
        tag = hashtag.removeprefix("#").strip().lower()
        self.connection.execute(
            "UPDATE discovered_hashtags SET last_scraped_at = CURRENT_TIMESTAMP WHERE hashtag = ? AND niche = ?",
            (tag, niche),
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
