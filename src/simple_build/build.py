"""The main entry module for the PyMonorepo build API."""

from pathlib import Path
import typing as t

from . import __name__, __version__
from .analyse import analyse_package
from .write import (
    SdistWriter,
    WheelFolderWriter,
    WheelMetadata,
    WheelZipWriter,
    write_sdist,
    write_wheel,
    write_wheel_metadata,
)


def build_wheel_metadata(root: Path, metadata_directory: Path) -> WheelFolderWriter:
    """Prepare the metadata for a wheel build.

    :param root: The root of the project.
    :param metadata_directory: The directory in which to place the .dist-info directory.
    """
    analysis = analyse_package(root)
    metadata = WheelMetadata(
        analysis.snake_name,
        analysis.project["version"],
        f"{__name__} {__version__}",
    )
    writer = WheelFolderWriter(metadata_directory, metadata)
    write_wheel_metadata(writer, analysis)
    return writer


def build_wheel(
    root: Path,
    wheel_directory: Path,
    metadata_directory: Path | None,
    *,
    editable: bool = False,
) -> WheelZipWriter:
    """Build a .whl file, and place it in the specified wheel_directory.

    :param root: The root of the project.
    :param wheel_directory: The directory in which to place the .whl file.
    :param metadata_directory: The directory containing the .dist-info directory.
        This may have been previously created by calling `build_wheel_metadata`.
    :param editable: Whether to build an editable wheel.
    """
    # TODO use metadata_directory?
    analysis = analyse_package(root)
    metadata = WheelMetadata(
        analysis.snake_name,
        analysis.project["version"],
        f"{__name__} {__version__}",
    )
    with WheelZipWriter(wheel_directory, metadata) as writer:
        write_wheel(writer, analysis, editable=editable)
    return writer


def build_sdist(  # type: ignore[misc]
    root: Path, sdist_directory: Path, config_settings: dict[str, t.Any] | None = None
) -> SdistWriter:
    """Build a .tar.gz file, and place it in the specified sdist_directory.

    :param root: The root of the project.
    :param sdist_directory: The directory in which to place the .tar.gz file.
    :param config_settings: A dictionary of configuration settings.
    """
    analysis = analyse_package(root)
    with SdistWriter(
        sdist_directory, analysis.snake_name, str(analysis.project["version"])
    ) as writer:
        write_sdist(writer, analysis)
    return writer
