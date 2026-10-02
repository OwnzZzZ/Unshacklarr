"""Build unshacklarr/static/unshackle-keys.json, the keys the unshackle.yaml editor suggests.

Reads an Unshackle checkout: the keys its config.py reads, and what its configuration reference
(docs/reference/configuration) says of each one and of their sub-keys.

    python tools/unshackle-keys.py ../unshackle
"""

import json
import re
import sys
from pathlib import Path

HEADING = re.compile(r"^(#{1,3}) (.*)$")
KEY = re.compile(r"^`([A-Za-z_][A-Za-z0-9_]*)`$")


def plain(text: str) -> str:
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)
    text = text.replace("`", "").replace("**", "").replace("*", "").replace("&nbsp;", " ")
    text = re.sub(r"\s+", " ", text).strip()
    first = re.match(r"(.+?\.)(\s|$)", text)
    return (first.group(1) if first else text)[:240]


def first_paragraph(lines: list[str]) -> str:
    fenced = False
    for line in lines:
        if line.startswith("```"):
            fenced = not fenced
        elif line.startswith("#"):
            break
        elif not fenced and line.strip() and not line.startswith(("-", "!", "|", " ")):
            return plain(line)
    return ""


def read_docs(folder: Path) -> dict:
    keys: dict = {}
    for page in sorted(folder.glob("*.md")):
        lines = page.read_text().splitlines()
        anchor = re.search(r"\{ #([a-z_]+) \}", lines[0]) if lines else None
        section = anchor.group(1) if anchor and anchor.group(1) in known else None
        columns = None
        for i, line in enumerate(lines):
            if m := HEADING.match(line):
                level, title = len(m.group(1)), re.sub(r"\s*\{.*\}$", "", m.group(2))
                if level == 2:
                    k = KEY.match(title)
                    section = k.group(1) if k else None
                    if section:
                        entry = keys.setdefault(section, {})
                        entry["info"] = first_paragraph(lines[i + 1:i + 30])
                        typ = re.search(r"\*\*Type:\*\* `([^`]+)`", "\n".join(lines[i + 1:i + 4]))
                        if typ:
                            entry["type"] = typ.group(1)
                columns = None
                continue
            if not line.startswith("|"):
                columns = None
                continue
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if columns is None:
                columns = [c.lower() for c in cells]
                continue
            k = KEY.match(cells[0]) if cells else None
            if not k or set(cells[0]) <= set("-: "):
                continue
            row = dict(zip(columns, cells))
            info = plain(cells[-1])
            typ = row.get("type", "").replace("`", "")
            entry = {"info": info, **({"type": typ} if typ else {})}
            if section:
                keys.setdefault(section, {}).setdefault("keys", {}).setdefault(k.group(1), entry)
            elif k.group(1) in known:
                keys.setdefault(k.group(1), {}).update(entry)
    return keys


if __name__ == "__main__":
    root = Path(sys.argv[1])
    source = (root / "unshackle/core/config.py").read_text()
    known = set(re.findall(r'kwargs\.get\("([a-z_]+)"', source)) | {"network"}
    keys = read_docs(root / "docs/reference/configuration")
    keys.get("key_vaults", {}).pop("keys", None)  # its table lists vault types, not keys
    catalog = {k: keys.get(k, {}) for k in sorted(known | set(keys))}
    out = Path(__file__).resolve().parent.parent / "unshacklarr/static/unshackle-keys.json"
    out.write_text(json.dumps(catalog, indent=1, sort_keys=True) + "\n")
    print(f"{len(catalog)} keys, {sum(len(v.get('keys', {})) for v in catalog.values())} sub-keys -> {out}")
