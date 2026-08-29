"""Renderizadores de saida do monitor."""
from shelf_monitor.report.dashboard import render_dashboard
from shelf_monitor.report.digest import render_digest, render_html, render_markdown

__all__ = ["render_dashboard", "render_digest", "render_html", "render_markdown"]
