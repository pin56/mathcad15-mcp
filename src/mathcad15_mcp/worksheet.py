"""Mathcad 15 worksheet file (.xmcd).

Top-level regions are kept as verbatim XML text, so regions we do not touch are
written back byte for byte. New regions come from DSL (`dsl.Region`). Layout is
one column: a replaced block is restacked from its top with a 9 pt gap and
everything below it moves by the height difference. Mathcad keeps `align-y`
and recomputes `top`/`height` on save, so callers lay out twice: estimate,
let Mathcad save, then `apply` the same plan to the saved sheet.
"""
from __future__ import annotations

import base64
import gzip
import math
import re
import uuid
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from importlib import resources

from .dsl import Region
from .plot import build_blob

PT_PER_PX = 0.75             # COM Region.X/Y are 96 dpi pixels
LEFT, TOP, GAP = 18.0, 18.0, 9.0
TEXT_W = 420.0               # width of long (wrapped) text regions
_DIGEST = re.compile(r"<\?validation-md5-digest [^?]*\?>\s*")
_TAG = re.compile(r'<(/?)region\b(?=[\s/>])(?:[^>"]|"[^"]*")*>')
_HEAD = re.compile(r'<region\b(?:[^>"]|"[^"]*")*>')
_ATTR = re.compile(r'([\w:.-]+)="([^"]*)"')
_ITEM = re.compile(r'<item item-id="(\d+)"([^>]*)>(.*?)</item>', re.S)


class WorksheetError(Exception):
    pass


def _fmt(v: float) -> str:
    s = f"{v:.2f}".rstrip("0").rstrip(".")
    return "0" if s in ("", "-0") else s


def _split(body: str) -> list[str]:
    """Top-level <region> elements of a <regions> body, nested ones stay inside their parent."""
    out, depth, start = [], 0, 0
    for m in _TAG.finditer(body):
        if m.group(1):
            depth -= 1
            if depth == 0:
                out.append(body[start:m.end()])
        elif m.group(0).endswith("/>"):
            if depth == 0:
                out.append(m.group(0))
        else:
            if depth == 0:
                start = m.start()
            depth += 1
    return out


@dataclass(eq=False)
class WsRegion:
    xml: str        # whole <region …>…</region>
    kind: str       # local name of the first child: math, text, plot, area, picture, …
    a: dict = field(default_factory=dict)

    @classmethod
    def of(cls, xml: str) -> "WsRegion":
        m = _HEAD.match(xml)
        if not m:
            raise WorksheetError("регион без <region>")
        inner = re.search(r"<([A-Za-z][\w:.-]*)", xml[m.end():])
        kind = inner.group(1).split(":")[-1] if inner else "empty"
        return cls(xml, kind, dict(_ATTR.findall(m.group(0))))

    def f(self, k: str, default: float = 0.0) -> float:
        try:
            return float(self.a[k])
        except (KeyError, ValueError):
            return default

    @property
    def id(self) -> int:
        return int(self.f("region-id"))

    @property
    def top(self) -> float:
        return self.f("top")

    @property
    def left(self) -> float:
        return self.f("left")

    @property
    def height(self) -> float:
        return self.f("height")

    @property
    def width(self) -> float:
        return self.f("width")

    @property
    def align_y(self) -> float:
        return self.f("align-y", self.top)

    @property
    def bottom(self) -> float:
        return self.top + self.height

    def set(self, **kw):
        """Set start-tag attributes (python names: align_y -> align-y)."""
        head = _HEAD.match(self.xml).group(0)
        new = head
        for k, v in kw.items():
            k = k.replace("_", "-")
            v = _fmt(v) if isinstance(v, float) else str(v)
            self.a[k] = v
            pat = re.compile(rf'(\s{re.escape(k)}=)"[^"]*"')
            if pat.search(new):
                new = pat.sub(lambda m: f'{m.group(1)}"{v}"', new, count=1)
            else:
                end = -2 if new.endswith("/>") else -1
                new = new[:end] + f' {k}="{v}"' + new[end:]
        self.xml = new + self.xml[len(head):]


