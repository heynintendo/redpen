"""A tiny YAML reader for the .redpen.yml rule schema (stdlib-only).

Deliberately minimal: it parses a top-level mapping where one key (``rules``) is
a block list of flat mappings of scalar values. That is the entire .redpen.yml
schema, so we don't need a YAML dependency. Quoted strings, ints and booleans
are understood; full-line ``#`` comments are ignored. Anything fancier is out of
scope -- use ``.redpen.json`` for exact control.
"""

from __future__ import annotations


def _scalar(v: str):
    v = v.strip()
    if (len(v) >= 2) and ((v[0] == v[-1] == '"') or (v[0] == v[-1] == "'")):
        return v[1:-1]
    low = v.lower()
    if low in ("true", "yes"):
        return True
    if low in ("false", "no"):
        return False
    if low in ("null", "~", ""):
        return None
    try:
        return int(v)
    except ValueError:
        return v


def _split_kv(s: str) -> tuple[str, str]:
    key, _, val = s.partition(":")
    return key.strip(), val.strip()


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip())


def parse(text: str) -> dict:
    """Parse the restricted schema into a dict (with a list under 'rules')."""
    lines = text.splitlines()
    result: dict = {}
    i, n = 0, len(lines)
    while i < n:
        raw = lines[i]
        s = raw.strip()
        i += 1
        if not s or s.startswith("#"):
            continue
        key, val = _split_kv(s)
        if val == "":
            # A block: collect list items ("- ...") indented under this key.
            items: list[dict] = []
            while i < n:
                l2 = lines[i]
                if not l2.strip() or l2.strip().startswith("#"):
                    i += 1
                    continue
                if not l2.strip().startswith("-"):
                    break  # dedented back out of the block
                item_indent = _indent(l2)
                item: dict = {}
                first = l2.strip()[1:].strip()
                if first:
                    k, v = _split_kv(first)
                    item[k] = _scalar(v)
                i += 1
                while i < n:  # remaining keys of this item, more-indented
                    l3 = lines[i]
                    if not l3.strip() or l3.strip().startswith("#"):
                        i += 1
                        continue
                    if _indent(l3) <= item_indent or l3.strip().startswith("-"):
                        break
                    k, v = _split_kv(l3.strip())
                    item[k] = _scalar(v)
                    i += 1
                items.append(item)
            result[key] = items
        else:
            result[key] = _scalar(val)
    return result
