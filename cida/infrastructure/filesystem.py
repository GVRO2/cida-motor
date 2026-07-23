import os
import shutil
from typing import List

class PhysicalFilesystem:
    """Concrete implementation of filesystem repository."""

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

    def write_text(self, filepath: str, content: str, encoding: str = "utf-8") -> None:
        os.makedirs(os.path.dirname(os.path.abspath(filepath)), exist_ok=True)
        content_lf = content.replace('\r\n', '\n')
        with open(filepath, 'w', encoding=encoding, newline='\n') as f:
            f.write(content_lf)

    def write_bytes(self, filepath: str, content: bytes) -> None:
        os.makedirs(os.path.dirname(os.path.abspath(filepath)), exist_ok=True)
        with open(filepath, 'wb') as f:
            f.write(content)

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
        except Exception:
            pass
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
