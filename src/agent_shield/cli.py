import typer

app = typer.Typer(no_args_is_help=True)


@app.command()
def version():
    """Print the agent-shield version."""
    from agent_shield import __version__

    typer.echo(f"agent-shield v{__version__}")


@app.command()
def init():
    """Generate a template agent-shield.yaml in the current directory."""
    typer.echo("Not yet implemented.")
    raise typer.Exit(code=1)


@app.command()
def run(
    config: str = typer.Option(None, help="Path to config file"),
    ci: bool = typer.Option(False, help="Exit with code 1 if below threshold"),
    verbose: bool = typer.Option(False, help="Show full responses"),
):
    """Run all tests from agent-shield.yaml."""
    typer.echo("Not yet implemented.")
    raise typer.Exit(code=1)


if __name__ == "__main__":
    app()
