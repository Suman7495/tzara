"""A Notion stand-in for the benchmark: an MCP server over stdio with Notion's tool names.

Pages live in a JSON file (``BENCH_NOTION_FILE``) that the runner writes before each task and reads
afterwards, so a task can check what the agent did to them. A page in ``trashed`` refuses
changes with
the message Notion gives.
"""

import json
import os
import re
import sys
import uuid
from pathlib import Path
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

server = MCPServer("notion")
STORE = Path(os.environ.get("BENCH_NOTION_FILE", "notion.json"))


class Pages:
    """The fake workspace."""

    @staticmethod
    def load() -> dict[str, Any]:
        return json.loads(STORE.read_text()) if STORE.exists() else {"pages": {}, "trashed": []}

    @staticmethod
    def save(data: dict[str, Any]) -> None:
        STORE.write_text(json.dumps(data, indent=1))

    @classmethod
    def find(cls, data: dict[str, Any], ident: str) -> str | None:
        """The id of the page an id, address or title names."""
        ident = ident.strip()
        tail = re.split(r"[-/]", ident.split("?")[0].rstrip("/"))[-1]
        for page_id, page in data["pages"].items():
            if (
                ident == page_id
                or tail == page_id
                or page_id in ident
                or page["title"].lower() == ident.lower()
            ):
                return page_id
        return None

    @staticmethod
    def text(value: Any) -> str:
        """A title or content as text: a model sometimes sends a dictionary or a list."""
        if isinstance(value, dict):
            value = next(iter(value.values()), "")
        return value if isinstance(value, str) else json.dumps(value)

    @staticmethod
    def url(page_id: str, title: str) -> str:
        slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
        return f"https://www.notion.so/{slug}-{page_id}"


@server.tool(name="notion-search", annotations=ToolAnnotations(read_only_hint=True))
def notion_search(query: str, query_type: str = "internal") -> str:
    """Search Notion pages and databases by title and content."""
    data = Pages.load()
    words = re.findall(r"\w{3,}", query.lower())
    found = []
    for page_id, page in data["pages"].items():
        text = (page["title"] + " " + page["content"]).lower()
        score = sum(w in text for w in words)
        if score:
            found.append((score, page_id, page["title"]))
    if not found:
        return "No results found."
    found.sort(reverse=True)
    return "Results:\n" + "\n".join(
        f"- {title} (id: {page_id}, url: {Pages.url(page_id, title)})"
        for _, page_id, title in found[:8]
    )


@server.tool(name="notion-fetch", annotations=ToolAnnotations(read_only_hint=True))
def notion_fetch(id: str) -> str:
    """Retrieve a Notion page by its url or id."""
    data = Pages.load()
    page_id = Pages.find(data, id)
    if page_id is None:
        raise ValueError(f"Could not find a page with id {id!r}.")
    page = data["pages"][page_id]
    title = page["title"]
    return (
        f'<page url="{Pages.url(page_id, title)}">\n<title>{title}</title>\n'
        f"<content>\n{page['content']}\n</content>\n</page>"
    )


@server.tool(name="notion-create-pages")
def notion_create_pages(pages: list[dict[str, Any]], parent: dict[str, Any] | None = None) -> str:
    """Create Notion pages. Each page has properties (title) and content (Markdown)."""
    data = Pages.load()
    made = []
    for spec in pages:
        title = Pages.text(
            (spec.get("properties") or {}).get("title") or spec.get("title") or "Untitled"
        )
        page_id = "pg_" + uuid.uuid4().hex[:8]
        data["pages"][page_id] = {"title": title, "content": Pages.text(spec.get("content", ""))}
        made.append(f"{title} (id: {page_id})")
    Pages.save(data)
    return "Created: " + ", ".join(made)


@server.tool(name="notion-update-page")
def notion_update_page(
    page_id: str,
    command: str,
    content: str | None = None,
    new_str: str | None = None,
    content_updates: list[dict[str, str]] | None = None,
    properties: dict[str, Any] | None = None,
    position: dict[str, Any] | None = None,
    allow_deleting_content: bool = False,
    is_skill: bool | None = None,
) -> str:
    """Update a Notion page's properties or content."""
    data = Pages.load()
    found = Pages.find(data, page_id)
    if found is None:
        raise ValueError(f"Could not find a page with id {page_id!r}.")
    page = data["pages"][found]
    if page["title"] in data["trashed"]:
        raise ValueError("This page is in the trash. It must be restored before it can be updated.")
    if command == "insert_content":
        if not content:
            raise ValueError("insert_content needs content.")
        anchor = (position or {}).get("anchor")
        if (position or {}).get("type") == "after_content" and anchor and anchor in page["content"]:
            page["content"] = page["content"].replace(anchor, anchor + "\n" + content, 1)
        elif (position or {}).get("type") == "start":
            page["content"] = content + "\n" + page["content"]
        else:
            page["content"] = page["content"].rstrip() + "\n\n" + Pages.text(content)
    elif command == "replace_content":
        if new_str is None:
            raise ValueError("replace_content needs new_str.")
        page["content"] = new_str
    elif command == "update_content":
        for change in content_updates or []:
            if change.get("old_str") not in page["content"]:
                raise ValueError(f"old_str not found: {change.get('old_str')!r}")
            page["content"] = page["content"].replace(
                change["old_str"], change.get("new_str", ""), 1
            )
    elif command == "update_properties":
        if properties and "title" in properties:
            page["title"] = Pages.text(properties["title"])
    else:
        raise ValueError(f"Unknown command {command!r}.")
    Pages.save(data)
    return "Updated the page."


if __name__ == "__main__":
    if "-h" in sys.argv:
        print(__doc__)
    else:
        server.run("stdio")
