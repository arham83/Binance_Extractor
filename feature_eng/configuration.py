"""Read and validate the YAML settings used by the project entry point."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml


class ConfigurationError(ValueError):
    """A configuration file is invalid or has unsupported settings."""


class _UniqueKeySafeLoader(yaml.SafeLoader):
    """Keep SafeLoader's safe types while rejecting ambiguous duplicate keys."""

    def construct_mapping(self, node, deep=False):
        self.flatten_mapping(node)
        mapping = {}
        for key_node, value_node in node.value:
            key = self.construct_object(key_node, deep=deep)
            try:
                duplicate = key in mapping
            except TypeError as error:
                raise yaml.constructor.ConstructorError(
                    "while reading a mapping", node.start_mark,
                    "configuration keys must be strings", key_node.start_mark,
                ) from error
            if duplicate:
                raise yaml.constructor.ConstructorError(
                    "while reading a mapping", node.start_mark,
                    f"duplicate key: {key!r}", key_node.start_mark,
                )
            mapping[key] = self.construct_object(value_node, deep=deep)
        return mapping


@dataclass(frozen=True)
class PipelineConfig:
    input_path: Path
    output_dir: Path
    symbols: tuple[str, ...] | None = None
    gap_policy: str = "reset"


def load_config(path: str | Path) -> PipelineConfig:
    """Load settings, resolving relative data paths beside the YAML file.

    ``input`` and ``output`` are required. An absent, null, or empty ``symbols``
    list selects all instruments. ``gap_policy`` defaults to ``reset``.
    """
    config_path = Path(path).expanduser().resolve()
    try:
        contents = config_path.read_text(encoding="utf-8")
    except OSError as error:
        raise ConfigurationError(f"Cannot read configuration {config_path}: {error}") from error
    try:
        settings = yaml.load(contents, Loader=_UniqueKeySafeLoader)
    except yaml.YAMLError as error:
        raise ConfigurationError(f"Invalid YAML in {config_path}: {error}") from error
    if not isinstance(settings, dict):
        raise ConfigurationError("Configuration must be a YAML mapping of setting names to values.")
    if not all(isinstance(key, str) for key in settings):
        raise ConfigurationError("Configuration setting names must be strings.")
    unknown = sorted(set(settings) - {"input", "output", "symbols", "gap_policy"})
    if unknown:
        raise ConfigurationError(f"Unknown configuration setting(s): {', '.join(unknown)}")

    paths = {}
    for name in ("input", "output"):
        value = settings.get(name)
        if not isinstance(value, str) or not value.strip():
            raise ConfigurationError(f"{name} must be a nonempty path string.")
        candidate = Path(value).expanduser()
        if not candidate.is_absolute():
            candidate = config_path.parent / candidate
        paths[name] = candidate.resolve()

    symbols = settings.get("symbols")
    if symbols is not None:
        if not isinstance(symbols, list) or any(
            not isinstance(symbol, str) or not symbol.strip() for symbol in symbols
        ):
            raise ConfigurationError("symbols must be a list of nonempty strings, [] or null.")
    gap_policy = settings.get("gap_policy", "reset")
    if not isinstance(gap_policy, str) or gap_policy not in ("reset", "rows"):
        raise ConfigurationError("gap_policy must be reset or rows.")
    return PipelineConfig(
        input_path=paths["input"], output_dir=paths["output"],
        symbols=tuple(symbols) if symbols else None, gap_policy=gap_policy,
    )
