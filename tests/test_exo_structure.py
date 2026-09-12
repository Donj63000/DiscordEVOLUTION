"""Controles structurels sans charger le SDK Discord."""
import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def tree():
    return ast.parse((ROOT / "exo.py").read_text(encoding="utf-8"))


def test_modal_literal_labels_fit_discord():
    labels = [
        node.args[1].value for node in ast.walk(tree())
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        and node.func.attr == "add_input" and len(node.args) > 1
        and isinstance(node.args[1], ast.Constant)
        and isinstance(node.args[1].value, str)
    ]
    assert len(labels) >= 15
    assert all(len(label.encode("utf-16-le")) // 2 <= 45 for label in labels)


def test_modal_titles_fit_discord():
    modal = next(node for node in tree().body
                 if isinstance(node, ast.ClassDef) and node.name == "ExoModal")
    titles = next(
        node.value for node in ast.walk(modal)
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "titles" for target in node.targets)
    )
    assert all(len(title) <= 45 for title in ast.literal_eval(titles).values())


def test_all_optional_command_options_are_preserved():
    command = next(node for node in ast.walk(tree())
                   if isinstance(node, ast.AsyncFunctionDef) and node.name == "exo")
    assert [arg.arg for arg in command.args.args] == [
        "self", "interaction", "objet", "objectif", "reprise",
    ]
    assert len(command.args.defaults) == 3
    assert all(isinstance(value, ast.Constant) and value.value is None
               for value in command.args.defaults)


def test_production_modules_compile_without_importing_discord():
    paths = [ROOT / "exo.py", *sorted((ROOT / "utils").glob("exo*.py"))]
    for path in paths:
        compile(path.read_text(encoding="utf-8"), str(path), "exec")
