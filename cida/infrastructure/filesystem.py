import os
import tempfile
import shutil
from typing import List

class PhysicalFilesystem:
    """Concrete implementation of filesystem repository."""

    def __init__(self, durable: bool = False):
        self.durable = durable

    def read_text(self, filepath: str, encoding: str = "utf-8") -> str:
        try:
            with open(filepath, 'r', encoding=encoding, errors='strict', newline='') as f:
                return f.read()
        except UnicodeDecodeError as e:
            from cida.domain.errors import EncodingValidationError
            raise EncodingValidationError(f"Invalid {encoding} encoding in file {filepath}: {e}") from e

    def read_bytes(self, filepath: str) -> bytes:
        with open(filepath, 'rb') as f:
            return f.read()

    def write_text(self, filepath: str, content: str, encoding: str = "utf-8", durable: bool = False) -> None:
        abs_path = os.path.abspath(filepath)
        dir_name = os.path.dirname(abs_path)
        os.makedirs(dir_name, exist_ok=True)
        content_lf = content.replace('\r\n', '\n')
        fd, tmp_path = tempfile.mkstemp(dir=dir_name, prefix=".tmp-")
        try:
            with os.fdopen(fd, 'w', encoding=encoding, newline='\n') as f:
                f.write(content_lf)
                f.flush()
                if durable or self.durable:
                    os.fsync(f.fileno())
            os.replace(tmp_path, abs_path)
            if durable or self.durable:
                _sync_directory(dir_name)
        except Exception:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
            raise

    def write_bytes(self, filepath: str, content: bytes, durable: bool = False) -> None:
        abs_path = os.path.abspath(filepath)
        dir_name = os.path.dirname(abs_path)
        os.makedirs(dir_name, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(dir=dir_name, prefix=".tmp-")
        try:
            with os.fdopen(fd, 'wb') as f:
                f.write(content)
                f.flush()
                if durable or self.durable:
                    os.fsync(f.fileno())
            os.replace(tmp_path, abs_path)
            if durable or self.durable:
                _sync_directory(dir_name)
        except Exception:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
            raise

    def exists(self, path: str) -> bool:
        return os.path.exists(path)

    def is_file(self, path: str) -> bool:
        return os.path.isfile(path)

    def is_dir(self, path: str) -> bool:
        return os.path.isdir(path)

    def makedirs(self, path: str) -> None:
        os.makedirs(path, exist_ok=True)

    def copy(self, src: str, dst: str) -> None:
        os.makedirs(os.path.dirname(os.path.abspath(dst)), exist_ok=True)
        shutil.copy2(src, dst)

    def remove(self, path: str) -> None:
        if os.path.exists(path):
            os.remove(path)

    def is_binary_file(self, filepath: str) -> bool:
        from cida.domain.policies import is_binary_extension
        if is_binary_extension(filepath):
            return True
        try:
            with open(filepath, 'rb') as f:
                chunk = f.read(1024)
                if b'\0' in chunk:
                    return True
        except OSError:
            # Cannot read file — assume non-binary (caller handles read errors)
            return False
        return False

    def list_files(self, dir_path: str) -> List[str]:
        files_list = []
        for root, _, files in os.walk(dir_path):
            for f in files:
                files_list.append(os.path.join(root, f))
        return files_list

    def relpath(self, path: str, start: str) -> str:
        return os.path.relpath(path, start).replace('\\', '/')

    def abspath(self, path: str) -> str:
        return os.path.abspath(path)

    def basename(self, path: str) -> str:
        return os.path.basename(path)

    def dirname(self, path: str) -> str:
        return os.path.dirname(os.path.abspath(path))

    def join(self, *parts: str) -> str:
        return os.path.join(*parts)

    def list_dir(self, path: str) -> List[str]:
        if not os.path.exists(path):
            return []
        return os.listdir(path)


def _sync_directory(dir_path: str) -> None:
    """Sync directory entry on platforms that support O_DIRECTORY (POSIX).
    On Windows, this is a no-op since Windows does not require directory fsync
    and does not expose O_DIRECTORY in the standard library.
    """
    if not hasattr(os, 'O_DIRECTORY'):
        return
    try:
        dir_fd = os.open(dir_path, os.O_DIRECTORY)  # type: ignore[attr-defined]
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    except OSError:
        # Best-effort — directory sync is advisory; do not fail the write.
        pass


def validate_filesystem_safety(source: str, destination: str, report_path: str = "") -> None:
    from cida.domain.errors import SourcePathError

    src_abs = os.path.normcase(os.path.realpath(os.path.abspath(source)))
    dst_abs = os.path.normcase(os.path.realpath(os.path.abspath(destination)))

    if src_abs == dst_abs:
        raise SourcePathError(f"Destination path cannot be identical to source path: {src_abs}")

    try:
        common = os.path.normcase(os.path.commonpath([src_abs, dst_abs]))
    except ValueError:
        common = ""

    if common and common == src_abs:
        raise SourcePathError(f"Destination directory cannot be nested inside source directory: {dst_abs} inside {src_abs}")

    if common and common == dst_abs and os.path.isdir(dst_abs):
        raise SourcePathError(f"Source directory cannot be inside destination directory: {src_abs} inside {dst_abs}")

    if report_path:
        source_is_file = os.path.isfile(src_abs)
        output_collision_paths = set()
        if source_is_file:
            output_base = os.path.normcase(os.path.realpath(os.path.join(dst_abs, os.path.basename(src_abs))))
            output_collision_paths.update({
                output_base,
                output_base + ".tknc",
                output_base + ".cidatkn",
                output_base + ".tknc.cidatkn",
            })

        # Validate both final resolved output paths, not just the bare stem.
        for suffix in (".md", ".json"):
            candidate = report_path + suffix
            rep_abs = os.path.normcase(os.path.realpath(os.path.abspath(candidate)))
            if rep_abs == src_abs:
                raise SourcePathError(
                    f"Report path '{candidate}' cannot overwrite source input: {src_abs}"
                )
            try:
                if os.path.commonpath([src_abs, rep_abs]) == src_abs and os.path.isfile(src_abs):
                    raise SourcePathError(
                        f"Report path '{candidate}' cannot overwrite source file: {src_abs}"
                    )
            except ValueError:
                pass
            if rep_abs in output_collision_paths:
                raise SourcePathError(
                    f"Report path '{candidate}' cannot overwrite generated output or sidecar: {rep_abs}"
                )

