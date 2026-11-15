"""Style class check: finds the same bare class name defined in two CSS files.

A bare class (`.mi`, `.last:hover`, no parent and no second class) that sets layout
(position, display, width, height, grid) in two files moves things on the pages that load
both. That is what `.mi` and `.last` did on 14 and 15/11/2026.

Usage (standard library only):
    python -X utf8 scripts/elegxos_klaseon.py [--root <folder>] [--diff <ref>]

Check 1 always runs: the same bare class with layout rules in two or more files.
Check 2 runs with --diff <ref>: new bare classes with a name of up to 3 characters in the
added CSS lines of `git diff <ref>`.
Exit code: 0 = nothing outside KNOWN, 1 = something found or a file could not be read.
"""

import argparse
import re
import subprocess
import sys
from pathlib import Path

# Names that are known and do not break today. One comment per name: why.
KNOWN = {
    # Page styles load after app.css and override it on purpose (hidden on desktop, shown on
    # mobile); rafi, photo and stock are separate pages that never load together.
    "back",
    # app.css is the base; each page file loads after it and adjusts the tabs for that page.
    # On purpose today (audit 15/11/2026). Look at it by eye when a page changes.
    "tabs",
    # rafi.css and stock.css are never loaded on the same page.
    "tog",
    # photo.css (invoice photo page) and stock.css (camera and scan pages) are never loaded on
    # the same page, and both make the grid one column on mobile (architect, 15/11/2026).
    "g-cam",
    "g-par",
    # photo.css repeats the app.css rule word for word: hidden on desktop, flex on mobile.
    "only-m",
    # arxiki.css loads only on the home page, timologia.css only on the invoices page (audit
    # 15/11/2026). Rename it when the invoices page is rebuilt in the home B direction.
    "up",
}

LAYOUT_PROPS = {"position", "display", "width", "height", "grid", "grid-template-columns"}
BARE = re.compile(r"\.([A-Za-z_][\w-]*)(?::[a-zA-Z-]+(?:\([^)]*\))?)*")
BLOCK_AT = ("@media", "@supports", "@layer", "@container")


def strip_comments(text):
    """Remove /* */ comments but keep the line breaks, so line numbers stay right."""
    return re.sub(r"/\*.*?\*/", lambda m: "\n" * m.group(0).count("\n"), text, flags=re.S)


def parse_rules(text):
    """Yield (line, selector, property_names) for every style rule, also inside @media."""
    text = strip_comments(text)
    out = []
    # stack of (kind, start_index_of_prelude) while scanning
    i, n = 0, len(text)
    prelude_start = 0
    stack = []  # "group" (media...) or "skip" (keyframes...) or "rule"
    rule = None
    while i < n:
        c = text[i]
        if c == "{":
            prelude = text[prelude_start:i]
            stripped = prelude.strip()
            if stack and stack[-1] == "skip":
                stack.append("skip")
            elif stripped.lower().startswith(BLOCK_AT):
                stack.append("group")
            elif stripped.startswith("@"):
                stack.append("skip")
            elif stack and stack[-1] == "rule":
                stack.append("skip")  # nested block inside a rule: not used by this site
            else:
                lead = len(prelude) - len(prelude.lstrip())
                line = text.count("\n", 0, prelude_start + lead) + 1
                stack.append("rule")
                rule = [line, stripped, i + 1]
            prelude_start = i + 1
        elif c == "}":
            if stack:
                kind = stack.pop()
                if kind == "rule" and rule is not None:
                    body = text[rule[2]:i]
                    props = set()
                    for decl in body.split(";"):
                        if ":" in decl:
                            props.add(decl.split(":", 1)[0].strip().lower())
                    out.append((rule[0], rule[1], props))
                    rule = None
            prelude_start = i + 1
        elif c == ";" and (not stack or stack[-1] == "group"):
            prelude_start = i + 1  # @import and the like
        i += 1
    return out