def rows(regions: list[WsRegion]) -> list[list[WsRegion]]:
    """Group into visual rows: a region joins the row if it overlaps the row's first region
    by at least half of the smaller height. Rows top to bottom, regions left to right."""
    out: list[list[WsRegion]] = []
    for r in sorted(regions, key=lambda r: (r.top, r.left)):
        if out:
            a = out[-1][0]
            overlap = min(a.bottom, r.bottom) - r.top
            if overlap > 0 and overlap >= min(a.height, r.height) / 2:
                out[-1].append(r)
                continue
        out.append([r])
    return [sorted(row, key=lambda r: r.left) for row in out]


def order(regions: list[WsRegion]) -> list[WsRegion]:
    return [r for row in rows(regions) for r in row]


@dataclass
class Plan:
    """Where a replaced block goes; replayable on the Mathcad-saved sheet (ids survive save).

    Several plans replay correctly when made top-down and applied bottom-up (reversed)."""
    new_ids: list[int]
    left: float
    top: float
    end: float                           # y below the block (top + heights + gaps) as last laid out


def _estimate(r: Region) -> tuple[float, float, float]:
    """(height, ascent, width) guess before Mathcad measures the region."""
    if r.kind == "plot":
        return 129.75, 0.0, 158.25
    if r.kind == "text":
        content = re.sub(r"<[^>]+>", "", r.xml)
        heading = 'style="Heading' in r.xml
        if 'lock-width="true"' in r.xml:
            lines = max(1, math.ceil(len(content) * 5.2 / TEXT_W))
            return 12.0 * lines + 4, 9.75, TEXT_W
        return (15.75, 12.75, len(content) * 8.5 + 6) if heading else (14.0, 9.75, len(content) * 5.5 + 6)
    lines = r.dsl.count("\n") + 1
    if lines > 1:
        return 15.0 * lines, 9.0, 160.0
    m = re.search(r'rows="(\d+)"', r.xml)
    if m and int(m.group(1)) > 1:
        h = 15.0 * int(m.group(1)) + 4
        return h, h / 2 + 4, 100.0
    if "<ml:div" in r.xml:
        return 27.75, 15.75, 80.0
    return 12.75, 9.0, 60.0


