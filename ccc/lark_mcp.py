"""Lark document/spreadsheet MCP server for Claude agent integration.

Provides MCP tools that allow Claude to read and modify Lark documents
and spreadsheets during task execution.
"""

import json
import logging
from typing import Any

import lark_oapi as lark
from lark_oapi.api.docx.v1 import (
    Block,
    RawContentDocumentRequest,
    ListDocumentBlockRequest,
    PatchDocumentBlockRequest,
    UpdateBlockRequest,
    UpdateTextElementsRequest,
    TextElement,
    TextRun,
    CreateDocumentBlockChildrenRequest,
    CreateDocumentBlockChildrenRequestBody,
    BatchDeleteDocumentBlockChildrenRequest,
    BatchDeleteDocumentBlockChildrenRequestBody,
)
from lark_oapi.api.sheets.v3 import (
    GetSpreadsheetRequest,
    FindSpreadsheetSheetRequest,
    Find,
    FindCondition,
    ReplaceSpreadsheetSheetRequest,
    Replace,
)

from claude_agent_sdk import create_sdk_mcp_server, tool

logger = logging.getLogger(__name__)

# Module-level state set by create_lark_mcp_server()
_lark_client: lark.Client | None = None
_documents: list[dict] = []


def _text_result(text: str) -> dict[str, Any]:
    """Helper to create a standard MCP tool text result."""
    return {"content": [{"type": "text", "text": text}]}


def _error_result(msg: str) -> dict[str, Any]:
    """Helper to create an error result."""
    return {"content": [{"type": "text", "text": f"Error: {msg}"}]}


def _resolve_document(name_or_token: str, doc_type: str = None) -> dict | None:
    """Resolve a document by name or token.

    Args:
        name_or_token: Either a configured document name or a raw token.
        doc_type: Optional type filter ("doc" or "spreadsheet").

    Returns:
        Dict with "name", "type", "token" or None.
    """
    for doc in _documents:
        if doc["name"] == name_or_token or doc["token"] == name_or_token:
            if doc_type and doc["type"] != doc_type:
                continue
            return doc
    # If not found by name, treat as raw token
    if name_or_token and not any(d["name"] == name_or_token for d in _documents):
        return {"name": name_or_token, "type": doc_type or "doc", "token": name_or_token}
    return None


def _block_to_dict(block) -> dict:
    """Convert a Block object to a readable dict."""
    result = {
        "block_id": block.block_id,
        "block_type": block.block_type,
        "parent_id": block.parent_id,
    }
    if block.children:
        result["children"] = block.children

    # Extract text content for common block types
    text_content = None
    for attr in ("text", "heading1", "heading2", "heading3", "heading4",
                 "heading5", "heading6", "heading7", "heading8", "heading9",
                 "bullet", "ordered", "code", "quote", "todo"):
        obj = getattr(block, attr, None)
        if obj and hasattr(obj, "elements") and obj.elements:
            parts = []
            for elem in obj.elements:
                if elem.text_run and elem.text_run.content:
                    parts.append(elem.text_run.content)
            if parts:
                text_content = "".join(parts)
                break

    if text_content is not None:
        result["text_content"] = text_content

    return result


# ─── Document tools ──────────────────────────────────────────────────────────

@tool(
    "lark_list_documents",
    "List all configured Lark documents and spreadsheets. Returns name, type, and token for each.",
    {"type": "object", "properties": {}, "required": []},
)
async def list_documents(args: dict[str, Any]) -> dict[str, Any]:
    if not _documents:
        return _text_result("No Lark documents configured.")
    lines = []
    for doc in _documents:
        lines.append(f"- {doc['name']} (type: {doc['type']}, token: {doc['token']})")
    return _text_result("Configured Lark documents:\n" + "\n".join(lines))


@tool(
    "lark_doc_read",
    "Read a Lark document's full content as plain text/markdown. Input: document name or token.",
    {"document": str},
)
async def doc_read(args: dict[str, Any]) -> dict[str, Any]:
    doc = _resolve_document(args["document"], "doc")
    if not doc:
        return _error_result(f"Document '{args['document']}' not found.")

    request = RawContentDocumentRequest.builder().document_id(doc["token"]).lang(0).build()
    response = _lark_client.docx.v1.document.raw_content(request)

    if not response.success():
        return _error_result(f"Failed to read document: {response.code} - {response.msg}")

    return _text_result(response.data.content)


