"""Raw OOXML package inspection via :mod:`zipfile`.

A .pptx is a zip archive. ``python-pptx`` gives a convenient object model over the
slide parts but deliberately hides, or simply does not expose, several things the
hygiene rules must check:

* ``docProps/core.xml`` and ``docProps/app.xml`` -- author names, company, revision
  history. HY-004 exists because these leak the identity of the bank's analyst and
  the client's previous project codename into a document sent outside the firm.
* PowerPoint comments, in both the legacy ``ppt/comments/notesSlide*.xml`` shape and
  the modern ``ppt/comments/modernComment_*.xml`` shape (HY-005).
* Every ``_rels/*.rels`` part, so external and broken relationships are found
  wherever they hide, including inside charts and embedded objects (HY-007).
* Embedded font parts, so HY-009 can tell an embedded font from a missing one.
* Native pixel dimensions of every media part, for the effective-DPI check (HY-008).

Nothing in this module imports ``python-pptx``. It is deliberately usable on a
package that ``python-pptx`` refuses to open.
"""

from __future__ import annotations

import hashlib
import io
import posixpath
import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final
from xml.etree import ElementTree as ET

from PIL import Image, UnidentifiedImageError

# Namespaces used by the package-level parts.
NS: Final[dict[str, str]] = {
    "cp": "http://schemas.openxmlformats.org/package/2006/metadata/core-properties",
    "dc": "http://purl.org/dc/elements/1.1/",
    "dcterms": "http://purl.org/dc/terms/",
    "ep": "http://schemas.openxmlformats.org/officeDocument/2006/extended-properties",
    "rel": "http://schemas.openxmlformats.org/package/2006/relationships",
    "p": "http://schemas.openxmlformats.org/presentationml/2006/main",
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "p188": "http://schemas.microsoft.com/office/powerpoint/2018/8/main",
}

#: Relationship types whose targets are legitimately external and never a defect.
_BENIGN_EXTERNAL_TYPES: Final[frozenset[str]] = frozenset(
    {
        "http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink",
    }
)

#: A Windows drive-letter or UNC path, i.e. a link that will break on any other machine.
_LOCAL_PATH_RE: Final[re.Pattern[str]] = re.compile(r"^(?:[A-Za-z]:[\\/]|\\\\|file:///?)")

#: Relationship types that indicate a linked (not embedded) payload.
_LINK_BEARING_TYPES: Final[tuple[str, ...]] = (
    "/oleObject",
    "/package",
    "/image",
    "/audio",
    "/video",
    "/media",
)

_IMAGE_EXTENSIONS: Final[frozenset[str]] = frozenset(
    {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".tif", ".tiff", ".webp", ".emf", ".wmf"}
)


@dataclass(frozen=True, slots=True)
class CoreProperties:
    """``docProps/core.xml`` fields relevant to metadata hygiene."""

    creator: str | None
    last_modified_by: str | None
    title: str | None
    subject: str | None
    keywords: str | None
    description: str | None
    category: str | None
    revision: str | None
    created: str | None
    modified: str | None

    def identifying_fields(self) -> dict[str, str]:
        """Fields that name a person or an internal project. HY-004's payload."""
        out: dict[str, str] = {}
        for label, value in (
            ("creator", self.creator),
            ("lastModifiedBy", self.last_modified_by),
            ("category", self.category),
            ("keywords", self.keywords),
        ):
            if value and value.strip():
                out[label] = value.strip()
        return out


@dataclass(frozen=True, slots=True)
class AppProperties:
    """``docProps/app.xml`` fields relevant to metadata hygiene."""

    company: str | None
    manager: str | None
    application: str | None
    template: str | None
    total_time: str | None

    def identifying_fields(self) -> dict[str, str]:
        out: dict[str, str] = {}
        for label, value in (("company", self.company), ("manager", self.manager)):
            if value and value.strip():
                out[label] = value.strip()
        return out


@dataclass(frozen=True, slots=True)
class Relationship:
    """One entry from a ``_rels/*.rels`` part."""

    source_part: str
    rel_id: str
    reltype: str
    target: str
    target_mode: str

    @property
    def is_external(self) -> bool:
        return self.target_mode == "External"

    @property
    def short_type(self) -> str:
        return self.reltype.rsplit("/", 1)[-1]

    @property
    def is_suspect_external(self) -> bool:
        """True when an external target will break on another machine.

        Hyperlinks to ``https://`` URLs are normal. A hyperlink or an image
        pointing at ``C:\\Users\\analyst\\Desktop`` is a defect that renders as a
        red X on the client's screen, and additionally discloses a local path.
        """
        if not self.is_external:
            return False
        if _LOCAL_PATH_RE.match(self.target):
            return True
        return self.reltype not in _BENIGN_EXTERNAL_TYPES and self.target.startswith("file:")

    @property
    def is_linked_payload(self) -> bool:
        """An external relationship for content that should have been embedded."""
        if not self.is_external:
            return False
        return any(self.reltype.endswith(suffix) for suffix in _LINK_BEARING_TYPES)