class Worksheet:
    def __init__(self, head: str, regions: list[WsRegion], tail: str):
        self.head, self.tail = head, tail
        self.regions = order(regions)
        self._items: list[str] = []
        root = re.search(r"<worksheet\b[^>]*>", head)
        self.nsdecl = " ".join(re.findall(r'xmlns(?::\w+)?="[^"]*"', root.group(0))) if root else ""

    # ------------------------------------------------------------ io

    @classmethod
    def parse(cls, text: str) -> "Worksheet":
        text = _DIGEST.sub("", text.lstrip("﻿"), count=1)
        m = re.search(r"<regions\s*/>|<regions>(.*?)</regions>", text, re.S)
        if not m or "<worksheet" not in text[:m.start()]:
            raise WorksheetError("нет <worksheet>/<regions> — это не лист Mathcad 15 (.xmcd)")
        regs = [WsRegion.of(x) for x in _split(m.group(1) or "")]
        return cls(text[:m.start()] + "<regions>", regs, "</regions>" + text[m.end():])

    @classmethod
    def load(cls, path: str) -> "Worksheet":
        with open(path, "rb") as fh:
            data = fh.read()
        if not data.lstrip(b"\xef\xbb\xbf \t\r\n").startswith(b"<"):
            raise WorksheetError(f"{path}: не XML (.mcd и другие двоичные форматы — только через mc_export в .xmcd)")
        return cls.parse(data.decode("utf-8", errors="replace"))

    @classmethod
    def new(cls) -> "Worksheet":
        t = resources.files("mathcad15_mcp").joinpath("data/template.xmcd").read_text("utf-8")
        t = re.sub(r"<documentID>[^<]*</documentID>", f"<documentID>{str(uuid.uuid4()).upper()}</documentID>", t)
        t = re.sub(r"<versionID>[^<]*</versionID>", f"<versionID>{str(uuid.uuid4()).upper()}</versionID>", t)
        return cls.parse(t)

    def to_xml(self) -> str:
        body = "".join("\n\t\t" + r.xml for r in self.regions)
        tail = self.tail
        if self._items:
            items = "".join("\n\t\t" + i for i in self._items)
            if re.search(r"<binaryContent\s*/>", tail):
                tail = re.sub(r"<binaryContent\s*/>", lambda _: f"<binaryContent>{items}\n\t</binaryContent>", tail, 1)
            elif "</binaryContent>" in tail:
                tail = tail.replace("</binaryContent>", items + "\n\t</binaryContent>", 1)
            else:
                tail = tail.replace("</worksheet>", f"\t<binaryContent>{items}\n\t</binaryContent>\n</worksheet>", 1)
        return self.head + body + ("\n\t" if body else "") + tail

    def save(self, path: str):
        with open(path, "w", encoding="utf-8", newline="") as fh:
            fh.write(self.to_xml())

    # ------------------------------------------------------------ lookup

    def elem(self, r: WsRegion) -> ET.Element:
        """Parsed <region> element (namespaces from the worksheet root)."""
        return ET.fromstring(f"<w {self.nsdecl}>{r.xml}</w>")[0]

    def items(self) -> dict[int, bytes]:
        out = {}
        for m in _ITEM.finditer(self.tail + "".join(self._items)):
            try:
                b = base64.b64decode(m.group(3))
                out[int(m.group(1))] = gzip.decompress(b) if 'content-encoding="gzip"' in m.group(2) else b
            except (ValueError, OSError, EOFError):
                continue
        return out

    def by_id(self) -> dict[int, WsRegion]:
        return {r.id: r for r in self.regions}

    def _next(self, attr: str) -> int:
        text = self.head + "".join(r.xml for r in self.regions) + self.tail + "".join(self._items)
        return max((int(x) for x in re.findall(rf'{attr}="(\d+)"', text)), default=0) + 1

    def _add_item(self, blob: bytes) -> int:
        iid = max(self._next("item-id"), self._next("item-idref"))
        data = base64.b64encode(gzip.compress(blob)).decode()
        self._items.append(f'<item item-id="{iid}" content-encoding="gzip">{data}</item>')
        return iid

    # ------------------------------------------------------------ edit

    def _make(self, r: Region, rid: int, left: float) -> WsRegion:
        h, asc, w = _estimate(r)
        if r.kind == "raw":
            reg = WsRegion.of(r.xml)
            if reg.height <= 0:
                reg.set(height=h, width=w)
            asc = reg.align_y - reg.top if "align-y" in reg.a else asc
            reg.set(region_id=rid, left=left, top=0.0, align_y=asc)
            return reg
        if r.kind == "plot":
            iid = self._add_item(build_blob(r.plot.xs, r.plot.ys, r.plot.xlim, r.plot.ylim))
            inner = f'<plot disable-calc="false" item-idref="{iid}"/>'
        elif r.kind == "text":
            inner = r.xml
        else:
            inner = f'<math optimize="false" disable-calc="false">{r.xml}</math>'
        return WsRegion.of(
            f'<region region-id="{rid}" left="{_fmt(left)}" top="0" width="{_fmt(w)}" height="{_fmt(h)}" '
            f'align-x="{_fmt(left)}" align-y="{_fmt(asc)}" show-border="false" show-highlight="false" '
            f'is-protected="true" z-order="0" background-color="inherit" tag="">{inner}</region>')

    def replace(self, start: int, end: int, regions: list[Region]) -> Plan:
        """Replace display regions start..end (1-based, inclusive) with `regions`.
        end == start-1 inserts before `start` (start == len+1 appends); empty `regions` deletes."""
        n = len(self.regions)
        if not (1 <= start <= n + 1 and start - 1 <= end <= n):
            raise WorksheetError(f"диапазон {start}..{end} вне листа (регионов: {n}); "
                                 f"вставка в конец: start={n + 1}, end={n}")
        old = self.regions[start - 1:end]
        if old:
            top, left = min(r.top for r in old), min(r.left for r in old)
            bottom = max(r.bottom for r in old)
            old_end, cut = bottom + GAP, bottom - 0.01
        elif start <= n:
            ref = self.regions[start - 1]
            row = next(row for row in rows(self.regions) if ref in row)
            top, left = min(r.top for r in row), ref.left
            old_end = cut = top
        else:
            top = max((r.bottom for r in self.regions), default=TOP - GAP) + GAP
            left = self.regions[-1].left if self.regions else LEFT
            old_end = cut = top
        rest = [r for r in self.regions if r not in old]
        below = {r.id for r in rest if r.top >= cut}
        rid = self._next("region-id")
        new = [self._make(r, rid + i, left) for i, r in enumerate(regions)]
        self.regions = rest + new
        y = self._stack([r.id for r in new], top, left)
        self._shift(below, y - old_end)
        return Plan([r.id for r in new], left, top, y)

    def _stack(self, ids: list[int], top: float, left: float) -> float:
        by = self.by_id()
        y = top
        for rid in ids:
            r = by.get(rid)
            if r is None:
                continue
            asc = r.align_y - r.top
            r.set(top=y, align_y=y + asc, left=left)
            y += r.height + GAP
        return y if ids else top

    def _shift(self, ids: set[int], d: float):
        if abs(d) > 0.001:
            for r in self.regions:
                if r.id in ids:
                    r.set(top=r.top + d, align_y=r.align_y + d)
        self.regions = order(self.regions)

    def apply(self, plan: Plan):
        """Restack the plan's regions with their current (Mathcad-measured) sizes; regions that
        were below the block move by the size difference."""
        if not plan.new_ids:
            return
        new = set(plan.new_ids)
        below = {r.id for r in self.regions if r.id not in new and r.top >= plan.end - GAP - 0.01}
        y = self._stack(plan.new_ids, plan.top, plan.left)
        self._shift(below, y - plan.end)
        plan.end = y

    def remove(self, ids: set[int]):
        """Drop regions and the binary items (renderings, plots) only they referenced."""
        gone = [r for r in self.regions if r.id in ids]
        self.regions = [r for r in self.regions if r.id not in ids]
        refs = {m for r in gone for m in re.findall(r'item-idref="(\d+)"', r.xml)}
        keep = set(re.findall(r'item-idref="(\d+)"', "".join(r.xml for r in self.regions)))
        for iid in refs - keep:
            self.tail = re.sub(rf'\s*<item item-id="{iid}"[^>]*>.*?</item>', "", self.tail, count=1, flags=re.S)
            self._items = [i for i in self._items if f'item-id="{iid}"' not in i]

    def walk(self):
        """(top-level region, nested-or-self <region> element) for every region in display order."""
        for r in self.regions:
            el = self.elem(r)
            for e in el.iter():
                if lname(e) == "region":
                    yield r, e