@tool(
    "lark_doc_list_blocks",
    "List all blocks in a Lark document with their IDs, types, and text content. Useful for finding block_ids before editing.",
    {"document": str},
)
async def doc_list_blocks(args: dict[str, Any]) -> dict[str, Any]:
    doc = _resolve_document(args["document"], "doc")
    if not doc:
        return _error_result(f"Document '{args['document']}' not found.")

    all_blocks = []
    page_token = None

    while True:
        builder = ListDocumentBlockRequest.builder().document_id(doc["token"]).page_size(500)
        if page_token:
            builder = builder.page_token(page_token)
        request = builder.build()
        response = _lark_client.docx.v1.document_block.list(request)

        if not response.success():
            return _error_result(f"Failed to list blocks: {response.code} - {response.msg}")

        if response.data.items:
            for block in response.data.items:
                all_blocks.append(_block_to_dict(block))

        if not response.data.has_more:
            break
        page_token = response.data.page_token

    return _text_result(json.dumps(all_blocks, indent=2, ensure_ascii=False))


@tool(
    "lark_doc_update_block",
    "Update the text content of a specific block in a Lark document. Use lark_doc_list_blocks first to find the block_id.",
    {
        "type": "object",
        "properties": {
            "document": {"type": "string", "description": "Document name or token"},
            "block_id": {"type": "string", "description": "The block ID to update"},
            "content": {"type": "string", "description": "New text content for the block"},
        },
        "required": ["document", "block_id", "content"],
    },
)
async def doc_update_block(args: dict[str, Any]) -> dict[str, Any]:
    doc = _resolve_document(args["document"], "doc")
    if not doc:
        return _error_result(f"Document '{args['document']}' not found.")

    elements = [
        TextElement.builder()
        .text_run(TextRun.builder().content(args["content"]).build())
        .build()
    ]

    update_req = UpdateBlockRequest.builder() \
        .update_text_elements(
            UpdateTextElementsRequest.builder().elements(elements).build()
        ).build()

    request = PatchDocumentBlockRequest.builder() \
        .document_id(doc["token"]) \
        .block_id(args["block_id"]) \
        .request_body(update_req) \
        .build()

    response = _lark_client.docx.v1.document_block.patch(request)

    if not response.success():
        return _error_result(f"Failed to update block: {response.code} - {response.msg}")

    return _text_result(f"Block {args['block_id']} updated successfully.")


@tool(
    "lark_doc_create_blocks",
    "Insert new text blocks into a Lark document under a parent block. Use lark_doc_list_blocks to find the parent block_id (the document's root block_id for top-level).",
    {
        "type": "object",
        "properties": {
            "document": {"type": "string", "description": "Document name or token"},
            "parent_block_id": {"type": "string", "description": "Parent block ID to insert children under (use document token for top-level)"},
            "texts": {
                "type": "array",
                "items": {"type": "string"},
                "description": "List of text strings, each becomes a new paragraph block",
            },
            "index": {"type": "integer", "description": "Position to insert at (-1 or omit for end)", "default": -1},
        },
        "required": ["document", "parent_block_id", "texts"],
    },
)
async def doc_create_blocks(args: dict[str, Any]) -> dict[str, Any]:
    doc = _resolve_document(args["document"], "doc")
    if not doc:
        return _error_result(f"Document '{args['document']}' not found.")

    children = []
    for text in args["texts"]:
        block = Block.builder() \
            .block_type(2) \
            .text(
                _build_text_body(text)
            ).build()
        children.append(block)

    body_builder = CreateDocumentBlockChildrenRequestBody.builder().children(children)
    idx = args.get("index", -1)
    if idx >= 0:
        body_builder = body_builder.index(idx)

    request = CreateDocumentBlockChildrenRequest.builder() \
        .document_id(doc["token"]) \
        .block_id(args["parent_block_id"]) \
        .request_body(body_builder.build()) \
        .build()

    response = _lark_client.docx.v1.document_block_children.create(request)

    if not response.success():
        return _error_result(f"Failed to create blocks: {response.code} - {response.msg}")

    return _text_result(f"Created {len(args['texts'])} block(s) successfully.")


