"""CLI entry point (commands added in later steps)."""

import typer

app = typer.Typer(no_args_is_help=True)


@app.callback()
def _main() -> None:
    """Agentic PR review POC."""
