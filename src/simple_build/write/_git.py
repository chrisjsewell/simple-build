"""A simple git interface."""

import os
from pathlib import Path, PurePosixPath
import subprocess


class GitError(Exception):
    """Raised a call to git fails."""


class NotGitRepositoryError(GitError):
    """Raised when the current directory is not in a git repository."""


class GitNotFoundError(GitError):
    """Raised when the git executable is not found."""


def find_git_root(path: Path | str) -> Path | None:
    """Find the root of a git repository.

    This function does not require git to be installed.

    :returns: The root of the git repository, or None if not in a git repository.
    """
    path = Path(path)
    while not (path / ".git").is_dir():
        # If we reach the root directory, we are not in a Git repository
        if path == path.parent:
            return None
        path = path.parent
    return path


class GitFolder:
    """A simple interface to a folder within a git repository."""

    def __init__(self, cwd: Path | str, git_exec: str = "git"):
        """Initialize the git repository.

        :param cwd: The current working directory.
        """
        cwd = Path(cwd).resolve()
        self._cwd = cwd
        root = find_git_root(cwd)
        if root is None:
            raise NotGitRepositoryError(
                f"Unable to find git repository root from: {cwd}"
            )
        self._root = root
        self._git_exec = git_exec

    @property
    def root(self) -> Path:
        """The root of the git repository."""
        return self._root

    @property
    def cwd(self) -> Path:
        """The current working directory."""
        return self._cwd

    def __repr__(self) -> str:
        return f"GitFolder({str(self.cwd)!r})"

    def run_git(self, *args: str) -> bytes:
        """Run a git command in the CWD.

        :param args: The arguments to pass to git.

        :raises GitNotFoundError: If git executable not found.
        :raises GitError: If git fails.
        """
        command = [self._git_exec, *args]
        try:
            outb = subprocess.check_output(
                command, cwd=str(self.cwd), stderr=subprocess.PIPE
            )
        except FileNotFoundError as err:
            raise GitNotFoundError() from err
        except subprocess.CalledProcessError as err:
            raise GitError(
                f"git command {' '.join(command)!r} failed in {self.cwd}: {err.stderr.decode()}"
            ) from err
        return outb

    def tracked_files(self) -> set[Path]:
        """Return all files tracked files in the CWD."""
        outb = self.run_git("ls-files", "--recurse-submodules", "-z")
        return {
            self.cwd / PurePosixPath(os.fsdecode(loc))
            for loc in outb.strip(b"\0").split(b"\0")
            if loc
        }

    def new_files(self) -> set[Path]:
        """Return all files that are not tracked by git."""
        outb = self.run_git("ls-files", "--others", "--exclude-standard", "-z")
        return {
            self.cwd / PurePosixPath(os.fsdecode(loc))
            for loc in outb.strip(b"\0").split(b"\0")
            if loc
        }

    def removed_files(self) -> set[Path]:
        """Return all files that are not tracked by git."""
        outb = self.run_git("ls-files", "--deleted", "-z")
        return {
            self.cwd / PurePosixPath(os.fsdecode(loc))
            for loc in outb.strip(b"\0").split(b"\0")
            if loc
        }
