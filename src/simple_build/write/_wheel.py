"""Wheel file writing.

See: https://peps.python.org/pep-0427/
"""

from base64 import urlsafe_b64encode
from dataclasses import dataclass
from datetime import datetime
import hashlib
import os
from pathlib import Path
import re
import shutil
from tempfile import TemporaryDirectory
from textwrap import dedent
from types import TracebackType
import typing as t
import zipfile

from packaging.version import Version

from ..analyse import PackageAnalysis
from ._files import gather_files, normalize_file_permissions
from ._metadata import create_entrypoints, create_metadata


def write_wheel(  # noqa: PLR0912
    writer: "WheelWriterProtocol", package: PackageAnalysis, *, editable: bool = False
) -> None:
    """Write a wheel.

    :param editable: Whether to build an editable wheel.
    :param pre_write_hooks: A sequence of functions to call before writing the files into the wheel.
        (not called for editable wheels)
    """

    # write the python modules
    if editable:
        # Note, another way to do this is to use the editables hook,
        # https://peps.python.org/pep-0660/#what-to-put-in-the-wheel.
        # However, although more precise, it is not supported in IDEs like VS Code
        # (for auto-completion, etc.), so we use the simpler method.
        pth_name = package.snake_name + ".pth"
        paths = {path.absolute().parent for path in package.modules.values()}
        writer.write_text([pth_name], "\n".join(str(path) for path in paths))
    else:
        for mod_name, module in package.modules.items():
            file_paths: t.Iterable[Path] = []

            if module.is_dir():
                # Note, as per https://peps.python.org/pep-0517/#build-wheel
                # > To ensure that wheels from different sources are built the same way,
                # > frontends may call build_sdist first,
                # > and then call build_wheel in the unpacked sdist
                # in this case we must allow for the folder to not be a git repo.

                # TODO config options to include/exclude files in wheel modules,
                # but they must allow for the behaviour above.

                file_paths = gather_files(module, allow_non_git=True)
            elif module.is_file():
                file_paths = [module]
            else:
                raise FileNotFoundError(module)

            pre_write_hooks = package.tool.get("pre_write_hooks", [])
            if not pre_write_hooks:
                for file_path in file_paths:
                    rel_path = file_path.relative_to(module.parent).parts
                    writer.write_path(rel_path, file_path)
            else:
                # if there are pre-write hooks, we must write the files to a temporary directory
                # and then call the hooks before writing the files to the wheel
                with TemporaryDirectory() as tmpdir:
                    tmp = Path(tmpdir)
                    for file_path in file_paths:
                        rel_path = file_path.relative_to(module.parent).parts
                        dest = tmp.joinpath(*rel_path)
                        dest.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copyfile(file_path, dest)
                    for hook in pre_write_hooks:
                        hook("wheel", tmp, mod_name, package)
                    for file_path in tmp.rglob("*"):
                        if file_path.is_file():
                            rel_path = file_path.relative_to(tmp).parts
                            writer.write_path(rel_path, file_path)

    # write the dist_info (note this is recommended to be last in the file)
    write_wheel_metadata(writer, package)


def write_wheel_metadata(
    writer: "WheelWriterProtocol", package: PackageAnalysis
) -> None:
    """Write the wheel metadata."""
    dist_info = writer.metadata.dist_info
    # write license files, note, there is currently no standard for this,
    # but it will likely be added in: https://peps.python.org/pep-0639
    for license_file in package.project["licenses"]:
        if "path" in license_file:
            license_text = package.root.joinpath(license_file["path"]).read_text(
                "utf-8"
            )
            license_path = (dist_info, "licenses", *license_file["path"].parts)
            writer.write_text(license_path, license_text)
    writer.write_text((dist_info, "WHEEL"), writer.metadata.text)
    metadata_text = create_metadata(package.project, package.root)
    writer.write_text((dist_info, "METADATA"), metadata_text)
    entrypoint_text = create_entrypoints(package.project)
    if entrypoint_text:
        writer.write_text((dist_info, "entry_points.txt"), entrypoint_text)


@dataclass
class Record:
    """A RECORD file entry.

    See:
    https://packaging.python.org/en/latest/specifications/recording-installed-packages/#the-record-file
    """

    path: str
    """Absolute, or relative to the directory containing the .dist-info directory."""
    hash: str
    """The name of a hash algorithm from hashlib.algorithms_guaranteed,
    followed by the equals character = and the digest of the file's contents,
    encoded with the urlsafe-base64-nopad encoding.
    """
    size: int
    """File size in bytes, as a base 10 integer."""


