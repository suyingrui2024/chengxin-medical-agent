"""Read-only artifact access scoped to one pinned workspace identity.

The service has no session, model credentials or process reference. It owns
directory traversal and previews; callers retain ownership of session status
and translate a failed root check into their own storage error presentation.
"""

from dataclasses import dataclass, field
import hashlib
from pathlib import Path
import stat

from ..infra.platform_fs import os
from .artifacts import IMAGE_LIMIT, projection, read_limit
from .errors import ExecutionError
from .files import DirectoryChanged, directory_page
from .workspace_files import file_revision, open_workspace_directory, safe_bytes, safe_file


@dataclass
class _ArtifactScan:
    entries: list = field(default_factory=list)
    visited: int = 0
    reasons: set = field(default_factory=set)

    def walk(self, directory, prefix="", depth=0):
        """Bound a metadata-only scan and record every incomplete traversal.

        A child is opened relative to the pinned parent, never through a link.
        Entry/depth/output ceilings match the existing UI contract; skipped
        caches and hidden files are outside that contract's visible scope.
        """
        if depth > 8:
            self.reasons.add("depth_limit")
            return
        if self.visited >= 1000 or len(self.entries) >= 200:
            self.reasons.add("entry_limit")
            return
        with os.scandir(directory) as entries:
            for entry in entries:
                self.visited += 1
                if self.visited > 1000 or len(self.entries) >= 200:
                    self.reasons.add("entry_limit")
                    return
                self._visit(entry, directory, prefix, depth)

    def _visit(self, entry, directory, prefix, depth):
        """Inspect a visible entry without reading contents or following links."""
        if entry.name.startswith(".") or entry.name in {"node_modules", "__pycache__"}:
            return
        try:
            entry.name.encode("utf-8")
        except UnicodeEncodeError:
            self.reasons.add("unsupported_name")
            return
        try:
            info = entry.stat(follow_symlinks=False)
            name = prefix + entry.name
            if stat.S_ISREG(info.st_mode):
                self.entries.append(
                    {
                        "path": name,
                        "size": info.st_size,
                        "revision": file_revision(info),
                    }
                )
            elif stat.S_ISDIR(info.st_mode):
                child = os.open(
                    entry.name,
                    os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                    dir_fd=directory,
                )
                try:
                    self.walk(child, name + "/", depth + 1)
                finally:
                    os.close(child)
        except OSError:
            self.reasons.add("unreadable_entry")


@dataclass(frozen=True)
class WorkspaceArtifactAccess:
    directory: Path
    directory_identity: tuple[int, int]

    def overview(self):
        """Return visible artifact metadata, or raise when the root changed.

        Scan failures below the root produce an explicitly partial result.
        Root failures are owned by the caller, so this service cannot mutate
        session state, erase a diagnostic, or start/stop an execution worker.
        """
        directory = open_workspace_directory(self.directory, self.directory_identity)
        scan = _ArtifactScan()
        try:
            try:
                scan.walk(directory)
            except OSError:
                scan.reasons.add("unreadable_directory")
        finally:
            os.close(directory)
        return {
            "entries": sorted(scan.entries, key=lambda item: item["path"]),
            "scan": {
                "performed": True,
                "complete": not scan.reasons,
                "reasons": sorted(scan.reasons),
                "visited_entries": scan.visited,
                "scope": "visible regular files only; links and common caches excluded",
            },
        }

    def list_directory(self, path="", cursor=None, limit=100, include_hidden=False):
        root = open_workspace_directory(self.directory, self.directory_identity)
        try:
            return directory_page(root, path, cursor, limit, include_hidden)
        except DirectoryChanged:
            raise
        except (OSError, ValueError) as error:
            raise ExecutionError(str(error)) from None
        finally:
            os.close(root)

    def read_file(self, relative):
        return safe_file(self.directory, relative, root_identity=self.directory_identity)

    def preview_file(self, relative):
        data, revision = safe_bytes(
            self.directory,
            relative,
            read_limit(relative),
            self.directory_identity,
        )
        return self._project(relative, data, revision)

    def image_file(self, relative, expected_sha256):
        data, revision = safe_bytes(
            self.directory,
            relative,
            IMAGE_LIMIT,
            self.directory_identity,
        )
        preview = self._project(relative, data, revision)
        if preview["kind"] != "image" or preview["sha256"] != expected_sha256:
            raise ExecutionError("图像已变化或不是支持的图片，请重新读取")
        return data, preview["mime"]

    def artifact_status(self, relative, expected_sha256):
        if (
            not isinstance(expected_sha256, str)
            or len(expected_sha256) != 64
            or any(c not in "0123456789abcdef" for c in expected_sha256)
        ):
            raise ExecutionError("文件摘要无效")
        data, _ = safe_bytes(
            self.directory,
            relative,
            read_limit(relative),
            self.directory_identity,
        )
        return {
            "schema_version": "artifact-freshness/v1",
            "path": relative,
            "matches": hashlib.sha256(data).hexdigest() == expected_sha256,
        }

    @staticmethod
    def _project(relative, data, revision):
        try:
            return projection(relative, data, revision)
        except ValueError as error:
            raise ExecutionError(str(error)) from None
