from dataclasses import dataclass
from typing import Dict, List, Optional, Any

@dataclass(frozen=True)
class TokenMetrics:
    original: int
    minified: int
    sidecar: int
    auxiliary: int

    @property
    def gross_savings(self) -> int:
        return self.original - self.minified

    @property
    def overhead(self) -> int:
        return self.sidecar + self.auxiliary

    @property
    def net_savings(self) -> int:
        return self.gross_savings - self.overhead

    @property
    def net_savings_percentage(self) -> float:
        if self.original <= 0:
            return 0.0
        return (self.net_savings / self.original) * 100.0

@dataclass(frozen=True)
class Sidecar:
    source: str
    source_sha256: str
    entries: Dict[str, str]
    format: str = "cida-token-sidecar"
    version: int = 1

@dataclass(frozen=True)
class OptimizationRequest:
    filepath: str
    profile: str
    verify_semantics: bool
    fail_on_inflation: bool
    dictionary_scope: str

@dataclass(frozen=True)
class OptimizationResult:
    filepath: str
    profile: str
    metrics: TokenMetrics
    accepted_transforms: List[str]
    rejected_transforms: List[str]
    semantic_status: str
    execution_time: float
    sidecar_data: Optional[Dict[str, Any]] = None

@dataclass(frozen=True)
class ManifestFile:
    path: str
    sha256: str
    size: int

@dataclass(frozen=True)
class CorpusManifest:
    schema_version: int
    commit_sha: str
    platform: str
    files: List[Dict[str, Any]]
    tree_sha256: str

@dataclass(frozen=True)
class SemanticValidationResult:
    is_valid: bool
    message: str
