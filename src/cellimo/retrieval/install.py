"""Installing a retrieval index.

The index is a separate, large artifact — the published KAI archive is 345 MB
compressed and 840 MB unpacked — so downloading it is always an explicit,
user-initiated action. Nothing here runs during ``pip install``, during tests,
or as a side effect of any other command.

Three things this does that KAI's downloader did not:

* strips the archive's leading ``retrieval/`` component, so the extracted tree
  is the one the readers actually expect (KAI's script extracted one level too
  deep, which made its own verification step always fail);
* skips ``__MACOSX`` junk and rejects entries that would escape the destination;
* downloads to a ``.part`` file and only moves it into place once the checksum
  matches, so an interrupted download is resumed or discarded rather than
  mistaken for a complete one.
"""

from __future__ import annotations

import hashlib
import shutil
import urllib.error
import urllib.request
import zipfile
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

from cellimo.errors import RetrievalError
from cellimo.resources import index_root

__all__ = [
    "DEFAULT_SOURCE",
    "INDEX_SOURCES",
    "IndexSource",
    "common_prefix",
    "download_archive",
    "extract_archive",
    "install_from_archive",
    "install_index",
]

_CHUNK = 1024 * 256


@dataclass(frozen=True)
class IndexSource:
    """A downloadable index build."""

    name: str
    url: str
    version: str
    md5: str = ""
    bytes: int = 0
    license: str = ""
    citation: str = ""
    #: Leading path component to strip when extracting.
    strip_prefix: str = ""

    def filename(self) -> str:
        return self.url.rsplit("/", 1)[-1]


#: The index inherited from KAI. Published on Zenodo under GPL-3.0-or-later,
#: which is why it is downloaded rather than redistributed with Cellimo.
INDEX_SOURCES: dict[str, IndexSource] = {
    "kai-251121": IndexSource(
        name="kai-251121",
        url="https://zenodo.org/records/17660667/files/kai_retrieval_251121.zip",
        version="251121",
        md5="f8c9fb9d4f258fb4add0228109cf2d14",
        bytes=345_602_911,
        license="GPL-3.0-or-later (index data; Cellimo's own code is Apache-2.0)",
        citation="KAI retrieval database, DOI 10.5281/zenodo.17660667",
        strip_prefix="retrieval/",
    )
}

DEFAULT_SOURCE = "kai-251121"

ProgressCallback = Callable[[int, int], None]


def _md5_of(path: Path) -> str:
    digest = hashlib.md5()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(_CHUNK)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def download_archive(
    source: IndexSource,
    destination: Path,
    *,
    progress: ProgressCallback | None = None,
    force: bool = False,
) -> Path:
    """Download ``source`` to ``destination``, resuming a partial download.

    Returns the path of the completed archive. Raises :class:`RetrievalError`
    when the checksum does not match, leaving the partial file in place so a
    retry can resume rather than start over.
    """
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists() and not force:
        if source.md5 and _md5_of(destination) == source.md5:
            return destination
        destination.unlink()

    partial = destination.with_suffix(destination.suffix + ".part")
    existing = partial.stat().st_size if partial.exists() else 0
    request = urllib.request.Request(source.url)
    if existing:
        request.add_header("Range", f"bytes={existing}-")

    try:
        with urllib.request.urlopen(request) as response:
            resumed = response.status == 206
            if existing and not resumed:
                existing = 0
                partial.unlink(missing_ok=True)
            total = int(response.headers.get("Content-Length") or 0) + existing
            mode = "ab" if resumed and existing else "wb"
            downloaded = existing
            with partial.open(mode) as handle:
                while True:
                    chunk = response.read(_CHUNK)
                    if not chunk:
                        break
                    handle.write(chunk)
                    downloaded += len(chunk)
                    if progress is not None:
                        progress(downloaded, total or source.bytes)
    except urllib.error.URLError as exc:
        raise RetrievalError(f"downloading {source.url} failed: {exc}") from exc

    if source.md5:
        actual = _md5_of(partial)
        if actual != source.md5:
            raise RetrievalError(
                f"checksum mismatch for {source.filename()}: expected {source.md5}, "
                f"got {actual}. The partial download was kept at {partial}; delete it "
                f"to start over."
            )
    partial.replace(destination)
    return destination


