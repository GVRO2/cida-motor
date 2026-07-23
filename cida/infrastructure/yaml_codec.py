import yaml  # type: ignore
from cida.domain.errors import SemanticValidationError

class UniqueKeyLoader(yaml.SafeLoader):
    def construct_mapping(self, node, deep=False):
        mapping = []
        for key_node, value_node in node.value:
            key = self.construct_object(key_node, deep=deep)
            if key in [k for k, _ in mapping]:
                raise yaml.constructor.ConstructorError(
                    None, None, f"Duplicate key '{key}' found in YAML frontmatter", key_node.start_mark
                )
            value = self.construct_object(value_node, deep=deep)
            mapping.append((key, value))
        return super().construct_mapping(node, deep=deep)

class YamlCodec:
    """Concrete YAML parser enforcing unique keys."""

    def decode(self, content: str) -> dict:
        try:
            data = yaml.load(content, Loader=UniqueKeyLoader)
            if data is None:
                return {}
            if not isinstance(data, dict):
                raise SemanticValidationError("Frontmatter must be a key-value dictionary")
            return {str(k): v for k, v in data.items()}
        except Exception as e:
            if not isinstance(e, SemanticValidationError):
                raise SemanticValidationError(f"YAML parsing error: {e}") from e
            raise

    def parse_yaml_frontmatter_safe(self, content: str) -> dict:
        """
        Parses frontmatter content using safe PyYAML and UniqueKeyLoader to reject duplicates.
        """
        if content.startswith('\ufeff'):
            content = content[1:]
        lines = content.strip().splitlines()
        if not lines or lines[0].strip() != '---':
            raise ValueError("YAML frontmatter must start with '---'")
        if len(lines) < 2 or lines[-1].strip() != '---':
            raise ValueError("YAML frontmatter must end with '---'")

        yaml_str = "\n".join(lines[1:-1])
        if not yaml_str.strip():
            return {}

        try:
            data = yaml.load(yaml_str, Loader=UniqueKeyLoader)
            if data is None:
                return {}
            if not isinstance(data, dict):
                raise ValueError("Frontmatter must be a key-value dictionary")
            return {str(k): v for k, v in data.items()}
        except Exception as e:
            raise ValueError(f"YAML parsing error: {e}")
