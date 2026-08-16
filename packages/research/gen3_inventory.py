"""Bounded, read-only metadata inventory for Gen3 Phase 0.

The inventory samples at most a caller-specified number of entries and, for
Parquet, reads only file schema through optional PyArrow. It never opens table
rows, writes files, or creates research/trial/contract artifacts.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from .gen3_policy import Availability, DataClass, DataSourceAuditRecord


@dataclass(frozen=True)
class InventoryLimits:
    max_entries_per_root: int = 50
    max_sample_files: int = 2
    recursive: bool = False

    def validate(self) -> None:
        if type(self.max_entries_per_root) is not int or self.max_entries_per_root < 1:
            raise ValueError("max_entries_per_root must be a positive non-boolean integer")
        if type(self.max_sample_files) is not int or self.max_sample_files < 1:
            raise ValueError("max_sample_files must be a positive non-boolean integer")
        if self.recursive is not False:
            raise ValueError("recursive inventory is prohibited in Phase 0")


@dataclass(frozen=True)
class InventoryObservation:
    data_class: DataClass
    root: Path
    root_exists: bool
    entry_count_observed: int
    entry_limit_reached: bool
    sample_paths: tuple[Path, ...]
    file_formats: tuple[str, ...]
    observed_fields: tuple[str, ...]
    schema_errors: tuple[str, ...]

    @property
    def availability(self) -> Availability:
        if not self.root_exists:
            return Availability.MISSING
        # Samples never prove coverage/PIT semantics, so cannot become AVAILABLE.
        return Availability.PARTIAL if self.sample_paths or self.entry_count_observed else Availability.UNVERIFIED

    def as_audit_record(self, *, source_id: str | None = None) -> DataSourceAuditRecord:
        # A policy record needs independently verified coverage bounds before
        # it can be PARTIAL/AVAILABLE. Preserve conservative evidence without
        # manufacturing those bounds from a directory sample.
        return DataSourceAuditRecord(
            source_id=source_id or f"inventory-{self.data_class.value}",
            data_class=self.data_class,
            availability=Availability.UNVERIFIED if self.root_exists else Availability.MISSING,
            local_path=str(self.root) if self.root_exists else None,
            file_format=", ".join(self.file_formats) or None,
            observed_fields=self.observed_fields,
            notes="bounded metadata-only inventory; coverage and PIT semantics remain unverified",
        )


def _bounded_files(root: Path, limits: InventoryLimits) -> tuple[list[Path], bool]:
    candidates: list[Path] = []
    for entry in root.iterdir():
        if entry.is_file():
            candidates.append(entry)
        if len(candidates) >= limits.max_entries_per_root:
            return candidates, True
    return candidates, False


def _schema_fields(path: Path) -> tuple[str, ...]:
    """Read Parquet schema metadata only; no record batches are materialized."""
    if path.suffix.lower() != ".parquet":
        return ()
    try:
        import pyarrow.parquet as pq
    except ImportError as exc:  # pragma: no cover - environment-dependent
        raise RuntimeError("pyarrow is unavailable for Parquet schema inspection") from exc
    return tuple(pq.ParquetFile(path).schema.names)


def inspect_root(
    data_class: DataClass,
    root: str | Path,
    *,
    limits: InventoryLimits = InventoryLimits(),
) -> InventoryObservation:
    """Inspect one explicit root. No machine-specific roots are hard-coded."""
    limits.validate()
    root_path = Path(root)
    if not root_path.is_dir():
        return InventoryObservation(data_class, root_path, False, 0, False, (), (), (), ())
    entries, capped = _bounded_files(root_path, limits)
    samples = tuple(entries[: limits.max_sample_files])
    fields: set[str] = set()
    errors: list[str] = []
    for sample in samples:
        try:
            fields.update(_schema_fields(sample))
        except (OSError, RuntimeError, ValueError) as exc:
            errors.append(f"{sample.name}: {exc}")
    return InventoryObservation(
        data_class=data_class,
        root=root_path,
        root_exists=True,
        entry_count_observed=len(entries),
        entry_limit_reached=capped,
        sample_paths=samples,
        file_formats=tuple(sorted({path.suffix.lower().lstrip(".") or "no_extension" for path in samples})),
        observed_fields=tuple(sorted(fields)),
        schema_errors=tuple(errors),
    )


def inspect_domains(
    roots: Mapping[DataClass, str | Path],
    *,
    limits: InventoryLimits = InventoryLimits(),
) -> dict[DataClass, InventoryObservation]:
    """Return every required domain; omitted roots are MISSING, never assumed."""
    limits.validate()
    return {
        data_class: inspect_root(data_class, roots[data_class], limits=limits)
        if data_class in roots
        else InventoryObservation(data_class, Path("<not-configured>"), False, 0, False, (), (), (), ())
        for data_class in DataClass
    }