def lname(e: ET.Element) -> str:
    """Local tag name; worksheet10/30 and math10/20/30 files differ only in namespaces."""
    return e.tag.rsplit("}", 1)[-1] if isinstance(e.tag, str) else ""


def child(e: ET.Element, name: str) -> ET.Element | None:
    return next((c for c in e if lname(c) == name), None)


def error_names(ws: Worksheet, com) -> dict[int, str]:
    """Pair COM error messages (they carry the bad name) with the saved sheet's error regions.

    `com`: objects with .x_pt, .y_pt (region left/top in points) and .error ('' if none).
    Matched by position within 2 pt, leftovers in order. Returns region-id -> COM message."""
    xml = []
    for _, e in ws.walk():
        m = child(e, "math")
        if m is not None and m.get("error"):
            xml.append((int(float(e.get("region-id", "0"))), float(e.get("left", "0")), float(e.get("top", "0"))))
    errs = [c for c in com if c.error]
    out, used = {}, set()
    for rid, x, y in xml:
        k = next((i for i, c in enumerate(errs) if i not in used
                  and abs(c.x_pt - x) <= 2 and abs(c.y_pt - y) <= 2), None)
        if k is not None:
            used.add(k)
            out[rid] = errs[k].error
    rest = [c for i, c in enumerate(errs) if i not in used]
    todo = [rid for rid, _, _ in xml if rid not in out]
    if len(rest) == len(todo):
        out.update({rid: c.error for rid, c in zip(todo, rest)})
    return out
