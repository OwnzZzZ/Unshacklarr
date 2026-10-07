"""The English texts a person sees, for translating: python tools/i18n/extract.py

Reads unshacklarr/static/index.html (its markup, then its scripts' string literals, js/*.js) and the server's
messages (its strings and f-strings), and writes
unshacklarr/static/i18n/en.json: every text a user may see, as the page shows it. A template
literal's ${…} parts become {0}, {1}…, as the page matches them. The translations,
unshacklarr/static/i18n/<lang>.json, map these same texts to theirs.
"""

import ast
import json
import re
import sys
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PAGE = ROOT / "unshacklarr" / "static" / "index.html"
OUT = ROOT / "unshacklarr" / "static" / "i18n" / "en.json"
ATTRS = {"placeholder", "title", "aria-label", "alt", "data-tip"}
SKIP_TAGS = {"style", "script", "code", "pre", "svg"}


def norm(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


CSS_CLASSES: set[str] = set()  # the page's own, read from its <style>


def wanted(text: str) -> bool:
    """A text for a person, not code: letters outside the {n}, and not an identifier, a selector or a path."""
    bare = re.sub(r"\{\d+\}", "", text)
    if not re.search(r"[A-Za-z]{2}", bare) or "<" in text or "=>" in text:
        return False
    if re.fullmatch(r"[a-z0-9_:.\-/]+", text):  # an identifier, a CSS class, a key, a path
        return False
    if re.fullmatch(r"([a-z0-9-]+ ?)+", text) and all(w in CSS_CLASSES or "-" in w for w in text.split()):
        return False  # "btn small primary": classes
    if re.match(r"^[#.\[/&]|^https?:|^\w+://|^--|^[a-z]+\(|^\$|^%|^@media", text):
        return False
    if "{" in text and not re.search(r"\s", text):  # run={0}, {0}.wvd, S{0}E{1}: a key or a name, built
        return False
    if re.search(r"\(\?[:!=]|\$$|\\[dswSb]|\{\d+\}(?:/[^\s/]+){2,}|\{\d+\}/api/|\{\d+\}://|\w\[[\"']|\w\(\"", text):  # a pattern, a URL built, code
        return False
    if re.match(r"^[\"'][^\"']+[\"']:", text):  # "Device Type": PLAYREADY: YAML, as unshackle.yaml has it
        return False
    if re.fullmatch(r"X-[\w-]+|[A-Z][a-z]+(-[A-Z][a-z]+)+|[a-z]+/[\w.+-]+|\*\.\w+", text):  # a header, a MIME type, a glob
        return False
    if re.match(r"^[\^?]|^(from|import) \w|^docker ", text):  # a pattern, a query, Python, a command to copy
        return False
    if re.search(r"\b\d+px\b|^[a-z]+-[a-z-]+:\s|\b(sans-serif|monospace)\b", text):  # CSS
        return False
    if re.search(r"[{}]\s*$|;\s*$|^\s*[)\]]|var\(--|^\((max|min)-|\w:\S+;", bare):
        return False
    return True


class Markup(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.found: set[str] = set()
        self.skipping = 0

    def handle_starttag(self, tag, attrs):
        if tag in SKIP_TAGS:
            self.skipping += 1
        for name, value in attrs:
            if name in ATTRS and value and wanted(norm(value)):
                self.found.add(norm(value))

    def handle_endtag(self, tag):
        if tag in SKIP_TAGS and self.skipping:
            self.skipping -= 1

    def handle_data(self, data):  # the markup's text is all for people: "every", "days" included
        if not self.skipping and re.search(r"[A-Za-z]{2}", data):
            self.found.add(norm(data))


CODE_BEFORE = re.compile(r"(className|type|kind|mode|role|inputMode|autocomplete|rel|target|method|key|dataset|href|src|name|value|id)\s*:\s*$"
                         r"|(classList\.\w+|querySelector(All)?|closest|matches|\$|getElementById|setAttribute|getAttribute|removeAttribute"
                         r"|addEventListener|api|fetch|startsWith|endsWith|split|join|replace(All)?|includes|localStorage\.\w+)\(\s*$|[\w)\]]\[\s*$")  # x["key"], not an array


BRANCHES: set[str] = set()  # a ternary's choices, what a textContent or a toast shows: for a person, however short


def literals(code: str, holes_of: dict | None = None) -> list[str]:
    """Every string literal of a script, "a" + "b" joined, a template's ${…} as {n}; not those handed to code
    (a class name, a selector, an API path). holes_of gets each template's {n} expressions."""
    out: list[str] = []
    holes_of = {} if holes_of is None else holes_of
    i, n, last = 0, len(code), ""  # last: the previous significant character, for telling a regex from a division

    def string(q: str) -> tuple[str, int]:
        nonlocal i
        j, buf = i + 1, []
        while j < n and code[j] != q:
            if code[j] == "\\":
                nxt = code[j + 1]
                buf.append({"n": "\n", "t": "\t"}.get(nxt, nxt))
                j += 2
                continue
            buf.append(code[j])
            j += 1
        return "".join(buf), j + 1

    def template() -> tuple[str, int]:
        nonlocal exprs
        j, buf, holes = i + 1, [], 0
        while j < n and code[j] != "`":
            if code[j] == "\\":
                buf.append({"n": "\n", "t": "\t"}.get(code[j + 1], code[j + 1])); j += 2; continue
            if code.startswith("${", j):
                depth, k = 1, j + 2
                while k < n and depth:
                    depth += {"{": 1, "}": -1}.get(code[k], 0)
                    k += 1
                out.extend(literals(code[j + 2:k - 1], holes_of))  # the strings inside the hole too
                exprs.append(norm(code[j + 2:k - 1])[:120])
                buf.append(f"{{{holes}}}"); holes += 1
                j = k
                continue
            buf.append(code[j]); j += 1
        return "".join(buf), j + 1

    pending: str | None = None  # a literal waiting for a "+ literal"
    exprs: list[str] = []
    start = 0
    while i < n:
        c = code[i]
        if code.startswith("//", i):
            i = code.find("\n", i); i = n if i < 0 else i; continue
        if code.startswith("/*", i):
            i = code.find("*/", i) + 2; continue
        if c in "'\"`":
            if pending is None:
                start, exprs = i, []
            value, i = template() if c == "`" else string(c)
            if pending is not None:
                shift = len(re.findall(r"\{\d+\}", pending))
                value = pending + re.sub(r"\{(\d+)\}", lambda m: f"{{{int(m.group(1)) + shift}}}", value)
            j = i
            while j < n and code[j] in " \t\n":
                j += 1
            if j < n and code[j] == "+" and code[j + 1:j + 2] != "+":
                k = j + 1
                while k < n and code[k] in " \t\n":
                    k += 1
                if k < n and code[k] in "'\"`":
                    pending, i = value, k
                    continue
            before = code[max(0, start - 60):start]
            if re.search(r"\?\s*$|[\"'`)]\s*:\s*$|\b(textContent|title|ariaLabel|placeholder|sub)\s*:\s*$|\btoast\(\s*$", before):
                BRANCHES.add(norm(value))  # cond ? "all" : "these": words for a person, however short
            if not CODE_BEFORE.search(before):
                out.append(value)
                if exprs:
                    holes_of[norm(value)] = exprs
            pending = None; last = c
            continue
        if c == "/" and last in "(,=:[!&|?{};+-*%<>~^" or (c == "/" and last == "" ):  # a regex literal
            j, cls = i + 1, False
            while j < n and (code[j] != "/" or cls):
                if code[j] == "\\": j += 1
                elif code[j] == "[": cls = True
                elif code[j] == "]": cls = False
                j += 1
            i = j + 1; last = "/"; continue
        if not c.isspace():
            last = c if not (c.isalnum() or c in "_$)]") else "a"
            if code.startswith("return", i) and not (code[i + 6:i + 7].isalnum()):
                last = "("; i += 6; continue
        i += 1
    return out


def extract(page: str) -> dict[str, list[str]]:
    """{text: the code each of its {n} stands for (for the translator)}."""
    scripts = re.findall(r"<script>(.*?)</script>", page, re.S)
    script = max(scripts, key=len)  # the page's own; the small one in <head> has no text
    CSS_CLASSES.update(re.findall(r"\.([a-z][\w-]*)", " ".join(re.findall(r"<style>(.*?)</style>", page, re.S))))
    markup = Markup()
    markup.feed(re.sub(r"<script>.*?</script>", "", page, flags=re.S))
    holes: dict[str, list[str]] = {}
    found = markup.found | {norm(s) for s in literals(script, holes)
                            if wanted(norm(s)) or (norm(s) in BRANCHES and re.fullmatch(r"[a-z]{2,}( [a-z]+)*", norm(s))
                                                   and (" " in norm(s) or norm(s) not in CSS_CLASSES))}
    return {t: holes.get(t, []) for t in sorted(found)}


SERVER = ["sync.py", "web.py", "backend.py", "cdm.py", "cookies.py", "options.py", "push.py"]


def said(text: str) -> bool:
    """wanted(), and not a camelCase key, a CONSTANT, nor the terminal's colours: the server has more of those."""
    return wanted(text) and not re.fullmatch(r"[a-z]+[A-Z]\w*|[A-Z0-9_]+", text) and "\x1b" not in text


NOT_TEXT = {"LAUNCHER", "SECURITY_HEADERS", "EXAMPLE_KEYS", "UPDATE_HEADERS", "TMDB_HEADERS", "SITES", "NETWORKS"}  # the server's constants that hold code, not words


def server_texts(source: str) -> dict[str, list[str]]:
    """What the server says to a person (errors the page shows, notifications, Activity's steps): its strings
    and f-strings, an f-string's {…} as {n}; not docstrings, nor what print() writes to the log."""
    tree = ast.parse(source)
    skip = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(getattr(t, "id", "") in NOT_TEXT for t in node.targets):
            skip.update(id(n) for n in ast.walk(node.value))  # code or header values, never shown
        body = getattr(node, "body", None)
        if isinstance(body, list) and body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
            skip.add(id(body[0].value))  # a docstring
        if isinstance(node, ast.Call) and getattr(node.func, "id", "") in ("print", "getattr"):
            skip.update(id(n) for a in node.args for n in ast.walk(a))
        if isinstance(node, ast.Call) and getattr(getattr(node.func, "value", None), "id", "") == "re":  # re.compile(…): a pattern
            skip.update(id(n) for a in node.args for n in ast.walk(a))
        if isinstance(node, ast.Subscript):
            skip.update(id(n) for n in ast.walk(node.slice))
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):  # Path.home() / "Library": a path
            skip.update(id(n) for n in ast.walk(node))
        if isinstance(node, ast.JoinedStr):  # its own pieces make one text; the strings in its {…} are their own
            skip.update(id(n) for n in node.values if isinstance(n, ast.Constant))
            skip.update(id(n) for v in node.values if isinstance(v, ast.FormattedValue) and v.format_spec for n in ast.walk(v.format_spec))
    inside = {id(c) for f in ast.walk(tree) if isinstance(f, ast.FormattedValue) for n in ast.walk(f.value)  # what fills a {…}:
              for c in (n.values if isinstance(n, ast.BoolOp) else [n.body, n.orelse] if isinstance(n, ast.IfExp) else [])}  # x or "…", "…" if y else "…"
    found: dict[str, list[str]] = {}
    for node in ast.walk(tree):
        if id(node) in skip:
            continue
        if id(node) in inside and isinstance(node, ast.Constant) and isinstance(node.value, str) \
                and re.fullmatch(r"[a-z]{2,}( [a-z]+)*", norm(node.value)):
            found.setdefault(norm(node.value), [])  # f"… {x or 'completed'}": a word the message shows
            continue
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            for line in node.value.split("\n"):
                if said(norm(line)):
                    found.setdefault(norm(line), [])
        elif isinstance(node, ast.JoinedStr):
            text, holes = "", []
            for part in node.values:
                if isinstance(part, ast.Constant):
                    text += str(part.value)
                else:
                    text += f"{{{len(holes)}}}"
                    holes.append(ast.unparse(part.value)[:120])
            for line in text.split("\n"):  # a message is translated line by line
                used = [int(n) for n in re.findall(r"\{(\d+)\}", line)]
                renumbered = re.sub(r"\{(\d+)\}", lambda m: f"{{{used.index(int(m.group(1)))}}}", line)
                if said(norm(renumbered)):
                    found[norm(renumbered)] = [holes[n] for n in used]
    return found


def whole_page() -> str:
    """The page as one file again: its stylesheet and its scripts (app.css, js/*.js) put back in place."""
    page = PAGE.read_text()
    page = page.replace('<link rel="stylesheet" href="/app.css">', f"<style>{(PAGE.parent / 'app.css').read_text()}</style>")
    tags = re.findall(r'<script src="/js/([\w-]+\.js)"></script>\n?', page)
    script = "".join((PAGE.parent / "js" / name).read_text() for name in tags)
    page = re.sub(r'<script src="/js/[\w-]+\.js"></script>\n?', "", page)
    return page.replace("</body>", f"<script>{script}</script>\n</body>", 1)


def main():
    texts = extract(whole_page())
    for name in SERVER:
        for text, holes in server_texts((ROOT / "unshacklarr" / name).read_text()).items():
            texts.setdefault(text, holes)
    texts = dict(sorted(texts.items()))
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps(texts, ensure_ascii=False, indent=1) + "\n")
    print(f"{len(texts)} texts in {OUT.relative_to(ROOT)}", file=sys.stderr)


if __name__ == "__main__":
    main()