@tool(
    "lark_doc_delete_blocks",
    "Delete blocks from a Lark document by specifying a parent block and the child index range to delete.",
    {
        "type": "object",
        "properties": {
            "document": {"type": "string", "description": "Document name or token"},
            "block_id": {"type": "string", "description": "Parent block ID whose children will be deleted"},
            "start_index": {"type": "integer", "description": "Start index of children to delete (inclusive)"},
            "end_index": {"type": "integer", "description": "End index of children to delete (exclusive)"},
        },
        "required": ["document", "block_id", "start_index", "end_index"],
    },
)
async def doc_delete_blocks(args: dict[str, Any]) -> dict[str, Any]:
    doc = _resolve_document(args["document"], "doc")
    if not doc:
        return _error_result(f"Document '{args['document']}' not found.")

    body = BatchDeleteDocumentBlockChildrenRequestBody.builder() \
        .start_index(args["start_index"]) \
        .end_index(args["end_index"]) \
        .build()

    request = BatchDeleteDocumentBlockChildrenRequest.builder() \
        .document_id(doc["token"]) \
        .block_id(args["block_id"]) \
        .request_body(body) \
        .build()

    response = _lark_client.docx.v1.document_block_children.batch_delete(request)

    if not response.success():
        return _error_result(f"Failed to delete blocks: {response.code} - {response.msg}")

    return _text_result(f"Deleted blocks from index {args['start_index']} to {args['end_index']}.")


# ─── Spreadsheet tools ───────────────────────────────────────────────────────

@tool(
    "lark_sheet_get_info",
    "Get spreadsheet metadata including title and list of sheets with their IDs.",
    {"spreadsheet": str},
)
async def sheet_get_info(args: dict[str, Any]) -> dict[str, Any]:
    doc = _resolve_document(args["spreadsheet"], "spreadsheet")
    if not doc:
        return _error_result(f"Spreadsheet '{args['spreadsheet']}' not found.")

    request = GetSpreadsheetRequest.builder().spreadsheet_token(doc["token"]).build()
    response = _lark_client.sheets.v3.spreadsheet.get(request)

    if not response.success():
        return _error_result(f"Failed to get spreadsheet info: {response.code} - {response.msg}")

    spreadsheet = response.data.spreadsheet
    info = {
        "title": spreadsheet.title,
        "spreadsheet_token": spreadsheet.spreadsheet_token,
    }
    if hasattr(spreadsheet, "sheets") and spreadsheet.sheets:
        info["sheets"] = []
        for sheet in spreadsheet.sheets:
            sheet_info = {
                "sheet_id": sheet.sheet_id,
                "title": sheet.title,
                "index": sheet.index,
            }
            if hasattr(sheet, "row_count"):
                sheet_info["row_count"] = sheet.row_count
            if hasattr(sheet, "column_count"):
                sheet_info["column_count"] = sheet.column_count
            info["sheets"].append(sheet_info)

    return _text_result(json.dumps(info, indent=2, ensure_ascii=False))


@tool(
    "lark_sheet_read",
    "Read data from a spreadsheet range. Returns cell values as a 2D array. Range format: 'SheetName!A1:C10' or 'sheet_id!A1:C10'.",
    {
        "type": "object",
        "properties": {
            "spreadsheet": {"type": "string", "description": "Spreadsheet name or token"},
            "range": {"type": "string", "description": "Range to read, e.g. 'Sheet1!A1:C10' or 'abc123!A1:Z100'"},
        },
        "required": ["spreadsheet", "range"],
    },
)
async def sheet_read(args: dict[str, Any]) -> dict[str, Any]:
    doc = _resolve_document(args["spreadsheet"], "spreadsheet")
    if not doc:
        return _error_result(f"Spreadsheet '{args['spreadsheet']}' not found.")

    # Use v2 API via raw request for reading cell values
    range_str = args["range"]
    raw_req = lark.RawRequest()
    raw_req.http_method = lark.HttpMethod.GET
    raw_req.uri = f"/open-apis/sheets/v2/spreadsheets/{doc['token']}/values/{range_str}"
    raw_req.headers = {"Content-Type": "application/json"}
    raw_req.token_types = {lark.AccessTokenType.TENANT}

    response = _lark_client.request(raw_req)

    if response.status_code != 200:
        return _error_result(f"Failed to read spreadsheet: HTTP {response.status_code}")

    try:
        data = json.loads(response.content)
    except (json.JSONDecodeError, TypeError):
        return _error_result("Failed to parse spreadsheet response.")

    if data.get("code", 0) != 0:
        return _error_result(f"API error: {data.get('code')} - {data.get('msg', 'unknown')}")

    values = data.get("data", {}).get("valueRange", {}).get("values", [])
    return _text_result(json.dumps(values, indent=2, ensure_ascii=False))


