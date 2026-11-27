"""Extract visible HTML and literal route messages for the claim and pairing pages."""

import ast
import inspect
import json
import sys
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from switchboard import pages


class VisibleHTML(HTMLParser):
    void = set("area base br col embed hr img input link meta param source track wbr".split())
    inert = {"script", "style", "template"}
    copy_attributes = {"alt", "aria-description", "aria-label", "placeholder", "title"}

    def __init__(self):
        super().__init__()
        self.tags = []
        self.text = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if not any(self.tags) and tag not in self.inert and "hidden" not in attrs:
            self.text.extend(value for name, value in attrs.items() if name in self.copy_attributes and value)
        if tag not in self.void:
            self.tags.append(tag in self.inert or "hidden" in attrs)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_endtag(self, tag):
        if tag not in self.void:
            self.tags.pop()

    def handle_data(self, data):
        if data.strip() and not any(self.tags):
            self.text.append(data)


def rendered_pages():
    test_time = {"host": "swb.one", "expires_at": 120, "now": 60}
    routine = {"project": "example", "title": "Example", "routine_id": "trig_example",
               "new_project": True, "primary": True, "created_by": "agent:claude", "expires_at": 120}
    yield pages.claim_page, pages.claim_page("Title", "Scope", "csrf", owner_key=True)
    yield pages.claim_message_page, pages.claim_message_page("Title", status=200)
    for name in (*pages.CLAIM_CONNECTORS, "openai", "unknown"):
        for require_https in (False, True):
            yield pages.token_claim_page, pages.token_claim_page(
                f"{name.upper()}_KEY", "csrf", require_https=require_https, **test_time)
    yield pages.routine_claim_page, pages.routine_claim_page(routine, "csrf", host="swb.one", now=60)
    yield pages.connect_code_page, pages.connect_code_page(
        {"code": "example", "agent": "Example", "project": "example"}, "https://swb.one")
    for error in ("link_expired", "passkey_failed", "oidc_failed"):
        yield pages.login_page, pages.login_page({"passkeys": True, "oidc": {"name": "Example"}}, "/", error)
    yield pages.login_page, pages.login_page({"passkeys": False, "oidc": None}, "/", None)


def literal_parts(node):
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        yield node.value
    elif isinstance(node, ast.JoinedStr):
        yield "".join(value.value if isinstance(value, ast.Constant) else " […] "
                      for value in node.values)
    elif isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        for left in literal_parts(node.left):
            for right in literal_parts(node.right):
                yield left + right
    elif isinstance(node, ast.IfExp):
        yield from literal_parts(node.body)
        yield from literal_parts(node.orelse)
    elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "get":
        if len(node.args) == 2:
            yield from literal_parts(node.args[1])
    else:
        yield " […] "


def scoped_literals():
    routes = {
        "app.py": {"claim_form", "claim_page_access", "token_claim", "routine_claim", "routine_add", "connect_code"},
        "chat/claims.py": {"claim"},
        "web.py": {"shell"},
    }
    message_calls = {"claim_page", "claim_message_page", "message_page"}
    forwarded_exceptions = {
        "claim": [("chat/claims.py", {"check", "complete"}, "ClaimError")],
        "connect_code": [("connect.py", {"mint"}, "ConnectError"), ("projects.py", {"get"}, "ProjectError")],
        "routine_add": [("blocks/claude_routine/__init__.py", {"add", "_check_name", "_free_name"}, "BlockError")],
    }
    raw_errors = set()
    for name, functions in routes.items():
        path = ROOT / "src/switchboard" / name
        tree = ast.parse(path.read_text())
        dictionaries = {target.id: node.value for node in tree.body if isinstance(node, ast.Assign)
                        and isinstance(node.value, ast.Dict) for target in node.targets if isinstance(target, ast.Name)}
        for node in ast.walk(tree):
            if not isinstance(node, ast.AsyncFunctionDef) or node.name not in functions:
                continue
            for sub in ast.walk(node):
                if not (isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute)
                        and isinstance(sub.func.value, ast.Name) and sub.func.value.id == "pages"
                        and sub.func.attr in message_calls):
                    continue
                for arg in [*sub.args[:2], *(kw.value for kw in sub.keywords if kw.arg in {"title", "text", "scope"})]:
                    yield path, arg
                    if not isinstance(arg, ast.Call):
                        continue
                    if isinstance(arg.func, ast.Name) and arg.func.id == "str":
                        raw_errors.add(node.name)
                    elif (isinstance(arg.func, ast.Attribute) and arg.func.attr == "get"
                          and isinstance(arg.func.value, ast.Name) and arg.func.value.id in dictionaries):
                        yield from ((path, value) for value in dictionaries[arg.func.value.id].values)
    for handler in sorted(raw_errors):
        for name, functions, error in forwarded_exceptions.get(handler, []):
            path = ROOT / "src/switchboard" / name
            for node in ast.walk(ast.parse(path.read_text())):
                if not isinstance(node, ast.FunctionDef) or node.name not in functions:
                    continue
                for sub in ast.walk(node):
                    if (isinstance(sub, ast.Raise) and isinstance(sub.exc, ast.Call)
                            and isinstance(sub.exc.func, ast.Name) and sub.exc.func.id == error):
                        yield from ((path, arg) for arg in sub.exc.args[:1])


def entry(path, line, text, origin=""):
    return {"path": str(path.relative_to(ROOT)), "line": line, "text": text, "origin": origin}


results = []
for render, response in rendered_pages():
    parser = VisibleHTML()
    parser.feed(response.body.decode())
    results.extend(entry(Path(inspect.getfile(render)), inspect.getsourcelines(render)[1], text,
                         f"rendered {render.__name__} ← ") for text in parser.text)
for path, node in scoped_literals():
    results.extend(entry(path, node.lineno, text) for text in literal_parts(node))
parser = VisibleHTML()
parser.feed((ROOT / "web/index.html").read_text())
results.extend(entry(ROOT / "web/index.html", 1, text) for text in parser.text)
print(json.dumps(results))
