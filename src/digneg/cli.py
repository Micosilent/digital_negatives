"""Command-line interface and web server launcher for DigNeg."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import click

from digneg.processor import convert_image_file
from digneg.profile import INK_MODES, CalibrationProfile
from digneg.web import run_web_server
from digneg.wedge import save_step_wedge


DEFAULT_PROFILES_DIR = Path.cwd() / "profiles"


@click.group(invoke_without_command=True)
@click.pass_context
def cli(ctx: click.Context) -> None:
    """Digital Negative & Step Wedge Profiler for Silver Gelatin Contact Printing.

    Run `digneg web` (or `digneg` with no subcommand) to launch the interactive web UI.
    """
    if ctx.invoked_subcommand is None:
        ctx.invoke(web)


@cli.command()
@click.option("--host", default="0.0.0.0", show_default=True, help="Bind address (0.0.0.0 for external access).")
@click.option("--port", default=8080, show_default=True, type=int, help="Port number (or set PORT env var).")
@click.option(
    "--open-browser/--no-browser",
    default=False,
    show_default=True,
    help="Open the web UI in a local browser on startup.",
)
def web(host: str, port: int, open_browser: bool) -> None:
    """Launch the stateless darkroom web interface (profiles stored in browser localStorage)."""
    run_web_server(host=host, port=port, open_browser=open_browser)


@cli.command()
@click.option("-o", "--output", default="step_wedge_21.tiff", show_default=True, type=click.Path(path_type=Path))
@click.option("-s", "--steps", default=21, show_default=True, type=int, help="Number of wedge steps.")
@click.option("--width", default=8.0, show_default=True, type=float, help="Width in inches.")
@click.option("--height", default=2.5, show_default=True, type=float, help="Height in inches.")
@click.option("--dpi", default=300, show_default=True, type=int, help="Output resolution in DPI.")
@click.option(
    "--ink-mode",
    default="pyro_amber",
    show_default=True,
    type=click.Choice(list(INK_MODES.keys())),
    help="Spectral ink density mode.",
)
@click.option("--flip/--no-flip", default=True, show_default=True, help="Flip horizontally for emulsion contact.")
@click.option("--profile", default=None, type=click.Path(exists=True, path_type=Path), help="Optional calibration profile JSON.")
@click.option("--compare-inks", is_flag=True, help="Include 3-mode ink comparison strip at bottom.")
def wedge(
    output: Path,
    steps: int,
    width: float,
    height: float,
    dpi: int,
    ink_mode: str,
    flip: bool,
    profile: Optional[Path],
    compare_inks: bool,
) -> None:
    """Generate a 16-bit TIFF calibration step wedge."""
    prof = CalibrationProfile.load(profile) if profile else None
    saved = save_step_wedge(
        output_path=output,
        steps=steps,
        width_inches=width,
        height_inches=height,
        dpi=dpi,
        ink_mode=ink_mode,
        flip_horizontal=flip,
        profile=prof,
        include_ink_comparison=compare_inks,
    )
    click.echo(f"Saved {steps}-step 16-bit wedge to {saved}")


@cli.command()
@click.option("--name", required=True, help="Profile name (e.g. ilford_mg_rc).")
@click.option("--steps", default=21, show_default=True, type=int, help="Total steps on the test wedge.")
@click.option("--black-step", required=True, type=float, help="Highest step number that prints Dmax pure black.")
@click.option("--white-step", required=True, type=float, help="Lowest step number that prints Dmin paper white.")
@click.option("--midtone-step", default=None, type=float, help="Optional step number that prints 50% middle gray.")
@click.option("--gamma", default=1.0, show_default=True, type=float, help="Midtone contrast gamma.")
@click.option(
    "--ink-mode",
    default="pyro_amber",
    show_default=True,
    type=click.Choice(list(INK_MODES.keys())),
)
@click.option("--flip/--no-flip", default=True, show_default=True)
@click.option("--notes", default="", help="Darkroom exposure and printer notes.")
@click.option("-o", "--output", default=None, type=click.Path(path_type=Path), help="Output JSON path.")
def calibrate(
    name: str,
    steps: int,
    black_step: float,
    white_step: float,
    midtone_step: Optional[float],
    gamma: float,
    ink_mode: str,
    flip: bool,
    notes: str,
    output: Optional[Path],
) -> None:
    """Create and save a step-wedge calibration profile JSON."""
    prof = CalibrationProfile(
        name=name,
        steps_total=steps,
        black_step=black_step,
        white_step=white_step,
        midtone_step=midtone_step,
        gamma=gamma,
        ink_mode=ink_mode,
        flip_horizontal=flip,
        notes=notes,
    )
    out_path = output or (DEFAULT_PROFILES_DIR / f"{name}.json")
    prof.save(out_path)
    click.echo(
        f"Saved profile '{name}' to {out_path} "
        f"(Black floor: {prof.d_min_fraction*100:.1f}% ink, White ceiling: {prof.d_max_fraction*100:.1f}% ink)"
    )


@cli.command()
@click.argument("input_image", type=click.Path(exists=True, path_type=Path))
@click.option("-o", "--output", default=None, type=click.Path(path_type=Path), help="Output 16-bit TIFF path.")
@click.option(
    "-p",
    "--profile",
    default=str(DEFAULT_PROFILES_DIR / "ilford_mg_rc_default.json"),
    show_default=True,
    type=click.Path(exists=True, path_type=Path),
    help="Calibration profile JSON file.",
)
@click.option("--ink-mode", default=None, type=click.Choice(list(INK_MODES.keys())), help="Override profile ink mode.")
@click.option("--flip/--no-flip", default=None, help="Override horizontal flip.")
@click.option("--dpi", default=None, type=float, help="Override output TIFF DPI.")
def convert(
    input_image: Path,
    output: Optional[Path],
    profile: Path,
    ink_mode: Optional[str],
    flip: Optional[bool],
    dpi: Optional[float],
) -> None:
    """Convert a positive image into a calibrated 16-bit digital negative TIFF."""
    prof = CalibrationProfile.load(profile)
    if ink_mode:
        prof.ink_mode = ink_mode
    out_path = output or input_image.with_name(f"{input_image.stem}_negative_16bit.tiff")
    saved = convert_image_file(
        input_path=input_image,
        output_path=out_path,
        profile=prof,
        override_flip=flip,
        override_dpi=dpi,
    )
    click.echo(f"Saved calibrated 16-bit digital negative to {saved}")


if __name__ == "__main__":
    cli()