def _safe_members(
    archive: zipfile.ZipFile, strip_prefix: str, destination: Path
) -> Iterable[tuple[zipfile.ZipInfo, Path]]:
    """Yield archive members with their resolved, contained destinations."""
    root = destination.resolve()
    for info in archive.infolist():
        name = info.filename
        if name.startswith("__MACOSX/") or name.endswith("/.DS_Store"):
            continue
        if strip_prefix and name.startswith(strip_prefix):
            name = name[len(strip_prefix) :]
        if not name or name.endswith("/"):
            continue
        target = (destination / name).resolve()
        if root != target and root not in target.parents:
            raise RetrievalError(
                f"archive entry {info.filename!r} would extract outside {destination}"
            )
        yield info, target


def extract_archive(
    archive_path: Path,
    destination: Path,
    *,
    strip_prefix: str = "",
    progress: ProgressCallback | None = None,
) -> int:
    """Extract ``archive_path`` into ``destination``. Returns the file count."""
    destination.mkdir(parents=True, exist_ok=True)
    written = 0
    with zipfile.ZipFile(archive_path) as archive:
        members = list(_safe_members(archive, strip_prefix, destination))
        total = len(members)
        for info, target in members:
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(info) as source_file, target.open("wb") as handle:
                shutil.copyfileobj(source_file, handle, _CHUNK)
            written += 1
            if progress is not None:
                progress(written, total)
    return written


def common_prefix(archive_path: Path) -> str:
    """Return the archive's single top-level directory, or an empty string.

    Published index archives wrap everything in one directory (``retrieval/`` in
    KAI's case). Stripping it automatically is what makes the extracted tree the
    one the readers expect, instead of one level too deep.
    """
    with zipfile.ZipFile(archive_path) as archive:
        tops = {
            name.split("/", 1)[0]
            for name in archive.namelist()
            if name and not name.startswith("__MACOSX")
        }
    if len(tops) != 1:
        return ""
    only = tops.pop()
    with zipfile.ZipFile(archive_path) as archive:
        nested = any(
            name.startswith(f"{only}/")
            for name in archive.namelist()
            if not name.startswith("__MACOSX")
        )
    return f"{only}/" if nested else ""


def install_from_archive(
    archive_path: str | Path,
    *,
    destination: Path | None = None,
    strip_prefix: str | None = None,
    force: bool = False,
) -> Path:
    """Install an index from a local archive, without touching the network.

    ``strip_prefix=None`` detects a single wrapping directory and removes it.
    Pass an empty string to extract the archive exactly as it is.
    """
    target = Path(destination) if destination is not None else index_root()
    archive = Path(archive_path)
    if not archive.is_file():
        raise RetrievalError(f"{archive} does not exist")
    if strip_prefix is None:
        strip_prefix = common_prefix(archive)
    if target.exists() and any(target.iterdir()) and not force:
        raise RetrievalError(
            f"{target} is not empty; pass force=True to replace the installed index"
        )
    if force and target.exists():
        shutil.rmtree(target)
    extract_archive(archive, target, strip_prefix=strip_prefix)
    return target


def install_index(
    source_name: str = DEFAULT_SOURCE,
    *,
    destination: Path | None = None,
    force: bool = False,
    keep_archive: bool = False,
    progress: ProgressCallback | None = None,
) -> Path:
    """Download and install a published index. Always an explicit network action."""
    if source_name not in INDEX_SOURCES:
        raise RetrievalError(
            f"unknown index {source_name!r}; available: {sorted(INDEX_SOURCES)}"
        )
    source = INDEX_SOURCES[source_name]
    target = Path(destination) if destination is not None else index_root()
    if target.exists() and any(target.iterdir()) and not force:
        raise RetrievalError(
            f"an index is already installed at {target}; pass --force to replace it"
        )

    cache = target.parent / ".cellimo-downloads"
    archive = download_archive(source, cache / source.filename(), progress=progress, force=force)
    if force and target.exists():
        shutil.rmtree(target)
    extract_archive(archive, target, strip_prefix=source.strip_prefix, progress=progress)
    if not keep_archive:
        archive.unlink(missing_ok=True)
    return target
