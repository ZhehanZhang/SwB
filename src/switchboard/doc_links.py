import re
from ipaddress import IPv6Address
from urllib.parse import quote, urlsplit

_DOCS = "https://swb.one/docs/"
_HOST_LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", re.IGNORECASE | re.ASCII)
_URL = re.compile(
    r"""(?<![\w./@%-])[a-z][a-z0-9+.-]*://(?:\[[0-9a-f:.]+\])?(?:[^\s<>()\[\]"'`“”‘’]|\([^\s<>()]*\))+""",
    re.IGNORECASE,
)


def doc_link(path: str, public_url: str | None) -> str:
    link = _DOCS + path
    if not public_url or re.search(r"[\s\x00-\x1f\x7f-\x9f?#%\\]", public_url):
        return link
    try:
        url = urlsplit(public_url)
        host = url.hostname
        if not host or url.username is not None or url.path not in ("", "/"):
            return link
        if url.scheme != "https" and not (url.scheme == "http" and host in ("localhost", "127.0.0.1", "::1")):
            return link
        if not re.fullmatch(r"(?:\[[^\]]+\]|[^:\[\]]+)(?::[0-9]+)?", url.netloc) or url.port == 0:
            return link
        if url.netloc.startswith("["):
            IPv6Address(host)
        else:
            domain = host.encode("idna").decode("ascii").removesuffix(".")
            if len(domain) > 253 or not all(_HOST_LABEL.fullmatch(label) for label in domain.split(".")):
                return link
    except ValueError:
        return link
    origin = f"{url.scheme}://{url.netloc}"
    return f'{link}#swb={quote(origin, safe="")}'


def rewrite_doc_links(text: str, public_url: str | None) -> str:
    suffix = doc_link("", public_url).removeprefix(_DOCS)
    if not suffix:
        return text

    def rewrite(match: re.Match[str]) -> str:
        url = match[0]
        if not url.startswith(_DOCS) or "#" in url:
            return url
        link = url.rstrip(".,;:!?…。，、；：！？）】」』")
        return link + suffix + url[len(link):]

    return _URL.sub(rewrite, text)