@dataclass
class WheelMetadata:
    """Wheel metadata

    See: https://packaging.python.org/en/latest/specifications/binary-distribution-format/#file-format

    Note, this supersedes https://peps.python.org/pep-0427/#file-format
    """

    name: str
    version: Version
    generator: str
    python: str = "py3"
    abi: str = "none"
    arch: str = "any"
    build: str | None = None
    purelib: bool = True

    def __post_init__(self) -> None:
        """Post init."""
        # see https://packaging.python.org/en/latest/specifications/binary-distribution-format/#file-name-convention
        norm_name = re.sub(r"[-_.]+", "_", self.name).lower()
        self._file_name = f"{norm_name}-{self.version}"
        if self.build:
            self._file_name += f"-{self.build}"
        self._file_name += f"-{self.python}-{self.abi}-{self.arch}.whl"
        self._dist_info = f"{norm_name}-{self.version}.dist-info"
        self._tags = []
        for x in self.python.split("."):
            for y in self.abi.split("."):
                for z in self.arch.split("."):
                    self._tags.append("-".join((x, y, z)))

    @property
    def file_name(self) -> str:
        """Get the file name."""
        return self._file_name

    @property
    def dist_info(self) -> str:
        """Get the dist info directory name."""
        return self._dist_info

    @property
    def tags(self) -> list[str]:
        """Compatibility tags, implementing https://www.python.org/dev/peps/pep-0425/."""
        return self._tags

    @property
    def text(self) -> str:
        """Return the text to place in the METADATA file."""
        content = dedent(
            f"""\
        Wheel-Version: 1.0
        Generator: {self.generator}
        Root-Is-Purelib: {'true' if self.purelib else 'false'}
        """
        )
        for tag in self.tags:
            content += f"Tag: {tag}"
        if self.build:
            content += f"Build: {self.build}"
        return content + "\n"


class WheelWriterProtocol(t.Protocol):
    @property
    def metadata(self) -> WheelMetadata:
        """Return the metadata."""

    def write_text(self, path: t.Sequence[str], text: str) -> None:
        """Write text to the wheel (with utf-8 encoding).

        :param path: The path to write to in the wheel.
        :param text: The text to write.
        """

    def write_path(self, path: t.Sequence[str], source: Path) -> None:
        """Write an external path to the wheel.

        :param path: The path to write to in the wheel.
        :param source: The path to write from.
        """


class WheelFolderWriter:
    """A wheel writer, for writing to a folder.

    This is for use by `prepare_metadata_for_build_wheel`
    """

    def __init__(self, directory: Path, metadata: WheelMetadata) -> None:
        """Initialize.

        :param directory: The directory to write to.
        :param metadata: The distribution name.
        """
        self._metadata = metadata
        self._path = directory
        self._path.mkdir(parents=True, exist_ok=True)

    @property
    def path(self) -> Path:
        """Return the path to the folder."""
        return self._path

    @property
    def metadata(self) -> WheelMetadata:
        """Return the metadata."""
        return self._metadata

    def write_text(self, path: t.Sequence[str], text: str) -> None:
        """Write text to the wheel (with utf-8 encoding).

        :param path: The path to write to in the wheel.
        :param text: The text to write.
        """
        file = self._path.joinpath(*path)
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(text, "utf-8")

    def write_path(self, path: t.Sequence[str], source: Path) -> None:
        """Write an external path to the wheel.

        :param path: The path to write to in the wheel.
        :param source: The path to write from.
        """
        file = self._path.joinpath(*path)
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_bytes(source.read_bytes())


# note, this can be replaced by Self in python 3.11
SelfType = t.TypeVar("SelfType", bound="WheelZipWriter")


