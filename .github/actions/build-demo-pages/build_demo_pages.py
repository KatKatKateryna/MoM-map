#!/usr/bin/env python3
"""Build a customized index.html for each demo_* folder under sub_pages/.

For every sub_pages/demo_* folder containing a replace.txt and/or add.txt, this
copies the root index.html into that folder and then:
  - for each "key = value" line in replace.txt, overwrites the line in the copy
    whose text before " = " matches that key;
  - inserts the lines of add.txt at the end of the Config script, just before
    the first "</script>" that follows the "// ── Config" line.
A <base> tag is inserted so the copy's relative references to css/, js/,
assets/ and data/ still resolve to the site root once the page is deployed
one level down from it (see the "Prepare deployment" step, which publishes
each sub_pages/demo_*/index.html back at the site's top level).
"""
import pathlib

ROOT = pathlib.Path.cwd()
INDEX_HTML = ROOT / "index.html"
SUB_PAGES_DIR = ROOT / "sub_pages"
CONFIG_MARKER = "// ── Config"


def line_key(line: str) -> str:
    return line.split(" = ")[0]


def read_lines(path: pathlib.Path) -> list[str]:
    if not path.is_file():
        return []
    return path.read_text(encoding="utf-8").splitlines()


def build_demo_page(demo_dir: pathlib.Path, replace_lines: list[str], add_lines: list[str]) -> None:
    dest = demo_dir / "index.html"

    replacements = {line_key(line): line for line in replace_lines if line.strip()}

    lines = INDEX_HTML.read_text(encoding="utf-8").splitlines(keepends=True)
    ending = "\r\n" if lines and lines[0].endswith("\r\n") else "\n"
    insert_at = None
    in_config = False
    for i, line in enumerate(lines):
        text = line.rstrip("\r\n")

        replacement = replacements.get(line_key(text))
        if replacement is not None:
            lines[i] = replacement + line[len(text):]

        if text.startswith(CONFIG_MARKER):
            in_config = True
        elif in_config and insert_at is None and text.strip() == "</script>":
            insert_at = i

    if add_lines:
        if insert_at is None:
            raise SystemExit(f"{demo_dir}: no '</script>' after '{CONFIG_MARKER}' in index.html for add.txt")
        lines[insert_at:insert_at] = [line + ending for line in add_lines]

    content = "".join(lines).replace("<head>", '<head>\n  <base href="../">', 1)
    dest.write_text(content, encoding="utf-8")
    print(f"Built {dest}")


def main() -> None:
    if not SUB_PAGES_DIR.is_dir():
        return
    for demo_dir in sorted(SUB_PAGES_DIR.glob("demo_*")):
        if not demo_dir.is_dir():
            continue
        replace_file = demo_dir / "replace.txt"
        add_file = demo_dir / "add.txt"
        if not replace_file.is_file() and not add_file.is_file():
            continue
        build_demo_page(demo_dir, read_lines(replace_file), read_lines(add_file))


if __name__ == "__main__":
    main()