@tool(
    "lark_sheet_write",
    "Write values to a spreadsheet range. Range format: 'SheetName!A1:C3' or 'sheet_id!A1:C3'. Values is a 2D array.",
    {
        "type": "object",
        "properties": {
            "spreadsheet": {"type": "string", "description": "Spreadsheet name or token"},
            "range": {"type": "string", "description": "Range to write to, e.g. 'Sheet1!A1:C3'"},
            "values": {
                "type": "array",
                "items": {"type": "array", "items": {}},
                "description": "2D array of values to write, e.g. [[1, 'a'], [2, 'b']]",
            },
        },
        "required": ["spreadsheet", "range", "values"],
    },
)
async def sheet_write(args: dict[str, Any]) -> dict[str, Any]:
    doc = _resolve_document(args["spreadsheet"], "spreadsheet")
    if not doc:
        return _error_result(f"Spreadsheet '{args['spreadsheet']}' not found.")

    range_str = args["range"]
    raw_req = lark.RawRequest()
    raw_req.http_method = lark.HttpMethod.PUT
    raw_req.uri = f"/open-apis/sheets/v2/spreadsheets/{doc['token']}/values"
    raw_req.headers = {"Content-Type": "application/json"}
    raw_req.body = {
        "valueRange": {
            "range": range_str,
            "values": args["values"],
        }
    }
    raw_req.token_types = {lark.AccessTokenType.TENANT}

    response = _lark_client.request(raw_req)

    if response.status_code != 200:
        return _error_result(f"Failed to write spreadsheet: HTTP {response.status_code}")

    try:
        data = json.loads(response.content)
    except (json.JSONDecodeError, TypeError):
        return _error_result("Failed to parse spreadsheet response.")

    if data.get("code", 0) != 0:
        return _error_result(f"API error: {data.get('code')} - {data.get('msg', 'unknown')}")

    updated = data.get("data", {}).get("updatedCells", 0)
    return _text_result(f"Successfully wrote to range '{range_str}'. Updated {updated} cells.")


@tool(
    "lark_sheet_find",
    "Find cells matching a value in a spreadsheet sheet.",
    {
        "type": "object",
        "properties": {
            "spreadsheet": {"type": "string", "description": "Spreadsheet name or token"},
            "sheet_id": {"type": "string", "description": "Sheet ID to search in"},
            "find": {"type": "string", "description": "Value to search for"},
            "range": {"type": "string", "description": "Optional range to limit search, e.g. 'A1:Z100'"},
            "match_case": {"type": "boolean", "description": "Case-sensitive search", "default": False},
            "match_entire_cell": {"type": "boolean", "description": "Match entire cell content", "default": False},
            "search_by_regex": {"type": "boolean", "description": "Treat find as regex", "default": False},
        },
        "required": ["spreadsheet", "sheet_id", "find"],
    },
)
async def sheet_find(args: dict[str, Any]) -> dict[str, Any]:
    doc = _resolve_document(args["spreadsheet"], "spreadsheet")
    if not doc:
        return _error_result(f"Spreadsheet '{args['spreadsheet']}' not found.")

    condition_builder = FindCondition.builder() \
        .match_case(args.get("match_case", False)) \
        .match_entire_cell(args.get("match_entire_cell", False)) \
        .search_by_regex(args.get("search_by_regex", False))

    if args.get("range"):
        condition_builder = condition_builder.range(args["range"])

    find_body = Find.builder() \
        .find(args["find"]) \
        .find_condition(condition_builder.build()) \
        .build()

    request = FindSpreadsheetSheetRequest.builder() \
        .spreadsheet_token(doc["token"]) \
        .sheet_id(args["sheet_id"]) \
        .request_body(find_body) \
        .build()

    response = _lark_client.sheets.v3.spreadsheet_sheet.find(request)

    if not response.success():
        return _error_result(f"Failed to find in spreadsheet: {response.code} - {response.msg}")

    result = response.data
    if not result or not result.find_result:
        return _text_result("No matches found.")

    matched = result.find_result
    info = {
        "matched_cells": matched.matched_cells if hasattr(matched, "matched_cells") else None,
        "matched_formula_cells": matched.matched_formula_cells if hasattr(matched, "matched_formula_cells") else None,
        "rows_count": matched.rows_count if hasattr(matched, "rows_count") else None,
    }
    return _text_result(json.dumps(info, indent=2, ensure_ascii=False))


