"""Optional .xmp sidecar files so names, tags and descriptions travel with
your photos to other apps (Immich, digiKam, Lightroom...)."""
import logging
from pathlib import Path
from xml.sax.saxutils import escape

from . import db

log = logging.getLogger("easyphotos.xmp")


def sidecar_path(lib_path: str, rel: str) -> Path:
    p = Path(lib_path) / rel
    return p.with_name(p.name + ".xmp")


def write_sidecar(asset_id: int) -> None:
    a = db.q1("SELECT a.*, l.path AS lib_path FROM assets a JOIN libraries l ON l.id=a.library_id WHERE a.id=?", (asset_id,))
    if a is None or a["deleted_at"] is not None:
        return
    tags = [r["name"] for r in db.q(
        "SELECT t.name FROM asset_tags at JOIN tags t ON t.id=at.tag_id WHERE at.asset_id=? ORDER BY t.name", (asset_id,))]
    faces = db.q("SELECT f.x1, f.y1, f.x2, f.y2, p.name FROM faces f JOIN persons p ON p.id=f.person_id "
                 "WHERE f.asset_id=? AND f.rejected=0 AND p.name IS NOT NULL", (asset_id,))
    names = sorted({f["name"] for f in faces})
    parts = []
    if a["description"]:
        parts.append(f'<dc:description><rdf:Alt><rdf:li xml:lang="x-default">{escape(a["description"])}</rdf:li></rdf:Alt></dc:description>')
    if tags:
        parts.append("<dc:subject><rdf:Bag>" + "".join(f"<rdf:li>{escape(t)}</rdf:li>" for t in tags) + "</rdf:Bag></dc:subject>")
    if names:
        parts.append("<Iptc4xmpExt:PersonInImage><rdf:Bag>" + "".join(f"<rdf:li>{escape(n)}</rdf:li>" for n in names) +
                     "</rdf:Bag></Iptc4xmpExt:PersonInImage>")
    if a["date_source"] == "manual" and a["taken_at"]:
        parts.append(f"<exif:DateTimeOriginal>{escape(a['taken_at'])}</exif:DateTimeOriginal>")
    if faces and a["width"] and a["height"]:
        regs = []
        for f in faces:
            w, h = f["x2"] - f["x1"], f["y2"] - f["y1"]
            regs.append(
                "<rdf:li rdf:parseType=\"Resource\">"
                f"<mwg-rs:Name>{escape(f['name'])}</mwg-rs:Name><mwg-rs:Type>Face</mwg-rs:Type>"
                f"<mwg-rs:Area stArea:x=\"{(f['x1'] + w / 2):.5f}\" stArea:y=\"{(f['y1'] + h / 2):.5f}\" "
                f"stArea:w=\"{w:.5f}\" stArea:h=\"{h:.5f}\" stArea:unit=\"normalized\"/>"
                "</rdf:li>")
        parts.append(
            "<mwg-rs:Regions rdf:parseType=\"Resource\">"
            f"<mwg-rs:AppliedToDimensions stDim:w=\"{a['width']}\" stDim:h=\"{a['height']}\" stDim:unit=\"pixel\"/>"
            "<mwg-rs:RegionList><rdf:Bag>" + "".join(regs) + "</rdf:Bag></mwg-rs:RegionList></mwg-rs:Regions>")
    xml = (
        '<?xpacket begin="﻿" id="W5M0MpCehiHzreSzNTczkc9d"?>\n'
        '<x:xmpmeta xmlns:x="adobe:ns:meta/" x:xmptk="EasyPhotos">\n'
        '<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">\n'
        '<rdf:Description rdf:about="" xmlns:dc="http://purl.org/dc/elements/1.1/" '
        'xmlns:exif="http://ns.adobe.com/exif/1.0/" '
        'xmlns:Iptc4xmpExt="http://iptc.org/std/Iptc4xmpExt/2008-02-29/" '
        'xmlns:mwg-rs="http://www.metadataworkinggroup.com/schemas/regions/" '
        'xmlns:stArea="http://ns.adobe.com/xmp/sType/Area#" '
        'xmlns:stDim="http://ns.adobe.com/xap/1.0/sType/Dimensions#">\n'
        + "\n".join(parts) +
        '\n</rdf:Description>\n</rdf:RDF>\n</x:xmpmeta>\n<?xpacket end="w"?>\n')
    dest = sidecar_path(a["lib_path"], a["path"])
    try:
        if not parts:
            if dest.exists():
                dest.unlink()
            return
        dest.write_text(xml, encoding="utf-8")
    except OSError as e:
        log.warning("Cannot write %s: %s", dest, e)


def mark_dirty(asset_ids) -> None:
    ids = list(asset_ids)
    if not ids:
        return
    for i in range(0, len(ids), 500):
        chunk = ids[i:i + 500]
        db.ex(f"UPDATE assets SET xmp_dirty=1 WHERE id IN ({','.join('?' * len(chunk))})", chunk)
