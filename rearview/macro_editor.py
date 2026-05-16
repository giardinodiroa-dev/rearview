from __future__ import annotations

from rearview.macro_store import Macro, MacroStep, save_macro, load_macro
from rich.console import Console
from rich.table import Table
from rich.text import Text
from rich.panel import Panel
from rich.prompt import Prompt


def format_action(step: MacroStep) -> str:
    """Build the Action column string for a step."""
    semantic = step.semantic
    raw = step.raw

    if semantic:
        text = semantic.get("text", "")
        selector = semantic.get("selector", "")
        if text:
            label = f"click '{text}' {selector}"
            if len(label) > 30:
                label = label[:27] + "..."
            return label

    raw_type = raw.get("type", "?")

    if raw_type == "click":
        x = raw.get("x", "?")
        y = raw.get("y", "?")
        return f"click ({x}, {y})"

    if raw_type == "key":
        key = raw.get("key", "?")
        return f"key: {key}"

    if raw_type == "scroll":
        x = raw.get("x", "?")
        y = raw.get("y", "?")
        delta_y = raw.get("delta_y", 0)
        direction = "↓" if delta_y < 0 else "↑"
        return f"scroll {direction} ({x}, {y})"

    return f"{raw_type} ..."


def _build_table(macro: Macro) -> Table:
    """Build the Rich table for displaying macro steps."""
    table = Table(
        show_header=True,
        header_style="bold",
        box=None,
        border_style="dim",
        show_edge=True,
    )

    table.add_column("#", style="dim", width=4, justify="right")
    table.add_column("t", width=8, justify="right")
    table.add_column("Action", width=32)
    table.add_column("Label", width=24)

    for i, step in enumerate(macro.steps, 1):
        action_str = format_action(step)
        if step.label:
            label_text = Text(step.label, style="bold green")
        else:
            label_text = Text("", style="dim")
        table.add_row(
            str(i),
            f"{step.t:.2f}s",
            action_str,
            label_text,
        )

    return table


def _display(macro: Macro, console: Console) -> None:
    """Print the panel header and steps table."""
    header = (
        f"Macro: [bold]{macro.name}[/bold]  "
        f"│  {len(macro.steps)} steps  "
        f"│  target: [cyan]{macro.target_hint}[/cyan]"
    )
    console.print(Panel(header, expand=False))
    console.print(_build_table(macro))
    console.print(
        "\n[dim]Commands:[/dim] "
        "[yellow][number][/yellow] to edit label  "
        "[yellow][d number][/yellow] to delete step  "
        "[yellow][p][/yellow] preview  "
        "[yellow][s][/yellow] save  "
        "[yellow][q][/yellow] quit\n"
    )


def preview_macro(macro: Macro, console: Console) -> None:
    """Print only the labeled steps — what the toast will show."""
    labeled = [(step.t, step.label) for step in macro.steps if step.label]
    if not labeled:
        console.print("[dim]No labeled steps.[/dim]")
        return
    for t, label in labeled:
        console.print(f"  {t:.2f}s  [bold #6366f1]●[/]  {label}")


def edit_macro(macro: Macro, console: Console | None = None) -> Macro:
    """Interactive editor loop. Returns the (possibly modified) macro."""
    if console is None:
        console = Console()

    while True:
        _display(macro, console)

        try:
            raw_input = input("Command: ").strip()
        except (EOFError, KeyboardInterrupt):
            console.print()
            break

        if not raw_input:
            continue

        # --- delete step: d N ---
        if raw_input.lower().startswith("d "):
            rest = raw_input[2:].strip()
            if not rest.isdigit():
                console.print("[red]Usage: d <number>[/red]")
                continue
            idx = int(rest)
            if idx < 1 or idx > len(macro.steps):
                console.print(f"[red]Step {idx} out of range.[/red]")
                continue
            confirm = input(f"Delete step {idx}? [y/N]: ").strip().lower()
            if confirm == "y":
                macro.steps.pop(idx - 1)
                console.print(f"[dim]Step {idx} deleted.[/dim]")
            else:
                console.print("[dim]Cancelled.[/dim]")
            continue

        # --- preview ---
        if raw_input.lower() == "p":
            preview_macro(macro, console)
            continue

        # --- save ---
        if raw_input.lower() == "s":
            save_macro(macro)
            console.print("[green]Saved.[/green]")
            return macro

        # --- quit ---
        if raw_input.lower() == "q":
            answer = input("Save before quitting? [Y/n]: ").strip().lower()
            if answer != "n":
                save_macro(macro)
                console.print("[green]Saved.[/green]")
            return macro

        # --- edit label: N ---
        if raw_input.isdigit():
            idx = int(raw_input)
            if idx < 1 or idx > len(macro.steps):
                console.print(f"[red]Step {idx} out of range.[/red]")
                continue
            new_label = input(f"Label for step {idx} (blank=silent): ").strip()
            macro.steps[idx - 1].label = new_label
            continue

        console.print("[red]Unknown command.[/red]")

    return macro