@tool(
    "lark_sheet_replace",
    "Find and replace values in a spreadsheet sheet.",
    {
        "type": "object",
        "properties": {
            "spreadsheet": {"type": "string", "description": "Spreadsheet name or token"},
            "sheet_id": {"type": "string", "description": "Sheet ID to search in"},
            "find": {"type": "string", "description": "Value to find"},
            "replacement": {"type": "string", "description": "Replacement value"},
            "range": {"type": "string", "description": "Optional range to limit search"},
            "match_case": {"type": "boolean", "description": "Case-sensitive search", "default": False},
            "match_entire_cell": {"type": "boolean", "description": "Match entire cell", "default": False},
            "search_by_regex": {"type": "boolean", "description": "Treat find as regex", "default": False},
        },
        "required": ["spreadsheet", "sheet_id", "find", "replacement"],
    },
)
async def sheet_replace(args: dict[str, Any]) -> dict[str, Any]:
    doc = _resolve_document(args["spreadsheet"], "spreadsheet")
    if not doc:
        return _error_result(f"Spreadsheet '{args['spreadsheet']}' not found.")

    condition_builder = FindCondition.builder() \
        .match_case(args.get("match_case", False)) \
        .match_entire_cell(args.get("match_entire_cell", False)) \
        .search_by_regex(args.get("search_by_regex", False))

    if args.get("range"):
        condition_builder = condition_builder.range(args["range"])

    replace_body = Replace.builder() \
        .find(args["find"]) \
        .replacement(args["replacement"]) \
        .find_condition(condition_builder.build()) \
        .build()

    request = ReplaceSpreadsheetSheetRequest.builder() \
        .spreadsheet_token(doc["token"]) \
        .sheet_id(args["sheet_id"]) \
        .request_body(replace_body) \
        .build()

    response = _lark_client.sheets.v3.spreadsheet_sheet.replace(request)

    if not response.success():
        return _error_result(f"Failed to replace in spreadsheet: {response.code} - {response.msg}")

    result = response.data
    if result and result.replace_result:
        matched = result.replace_result
        info = {
            "matched_cells": matched.matched_cells if hasattr(matched, "matched_cells") else None,
            "matched_formula_cells": matched.matched_formula_cells if hasattr(matched, "matched_formula_cells") else None,
            "rows_count": matched.rows_count if hasattr(matched, "rows_count") else None,
        }
        return _text_result(f"Replace completed:\n{json.dumps(info, indent=2, ensure_ascii=False)}")

    return _text_result("Replace completed.")


# ─── Text helpers ─────────────────────────────────────────────────────────────

def _build_text_body(content: str):
    """Build a Text object with a single TextRun element."""
    from lark_oapi.api.docx.v1 import Text
    return Text.builder().elements([
        TextElement.builder()
        .text_run(TextRun.builder().content(content).build())
        .build()
    ]).build()


# ─── Server factory ──────────────────────────────────────────────────────────

def create_lark_mcp_server(app_id: str, app_secret: str, documents: list[dict]):
    """Create and return the Lark documents MCP server config.

    Args:
        app_id: Lark app ID for authentication.
        app_secret: Lark app secret for authentication.
        documents: List of document configs from LARK_DOCUMENTS.

    Returns:
        MCP server config dict for ClaudeAgentOptions.mcp_servers.
    """
    global _lark_client, _documents

    _lark_client = lark.Client.builder() \
        .app_id(app_id) \
        .app_secret(app_secret) \
        .log_level(lark.LogLevel.WARNING) \
        .build()

    _documents = documents

    logger.info(f"Creating Lark MCP server with {len(documents)} document(s)")

    return create_sdk_mcp_server(
        name="lark-documents",
        tools=[
            list_documents,
            doc_read,
            doc_list_blocks,
            doc_update_block,
            doc_create_blocks,
            doc_delete_blocks,
            sheet_get_info,
            sheet_read,
            sheet_write,
            sheet_find,
            sheet_replace,
        ],
    )
