class CidaError(Exception):
    """Base class for all CIDA exceptions."""
    pass

class UsageError(CidaError):
    """Raised for general CLI usage errors."""
    exit_code = 1

class TokenizerError(CidaError):
    """Raised for tokenizer/tiktoken initialization or encoding errors."""
    exit_code = 2

class SemanticValidationError(CidaError):
    """Raised when semantic/structural comparison checks fail."""
    exit_code = 3

class EncodingValidationError(CidaError):
    """Raised when file text decoding fails strict encoding validation."""
    exit_code = 3

class SourcePathError(CidaError):
    """Raised when the source directory or file is invalid/missing."""
    exit_code = 4

class SidecarValidationError(CidaError, ValueError):
    """Raised when sidecar schema or integrity checks fail."""
    exit_code = 5

class ReconstructionError(CidaError):
    """Raised when decompression or content reconstruction fails."""
    exit_code = 5

class InternalProcessingError(CidaError):
    """Raised for unexpected processing errors or file-specific errors."""
    exit_code = 6