@dataclass(frozen=True, slots=True)
class MediaPart:
    """A media part, with the identity and native size the rules need."""

    part_name: str
    sha1: str
    byte_size: int
    pixel_width: int | None
    pixel_height: int | None
    format: str | None

    @property
    def aspect_ratio(self) -> float | None:
        if not self.pixel_width or not self.pixel_height:
            return None
        return self.pixel_width / self.pixel_height

    def effective_dpi(self, rendered_width_inches: float) -> float | None:
        """Native pixels per rendered inch. HY-008 measures this.

        Vector media (EMF/WMF) has no meaningful DPI and returns None rather than
        a fabricated number.
        """
        if not self.pixel_width or rendered_width_inches <= 0:
            return None
        return self.pixel_width / rendered_width_inches


@dataclass(frozen=True, slots=True)
class CommentPart:
    """A comment-bearing part, with a count so the report can be specific."""

    part_name: str
    comment_count: int
    authors: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class EmbeddedFont:
    """A typeface embedded in the package via ``p:embeddedFontLst``."""

    typeface: str
    regular: bool
    bold: bool
    italic: bool
    bold_italic: bool


@dataclass
class PackageInfo:
    """Everything the package layer knows about one .pptx."""

    path: Path
    core: CoreProperties
    app: AppProperties
    relationships: list[Relationship] = field(default_factory=list)
    media: dict[str, MediaPart] = field(default_factory=dict)
    comments: list[CommentPart] = field(default_factory=list)
    embedded_fonts: list[EmbeddedFont] = field(default_factory=list)
    part_names: tuple[str, ...] = ()

    @property
    def has_comments(self) -> bool:
        return any(part.comment_count > 0 for part in self.comments)

    @property
    def total_comment_count(self) -> int:
        return sum(part.comment_count for part in self.comments)

    @property
    def comment_authors(self) -> tuple[str, ...]:
        seen: dict[str, None] = {}
        for part in self.comments:
            for author in part.authors:
                seen.setdefault(author, None)
        return tuple(seen)

    @property
    def embedded_typefaces(self) -> frozenset[str]:
        return frozenset(font.typeface for font in self.embedded_fonts)

    def media_by_sha1(self, sha1: str) -> MediaPart | None:
        for part in self.media.values():
            if part.sha1 == sha1:
                return part
        return None

    def resolve(self, rel: Relationship) -> str | None:
        """The part name a relationship points at, or None when it is external."""
        return resolve_target(rel.source_part, rel.target)

    def suspect_relationships(self) -> list[Relationship]:
        return [
            rel
            for rel in self.relationships
            if rel.is_suspect_external or rel.is_linked_payload
        ]

    def broken_internal_relationships(self) -> list[Relationship]:
        """Internal relationships whose target part is absent from the package."""
        known = set(self.part_names)
        broken: list[Relationship] = []
        for rel in self.relationships:
            if rel.is_external:
                continue
            resolved = resolve_target(rel.source_part, rel.target)
            if resolved and resolved not in known:
                broken.append(rel)
        return broken


def load_package(path: str | Path) -> PackageInfo:
    """Read a .pptx as a zip archive and extract package-level facts."""
    path = Path(path)
    with zipfile.ZipFile(path) as archive:
        names = tuple(sorted(archive.namelist()))
        info = PackageInfo(
            path=path,
            core=_read_core(archive),
            app=_read_app(archive),
            part_names=names,
        )
        info.relationships = _read_relationships(archive, names)
        info.media = _read_media(archive, names)
        info.comments = _read_comments(archive, names)
        info.embedded_fonts = _read_embedded_fonts(archive)
    return info


def _text(root: ET.Element | None, xpath: str) -> str | None:
    if root is None:
        return None
    node = root.find(xpath, NS)
    if node is None or node.text is None:
        return None
    return node.text


def _parse(archive: zipfile.ZipFile, part: str) -> ET.Element | None:
    try:
        raw = archive.read(part)
    except KeyError:
        return None
    try:
        return ET.fromstring(raw)
    except ET.ParseError:
        return None


def _read_core(archive: zipfile.ZipFile) -> CoreProperties:
    root = _parse(archive, "docProps/core.xml")
    return CoreProperties(
        creator=_text(root, "dc:creator"),
        last_modified_by=_text(root, "cp:lastModifiedBy"),
        title=_text(root, "dc:title"),
        subject=_text(root, "dc:subject"),
        keywords=_text(root, "cp:keywords"),
        description=_text(root, "dc:description"),
        category=_text(root, "cp:category"),
        revision=_text(root, "cp:revision"),
        created=_text(root, "dcterms:created"),
        modified=_text(root, "dcterms:modified"),
    )


def _read_app(archive: zipfile.ZipFile) -> AppProperties:
    root = _parse(archive, "docProps/app.xml")
    return AppProperties(
        company=_text(root, "ep:Company"),
        manager=_text(root, "ep:Manager"),
        application=_text(root, "ep:Application"),
        template=_text(root, "ep:Template"),
        total_time=_text(root, "ep:TotalTime"),
    )


