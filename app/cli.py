"""Flask CLI commands (run with `flask --app wsgi <command>`)."""

from __future__ import annotations

from datetime import timedelta

import click
from flask import Flask, current_app
from sqlalchemy import delete

from app.db import Database
from app.models import Interview, utcnow


def register_cli(app: Flask) -> None:
    @app.cli.command("purge-old")
    @click.option("--days", type=int, default=None, help="Defaults to RETENTION_DAYS.")
    def purge_old(days: int | None) -> None:
        """Delete interviews (and transcripts) older than the retention window."""
        days = days if days is not None else current_app.extensions["settings"].retention_days
        database: Database = current_app.extensions["database"]
        cutoff = utcnow() - timedelta(days=days)
        with database.session() as s:
            result = s.execute(delete(Interview).where(Interview.created_at < cutoff))
            s.commit()
        click.echo(f"Deleted {result.rowcount} interviews older than {days} days")
