"""SQLite 用户设置存储。"""
import json
from pathlib import Path
from typing import Any, Dict, Union

from .sqlite_database import SQLiteDatabase


class UserSettingsStore:
    """按用户名保存可热更新设置。"""

    def __init__(self, database_path: Union[str, Path]):
        self.database = SQLiteDatabase(database_path)

    def load_all(self) -> Dict[str, Dict[str, Any]]:
        if not self.database.path.exists():
            return {}

        settings: Dict[str, Dict[str, Any]] = {}
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT username, setting_key, value_json FROM user_settings ORDER BY username, setting_key"
            ).fetchall()
        for row in rows:
            settings.setdefault(row["username"], {})[row["setting_key"]] = json.loads(row["value_json"])
        return settings

    def update(self, username: str, values: Dict[str, Any]) -> None:
        if not values:
            return
        rows = [
            (username, key, json.dumps(value, ensure_ascii=False))
            for key, value in values.items()
        ]
        with self.database.connect() as connection:
            connection.executemany(
                """
                INSERT INTO user_settings(username, setting_key, value_json)
                VALUES (?, ?, ?)
                ON CONFLICT(username, setting_key)
                DO UPDATE SET value_json = excluded.value_json
                """,
                rows,
            )