def _read_relationships(
    archive: zipfile.ZipFile, names: tuple[str, ...]
) -> list[Relationship]:
    out: list[Relationship] = []
    for name in names:
        if not name.endswith(".rels"):
            continue
        root = _parse(archive, name)
        if root is None:
            continue
        source = _source_part_for_rels(name)
        for node in root.findall("rel:Relationship", NS):
            out.append(
                Relationship(
                    source_part=source,
                    rel_id=node.get("Id", ""),
                    reltype=node.get("Type", ""),
                    target=node.get("Target", ""),
                    target_mode=node.get("TargetMode", "Internal"),
                )
            )
    return out


def _source_part_for_rels(rels_name: str) -> str:
    """``ppt/slides/_rels/slide1.xml.rels`` -> ``ppt/slides/slide1.xml``.

    The package's own root relationships live in ``_rels/.rels``, whose stem is
    empty. That case returns the empty string, meaning "the package root", so
    targets in it resolve relative to the archive root rather than to a
    directory called ``/``.
    """
    directory, filename = posixpath.split(rels_name)
    parent = posixpath.dirname(directory)
    stem = filename[: -len(".rels")]
    if not stem:
        return parent
    return posixpath.join(parent, stem) if parent else stem


def resolve_target(source_part: str, target: str) -> str | None:
    """Resolve a relationship target to a part name, or None if it is not internal.

    Returns a package-relative name with no leading slash, which is the form
    ``ZipFile.namelist`` uses, so the result can be compared against
    :attr:`PackageInfo.part_names` directly.
    """
    if not target or target.startswith(("http://", "https://", "mailto:", "file:")):
        return None
    if target.startswith("/"):
        return posixpath.normpath(target.lstrip("/"))
    base = posixpath.dirname(source_part)
    resolved = posixpath.join(base, target) if base else target
    return posixpath.normpath(resolved).lstrip("/")


#: Retained under the old private name for internal callers.
_resolve_part = resolve_target


def _read_media(archive: zipfile.ZipFile, names: tuple[str, ...]) -> dict[str, MediaPart]:
    out: dict[str, MediaPart] = {}
    for name in names:
        if not name.startswith("ppt/media/"):
            continue
        raw = archive.read(name)
        width: int | None = None
        height: int | None = None
        image_format: str | None = None
        if posixpath.splitext(name)[1].lower() in _IMAGE_EXTENSIONS:
            width, height, image_format = _probe_image(raw)
        out[name] = MediaPart(
            part_name=name,
            sha1=hashlib.sha1(raw, usedforsecurity=False).hexdigest(),
            byte_size=len(raw),
            pixel_width=width,
            pixel_height=height,
            format=image_format,
        )
    return out


def _probe_image(raw: bytes) -> tuple[int | None, int | None, str | None]:
    """Read pixel dimensions without decoding the whole image.

    EMF and WMF are vector formats Pillow cannot open; they return all-None and
    HY-008 skips them rather than reporting a fabricated DPI.
    """
    try:
        with Image.open(io.BytesIO(raw)) as image:
            return image.width, image.height, image.format
    except (UnidentifiedImageError, OSError, ValueError):
        return None, None, None


def _read_comments(archive: zipfile.ZipFile, names: tuple[str, ...]) -> list[CommentPart]:
    """Find comments in both the legacy and the modern PowerPoint shapes."""
    out: list[CommentPart] = []
    author_names = _read_comment_authors(archive)
    for name in names:
        if not name.startswith("ppt/comments/") or not name.endswith(".xml"):
            continue
        root = _parse(archive, name)
        if root is None:
            continue
        legacy = root.findall("p:cm", NS)
        modern = root.findall("p188:cm", NS)
        nodes = legacy or modern
        authors: list[str] = []
        for node in nodes:
            author_id = node.get("authorId")
            if author_id and author_id in author_names:
                authors.append(author_names[author_id])
        out.append(
            CommentPart(
                part_name=name,
                comment_count=len(nodes),
                authors=tuple(dict.fromkeys(authors)),
            )
        )
    return out


def _read_comment_authors(archive: zipfile.ZipFile) -> dict[str, str]:
    root = _parse(archive, "ppt/commentAuthors.xml")
    if root is None:
        return {}
    out: dict[str, str] = {}
    for node in root.findall("p:cmAuthor", NS):
        author_id = node.get("id")
        name = node.get("name")
        if author_id and name:
            out[author_id] = name
    return out


def _read_embedded_fonts(archive: zipfile.ZipFile) -> list[EmbeddedFont]:
    root = _parse(archive, "ppt/presentation.xml")
    if root is None:
        return []
    out: list[EmbeddedFont] = []
    for node in root.findall("p:embeddedFontLst/p:embeddedFont", NS):
        font = node.find("p:font", NS)
        if font is None:
            continue
        typeface = font.get("typeface")
        if not typeface:
            continue
        out.append(
            EmbeddedFont(
                typeface=typeface,
                regular=node.find("p:regular", NS) is not None,
                bold=node.find("p:bold", NS) is not None,
                italic=node.find("p:italic", NS) is not None,
                bold_italic=node.find("p:boldItalic", NS) is not None,
            )
        )
    return out
