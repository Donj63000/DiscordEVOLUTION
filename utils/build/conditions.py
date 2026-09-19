"""Grammaire bornée AND/OR/comparaisons : jamais eval, exec ou substitution de code."""
from __future__ import annotations
from enum import Enum
import operator
import re
import unicodedata


class Truth(str, Enum):
    TRUE = "true"
    FALSE = "false"
    UNKNOWN = "unknown"


def tri_and(items):
    items = tuple(items)
    return Truth.FALSE if Truth.FALSE in items else Truth.UNKNOWN if Truth.UNKNOWN in items else Truth.TRUE


def tri_or(items):
    items = tuple(items)
    return Truth.TRUE if Truth.TRUE in items else Truth.UNKNOWN if Truth.UNKNOWN in items else Truth.FALSE


def norm(text):
    text = "".join(c for c in unicodedata.normalize("NFKD", text.lower()) if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", text.replace("’", "'")).strip()


SYMBOLS = {
    "niveau": "level", "level": "level", "pl": "level", "force": "fo", "cs": "fo",
    "intelligence": "ine", "ci": "ine", "chance": "cha", "cc": "cha",
    "agilite": "age", "ca": "age", "vitalite": "vi", "cv": "vi", "sagesse": "sa", "cw": "sa",
    "pa": "pa", "pm": "pm", "portee": "po", "grade": "grade", "alignement": "alignment",
    "classe": "class_id", "pg": "class_id", "ps": "alignment", "pr": "grade",
}
OPS = {">": operator.gt, ">=": operator.ge, "<": operator.lt, "<=": operator.le, "=": operator.eq, "==": operator.eq, "!=": operator.ne, "!": operator.ne, "~": operator.ne}
TOKEN = re.compile(r"\s*(>=|<=|==|!=|[<>=!~()&|]|-?\d+|[a-z_]+)\s*")


def parse(text):
    if not isinstance(text, str) or len(text) > 600:
        return ("unknown",)
    text = norm(text)
    if not text or text in {"aucune", "aucune condition", "sans condition"}:
        return ("true",)
    if text == "bi=non equipable":
        return ("false",)
    from .models import CLASSES
    for identifier, classe in enumerate(CLASSES, 1):
        text = re.sub(r"\bclasse\s+non\s+" + classe + r"\b", f"classe != {identifier}", text)
        text = re.sub(r"\bclasse\s*=\s*" + classe + r"\b", f"classe = {identifier}", text)
    for alignment, identifier in {"neutre": 0, "bontarien": 1, "brakmarien": 2, "mercenaire": 3}.items():
        text = re.sub(r"\balignement\s*=\s*" + alignment + r"\b", f"alignement = {identifier}", text)
    text = re.sub(r"(?<=\d)\s+(?=[a-z_]+\s*(?:[<>=!~]))", " & ", text)
    tokens, position = [], 0
    while position < len(text):
        match = TOKEN.match(text, position)
        if not match or len(tokens) >= 128:
            return ("unknown",)
        value = match.group(1)
        tokens.append({"et": "&", "and": "&", "ou": "|", "or": "|"}.get(value, value))
        position = match.end()
    cursor = 0

    def atom(depth):
        nonlocal cursor
        if depth > 16 or cursor >= len(tokens):
            raise ValueError
        if tokens[cursor] == "(":
            cursor += 1
            node = expression(depth + 1)
            if cursor >= len(tokens) or tokens[cursor] != ")":
                raise ValueError
            cursor += 1
            return node
        if cursor + 2 >= len(tokens):
            raise ValueError
        symbol, op, target = tokens[cursor:cursor + 3]
        cursor += 3
        if op not in OPS or not re.fullmatch(r"-?\d{1,7}", target):
            raise ValueError
        return ("cmp", SYMBOLS.get(symbol, "unknown:" + symbol), op, int(target))

    def conjunction(depth):
        nonlocal cursor
        nodes = [atom(depth)]
        while cursor < len(tokens) and tokens[cursor] == "&":
            cursor += 1
            nodes.append(atom(depth))
        return nodes[0] if len(nodes) == 1 else ("and", tuple(nodes))

    def expression(depth):
        nonlocal cursor
        nodes = [conjunction(depth)]
        while cursor < len(tokens) and tokens[cursor] == "|":
            cursor += 1
            nodes.append(conjunction(depth))
        return nodes[0] if len(nodes) == 1 else ("or", tuple(nodes))

    try:
        node = expression(0)
        return node if cursor == len(tokens) else ("unknown",)
    except (ValueError, RecursionError):
        return ("unknown",)


def evaluate(node, context):
    kind = node[0]
    if kind == "true":
        return Truth.TRUE
    if kind == "false":
        return Truth.FALSE
    if kind == "cmp":
        _, symbol, op, expected = node
        actual = context.get(symbol)
        if type(actual) is not int:
            return Truth.UNKNOWN
        return Truth.TRUE if OPS[op](actual, expected) else Truth.FALSE
    if kind in {"and", "or"}:
        return (tri_and if kind == "and" else tri_or)(evaluate(n, context) for n in node[1])
    return Truth.UNKNOWN


def recognized(node):
    """Je distingue une syntaxe connue d'une valeur de personnage encore absente."""
    if node[0] in {"true", "false"}:
        return True
    if node[0] == "cmp":
        return not node[1].startswith("unknown:")
    if node[0] in {"and", "or"}:
        return all(recognized(child) for child in node[1])
    return False
