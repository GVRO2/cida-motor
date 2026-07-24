from dataclasses import dataclass, field
from typing import List


@dataclass(frozen=True)
class ProcessingContext:
    """Immutable per-file processing context to avoid redundant disk reads and hash computations."""
    source_path: str
    source_real_path: str
    relative_path: str
    source_bytes: bytes
    source_text: str
    source_sha256: str
    detected_profile: str
    original_tokens: int


@dataclass
class FileInventory:
    """Single deterministic inventory of files to process."""
    all_files: List[str] = field(default_factory=list)
    processable_files: List[str] = field(default_factory=list)
    markdown_files: List[str] = field(default_factory=list)
    java_files: List[str] = field(default_factory=list)
    code_files: List[str] = field(default_factory=list)
    binary_files: List[str] = field(default_factory=list)