def bare_class(piece):
    """The class name when the piece is exactly `.name` (or with pseudo-classes), else None."""
    m = BARE.fullmatch(piece.strip())
    return m.group(1) if m else None


def css_files(root):
    files = []
    for path in sorted(root.rglob("*.css")):
        if "vendor" in path.relative_to(root).parts:
            continue
        files.append(path)
    return files


def check_conflicts(root, label_root):
    """Returns (problems, bad_files). problems: {name: {file: first_line_with_layout}}."""
    defined = {}  # name -> {file: first line of a layout rule}
    bare_files = {}  # name -> set(files that define it bare)
    bad = []
    for path in css_files(root):
        label = path.relative_to(label_root).as_posix()
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            print(f"ΣΦΑΛΜΑ: {label} ({exc})")
            bad.append(label)
            continue
        for line, selector, props in parse_rules(text):
            for piece in selector.split(","):
                name = bare_class(piece)
                if name is None:
                    continue
                bare_files.setdefault(name, set()).add(label)
                if props & LAYOUT_PROPS:
                    defined.setdefault(name, {}).setdefault(label, line)
    problems = {name: files for name, files in defined.items()
                if len(files) >= 2 and len(bare_files[name]) >= 2}
    return problems, bad


def check_small_names(ref):
    """New bare classes of up to 3 characters in the added CSS lines. Returns [(name, file)]."""
    proc = subprocess.run(["git", "diff", "-U0", ref, "--", "*.css"],
                          capture_output=True, text=True, encoding="utf-8")
    if proc.returncode != 0:
        print(f"ΣΦΑΛΜΑ: git diff {ref} ({proc.stderr.strip()})")
        return None
    found = []
    current = None
    for raw in proc.stdout.splitlines():
        if raw.startswith("+++ "):
            current = raw[4:].removeprefix("b/")
            continue
        if not raw.startswith("+") or raw.startswith("+++"):
            continue
        line = strip_comments(raw[1:])
        pieces = []
        segments = re.split(r"([{}])", line)
        for k, seg in enumerate(segments):
            if k + 1 < len(segments) and segments[k + 1] == "{":
                pieces.extend(seg.split(","))
        if line.rstrip().endswith(","):
            pieces.extend(line.rstrip().rstrip(",").split(","))
        for piece in pieces:
            name = bare_class(piece)
            if name and len(name) <= 3 and (name, current) not in found:
                found.append((name, current))
    return found


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--root", default=None, help="folder with the CSS files (default: xangx/)")
    parser.add_argument("--diff", default=None, help="git ref to compare with, for new short names")
    args = parser.parse_args(argv)

    repo = Path(__file__).resolve().parents[1]
    root = Path(args.root).resolve() if args.root else repo / "xangx"
    if not root.is_dir():
        print(f"ΣΦΑΛΜΑ: {root} (δεν είναι φάκελος)")
        return 1
    label_root = root if args.root else repo

    problems, bad = check_conflicts(root, label_root)
    found = 0
    skipped = []
    for name in sorted(problems):
        if name in KNOWN:
            skipped.append(name)
            continue
        where = ", ".join(f"{f}:{l}" for f, l in sorted(problems[name].items()))
        print(f"ΣΥΓΚΡΟΥΣΗ .{name}: {where}")
        found += 1

    if args.diff:
        small = check_small_names(args.diff)
        if small is None:
            bad.append("git diff")
        else:
            for name, file in small:
                print(f"ΜΙΚΡΟ ΟΝΟΜΑ .{name}: {file}")
                found += 1

    print("γνωστά: " + (", ".join("." + k for k in sorted(KNOWN)) or "-"))
    if bad:
        print(f"ΔΕΝ ΟΛΟΚΛΗΡΩΘΗΚΕ: {len(bad)} αρχεία δεν διαβάστηκαν")
        return 1
    if found:
        print(f"ΒΡΕΘΗΚΑΝ {found}")
        return 1
    print("OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