class WheelZipWriter:
    """A wheel writer, implementing https://peps.python.org/pep-0427/.

    Should be used as a context manager.
    """

    def __init__(self, directory: Path, metadata: WheelMetadata) -> None:
        """Initialize.

        :param directory: The directory to write to.
        :param metadata: The distribution name.
        """
        self._metadata = metadata
        self._path = directory.joinpath(metadata.file_name)
        self._zip: zipfile.ZipFile | None = None
        self._records: list[Record] = []
        self._fixed_time_stamp = zip_timestamp_from_env()

    @staticmethod
    def raise_not_open() -> t.NoReturn:
        """Assert that the zip file is open."""
        raise OSError("Wheel file is not open.")

    def __enter__(self: SelfType) -> SelfType:
        """Enter the context manager."""
        self._zip = zipfile.ZipFile(self._path, "w", compression=zipfile.ZIP_DEFLATED)
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        """Exit the context manager."""
        if self._zip is None:
            self.raise_not_open()

        # write the RECORD file
        if not exc_type:
            record_text = ""
            for record in self.records:
                record_text += f"{record.path},{record.hash},{record.size}\n"
            # no hash or size for the RECORD file itself
            record_text += f"{self._metadata.dist_info}/RECORD,,\n"
            self.write_text((self._metadata.dist_info, "RECORD"), record_text)
            self._records.pop()

        self._zip.close()
        self._zip = None

    @property
    def metadata(self) -> WheelMetadata:
        """Return the metadata."""
        return self._metadata

    @property
    def path(self) -> Path:
        """Return the path to the wheel."""
        return self._path

    @property
    def records(self) -> list[Record]:
        """Return the records of written files."""
        return self._records

    # methods that require an open zip file

    def list_files(self) -> list[str]:
        """Return a list of files in the wheel."""
        if self._zip is None:
            self.raise_not_open()
        return self._zip.namelist()

    def write_text(self, path: t.Sequence[str], text: str) -> None:
        """Write text to the wheel (with utf-8 encoding).

        :param path: The path to write to in the wheel.
        :param text: The text to write.
        """
        if self._zip is None:
            self.raise_not_open()
        content = text.encode("utf-8")
        hashsum = hashlib.sha256(content)
        time_stamp = self._fixed_time_stamp or (2016, 1, 1, 0, 0, 0)
        zinfo = zipfile.ZipInfo("/".join(path), time_stamp)
        zinfo.external_attr = 0o644 << 16
        self._zip.writestr(zinfo, content, compress_type=zipfile.ZIP_DEFLATED)
        self._records.append(Record("/".join(path), encode_hash(hashsum), len(content)))

    def write_path(self, path: t.Sequence[str], source: Path) -> None:
        """Write an external path to the wheel.

        :param path: The path to write to in the wheel.
        :param source: The path to write from.
        """
        if self._zip is None:
            self.raise_not_open()

        rel_path = "/".join(path)
        if self._fixed_time_stamp is None:
            zinfo = zipfile.ZipInfo.from_file(source, rel_path)
        else:
            zinfo = zipfile.ZipInfo(rel_path, self._fixed_time_stamp)
        zinfo.compress_type = zipfile.ZIP_DEFLATED
        # Normalize permission bits to either 755 (executable) or 644
        st_mode = source.stat().st_mode
        new_mode = normalize_file_permissions(st_mode)
        zinfo.external_attr = (new_mode & 0xFFFF) << 16
        # stream the file content whilst computing the hash
        # to avoid loading the whole file into memory
        hashsum = hashlib.sha256()
        with source.open("rb") as src, self._zip.open(zinfo, "w") as dest:
            while True:
                buf = src.read(1024 * 8)
                if not buf:
                    break
                hashsum.update(buf)
                dest.write(buf)

        self._records.append(
            Record("/".join(path), encode_hash(hashsum), source.stat().st_size)
        )


def zip_timestamp_from_env() -> tuple[int, int, int, int, int, int] | None:
    """Prepare a timestamp from $SOURCE_DATE_EPOCH, if set.

    This allows for a fixed timestamp rather than the current time, so
    that building a wheel twice on the same computer can automatically
    give you the exact same result.
    """
    try:
        # If SOURCE_DATE_EPOCH is set (e.g. by Debian), it's used for
        # timestamps inside the zip file.
        d = datetime.utcfromtimestamp(int(os.environ["SOURCE_DATE_EPOCH"]))
    except (KeyError, ValueError):
        # Otherwise, we'll use the mtime of files, and generated files will
        # default to 2016-1-1 00:00:00
        return None

    if d.year >= 1980:  # noqa: PLR2004
        # zipfile expects a 6-tuple, not a datetime object
        return d.year, d.month, d.day, d.hour, d.minute, d.second
    else:
        return 1980, 1, 1, 0, 0, 0


def encode_hash(hashsum: t.Any) -> str:  # type: ignore[misc]
    """Encode a hash."""
    hash_digest = urlsafe_b64encode(hashsum.digest()).decode("ascii").rstrip("=")
    return f"{hashsum.name}={hash_digest}"
