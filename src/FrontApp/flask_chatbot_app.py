# Copyright (c) Microsoft. All rights reserved.

import os
import asyncio
import base64
import threading
import json
from typing import Any, List, Dict, Optional, Awaitable
from datetime import datetime

from flask import Flask, render_template, request, jsonify
from agent_framework import AgentProtocol, AgentThread, HostedMCPTool, HostedCodeInterpreterTool, AgentRunResponse, ChatResponseUpdate, HostedFileContent
from agent_framework.azure import AzureAIAgentClient
from azure.ai.agents.models import (
    RunStepDeltaCodeInterpreterDetailItemObject,
    RunStepDeltaCodeInterpreterImageOutput,
    RunStepDeltaCodeInterpreterLogOutput,
    RunStepDeltaMcpToolCall,
    RunStepMcpToolCall,
)
from azure.identity.aio import AzureCliCredential
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "your-secret-key-here")

# Define allowed MCP tools for security and performance optimization
MCP_ALLOWED_TOOLS = [
    "get_databases",
    "get_database_schemas",
    "get_table_schemas",
    "query_data",
    "update_values",
    "create_table",
    "drop_table",
    "get_similarproducts"
]

# Global variables for agent and client
chat_client = None
agent = None
threads = {}  # Store threads per session
agent_loop = None
agent_loop_thread = None
agent_initialized = False


def _start_agent_loop() -> asyncio.AbstractEventLoop:
    """Ensure a dedicated background event loop is running for the agent."""
    global agent_loop, agent_loop_thread

    if agent_loop and agent_loop.is_running():
        return agent_loop

    agent_loop = asyncio.new_event_loop()
    loop_ready = threading.Event()

    def _run_loop():
        asyncio.set_event_loop(agent_loop)
        loop_ready.set()
        agent_loop.run_forever()

    agent_loop_thread = threading.Thread(target=_run_loop, daemon=True, name="AgentEventLoop")
    agent_loop_thread.start()
    loop_ready.wait()
    return agent_loop


def run_in_agent_loop(coro: Awaitable[Any], timeout: Optional[float] = None):
    """Execute an awaitable on the agent loop and return its result."""
    loop = _start_agent_loop()
    future = asyncio.run_coroutine_threadsafe(coro, loop)
    return future.result(timeout)


def _collect_tool_details(raw_obj: Any) -> List[Dict[str, Any]]:
    """Recursively collect detailed tool execution information."""
    collected: List[Dict[str, Any]] = []

    def _walk(obj: Any, depth: int = 0):
        if obj is None or depth > 10:
            return
            
        # Handle Azure SDK objects with _data attribute
        if hasattr(obj, '_data') and hasattr(obj._data, 'get'):
            _walk(obj._data, depth + 1)
            return
            
        if isinstance(obj, RunStepMcpToolCall):
            tool_info = {
                "name": getattr(obj, "name", "unknown"),
                "server": getattr(obj, "server_label", "unknown"),
                "arguments": getattr(obj, "arguments", "{}"),
                "output": getattr(obj, "output", "")
            }
            # Try to parse arguments as JSON for better display
            try:
                tool_info["arguments_parsed"] = json.loads(tool_info["arguments"])
            except:
                tool_info["arguments_parsed"] = tool_info["arguments"]
            
            collected.append(tool_info)
            return
            
        if isinstance(obj, dict):
            # Look for MCP tool call data in dict format
            # Only collect if it has both 'type'='mcp' AND an actual tool name (not server config)
            # Check for 'id' field to distinguish actual tool calls from server configs
            if obj.get("type") == "mcp" and "id" in obj and obj.get("name"):
                tool_info = {
                    "name": obj.get("name", "unknown"),
                    "server": obj.get("server_label", "unknown"),
                    "arguments": obj.get("arguments", "{}"),
                    "output": obj.get("output", ""),
                    "id": obj.get("id", "")  # Include ID for debugging
                }
                try:
                    tool_info["arguments_parsed"] = json.loads(tool_info["arguments"]) if isinstance(tool_info["arguments"], str) else tool_info["arguments"]
                except:
                    tool_info["arguments_parsed"] = tool_info["arguments"]
                
                print(f"DEBUG _collect_tool_details: Found tool '{tool_info['name']}' with id={tool_info['id']}")
                collected.append(tool_info)
                # Don't return - continue walking to find nested calls
                
            # Recurse into nested structures
            if "step_details" in obj:
                _walk(obj["step_details"], depth + 1)
            if "tool_calls" in obj:
                _walk(obj["tool_calls"], depth + 1)
            for key, value in obj.items():
                if key not in ["step_details", "tool_calls"]:
                    _walk(value, depth + 1)
            return
            
        if isinstance(obj, (list, tuple, set)):
            for item in obj:
                _walk(item, depth + 1)
            return

    _walk(raw_obj)
    return collected


def extract_code_interpreter_outputs(response: AgentRunResponse) -> Dict[str, Any]:
    """Extract code interpreter outputs including images and MCP tool usage."""
    outputs: Dict[str, Any] = {
        "images": [],
        "files": [],
        "tools": [],
        "tool_details": []  # Store detailed tool execution info
    }
    
    # DEBUG: Check response structure
    print(f"DEBUG: Response has raw_representation: {response.raw_representation is not None}")
    if hasattr(response, 'run_steps'):
        print(f"DEBUG: Response has run_steps attribute")
    if hasattr(response, 'completed_run'):
        print(f"DEBUG: Response has completed_run attribute")
    
    if response.raw_representation is None:
        return outputs
    
    print(f"DEBUG: Processing {len(response.raw_representation)} chunks from raw_representation")
    
    # Also check if there are other attributes on response that might have tool info
    print(f"DEBUG: Response type: {type(response).__name__}")
    print(f"DEBUG: Response attributes: {[attr for attr in dir(response) if not attr.startswith('_')]}")
    
    for idx, chunk in enumerate(response.raw_representation):
        chunk_type = type(chunk).__name__
        # Only log first 5 and last 5 chunks to reduce noise
        if idx < 5 or idx >= len(response.raw_representation) - 5:
            print(f"DEBUG chunk {idx}: type={chunk_type}")
            if hasattr(chunk, 'raw_representation'):
                print(f"  -> raw_representation type: {type(chunk.raw_representation).__name__}")
        
        if isinstance(chunk, ChatResponseUpdate):
            # Check for code interpreter outputs
            if isinstance(chunk.raw_representation, RunStepDeltaCodeInterpreterDetailItemObject):
                if chunk.raw_representation.outputs:
                    for output in chunk.raw_representation.outputs:
                        if isinstance(output, RunStepDeltaCodeInterpreterImageOutput):
                            if hasattr(output, 'image') and output.image:
                                if hasattr(output.image, 'file_id'):
                                    print(f"DEBUG: Found image file_id: {output.image.file_id}")
                                    outputs["images"].append(output.image.file_id)
                        elif isinstance(output, RunStepDeltaCodeInterpreterLogOutput):
                            # Check if log output contains file references
                            if hasattr(output, 'logs') and output.logs:
                                print(f"DEBUG: Code interpreter log: {output.logs}")
                        # Try to extract file_id from any output that has it
                        elif hasattr(output, 'file_id'):
                            print(f"DEBUG: Found file output with file_id: {output.file_id}")
                            outputs["files"].append(output.file_id)

            raw = getattr(chunk, "raw_representation", None)
            
            # Collect detailed tool execution information from raw_representation
            tool_details = _collect_tool_details(raw)
            if tool_details:
                print(f"DEBUG: Found {len(tool_details)} tool details in chunk {idx}")
            outputs["tool_details"].extend(tool_details)
        
        # Also check if the chunk itself (not just ChatResponseUpdate) has tool info
        # This handles cases where chunks are RunStep, ThreadRun, etc.
        tool_details_from_chunk = _collect_tool_details(chunk)
        if tool_details_from_chunk:
            print(f"DEBUG: Found {len(tool_details_from_chunk)} tool details directly from chunk {idx}")
        outputs["tool_details"].extend(tool_details_from_chunk)
    
    # Deduplicate images by file_id (keep only unique ones, preserving order)
    seen_images = set()
    unique_images = []
    for file_id in outputs["images"]:
        if file_id not in seen_images:
            seen_images.add(file_id)
            unique_images.append(file_id)
    
    # STRATEGY: When multiple images exist, keep only the LAST one (most recent)
    # This handles the case where the agent "updates" a visualization
    if len(unique_images) > 1:
        print(f"DEBUG: Multiple images detected ({len(unique_images)}), keeping only the most recent")
        print(f"  Discarding: {unique_images[:-1]}")
        print(f"  Keeping: {unique_images[-1]}")
        outputs["images"] = [unique_images[-1]]  # Keep only the last image
    else:
        outputs["images"] = unique_images
    
    print(f"DEBUG: Total image file_ids found: {len(seen_images)}, unique: {len(unique_images)}, returned: {len(outputs['images'])}")
    
    # Deduplicate tool details by (name, arguments) combination to handle multiple chunks
    seen = set()
    unique_details = []
    for detail in outputs["tool_details"]:
        key = (detail["name"], detail["arguments"])
        if key not in seen:
            seen.add(key)
            unique_details.append(detail)
    outputs["tool_details"] = unique_details
    
    # Derive tool names from tool details to ensure perfect 1:1 mapping
    outputs["tools"] = [f"{detail['server']}:{detail['name']}" for detail in unique_details]
    
    # DEBUG: Log the final mapping with VERIFICATION
    print(f"\n{'='*80}")
    print(f"TOOL MAPPING VERIFICATION ({len(outputs['tools'])} tools, {len(outputs['tool_details'])} details)")
    print(f"{'='*80}")
    if len(outputs['tools']) != len(outputs['tool_details']):
        print(f"⚠️  WARNING: MISMATCH! Tools={len(outputs['tools'])}, Details={len(outputs['tool_details'])}")
    for idx in range(max(len(outputs['tools']), len(outputs['tool_details']))):
        tool_name = outputs['tools'][idx] if idx < len(outputs['tools']) else "MISSING"
        detail = outputs['tool_details'][idx] if idx < len(outputs['tool_details']) else None
        if detail:
            detail_name = f"{detail['server']}:{detail['name']}"
            match = "✓" if tool_name == detail_name else "✗ MISMATCH"
            print(f"  [{idx}] {match}")
            print(f"       Tool:   {tool_name}")
            print(f"       Detail: {detail_name}")
            print(f"       Args:   {detail['arguments'][:80]}...")
        else:
            print(f"  [{idx}] ✗ Tool: {tool_name}, Detail: MISSING")
    print(f"{'='*80}\n")
    print(f"DEBUG: Returning {len(outputs['images'])} unique images (deduplicated from {len(seen_images)} total)")
    
    # WORKAROUND: Extract file references from the final text response
    # Files from CodeInterpreter are often only mentioned in text as sandbox:// links
    final_text = str(response)  # Get the final text response from AgentRunResponse
    print(f"DEBUG: Checking final text for sandbox file references...")
    
    import re
    # Pattern to match sandbox file paths: sandbox:/mnt/data/filename.ext
    sandbox_pattern = r'sandbox:/mnt/data/([^\)]+\.(pptx|xlsx|csv|pdf|docx|txt|json|png|jpg))'
    sandbox_matches = re.findall(sandbox_pattern, final_text, re.IGNORECASE)
    
    if sandbox_matches:
        print(f"DEBUG: Found {len(sandbox_matches)} sandbox file references in text")
        for match in sandbox_matches:
            filename = match[0]  # Full filename with extension
            file_type = match[1]  # Extension captured by group 2
            print(f"📁 Sandbox file found: {filename} (type: {file_type})")
            # Create file info with file_id=None (will be populated by thread message workaround)
            file_info = {
                "file_id": None,  # Sandbox files don't have file_ids in response
                "file_name": filename,
                "file_type": file_type,
                "sandbox_path": f"sandbox:/mnt/data/{filename}"
            }
            outputs["files"].append(file_info)
            print(f"DEBUG: Added sandbox file: {file_info}")
    
    # Deduplicate files by file_name to avoid duplicates
    seen_files = {}
    for file_info in outputs["files"]:
        if isinstance(file_info, dict):
            file_name = file_info.get("file_name")
            if file_name and file_name not in seen_files:
                seen_files[file_name] = file_info
        else:
            # Handle string file_id format
            if file_info not in seen_files:
                seen_files[file_info] = {"file_id": file_info, "file_name": None, "file_type": None}
    outputs["files"] = list(seen_files.values())
    print(f"DEBUG: Total unique files found: {len(outputs['files'])}")
    
    return outputs


async def handle_agent_query(query: str, thread: AgentThread) -> Dict[str, Any]:
    """Handle agent query with approval workflow and extract outputs."""
    global agent, chat_client
    
    # Collect all chunks for code interpreter analysis
    result = await AgentRunResponse.from_agent_response_generator(
        agent.run_stream(query, thread=thread, store=True)
    )
    
    # Handle any approval requests (auto-approve for this implementation)
    while len(result.user_input_requests) > 0:
        from agent_framework import ChatMessage
        new_input: List[Any] = []
        for user_input_needed in result.user_input_requests:
            # Auto-approve all function calls
            new_input.append(
                ChatMessage(
                    role="user",
                    contents=[user_input_needed.create_response(True)],
                )
            )
        result = await AgentRunResponse.from_agent_response_generator(
            agent.run_stream(new_input, thread=thread, store=True)
        )
    
    # Extract outputs
    outputs = extract_code_interpreter_outputs(result)
    
    # Fetch file_ids from thread messages after completion
    print(f"\n{'='*80}")
    print("🔧 Fetching file_ids from thread messages")
    print(f"{'='*80}")
    
    try:
        # Access the underlying thread_id using service_thread_id attribute
        if hasattr(thread, 'service_thread_id'):
            thread_id = thread.service_thread_id
            print(f"✅ Using thread.service_thread_id: {thread_id}")
        else:
            print(f"❌ Thread object has no service_thread_id attribute")
            thread_id = None
        
        # Get the project_client to access the agents API
        if thread_id and hasattr(chat_client, 'project_client') and chat_client.project_client:
            agents_client = chat_client.project_client.agents
            
            # List messages in the thread to find file_ids
            print(f"Fetching messages for thread: {thread_id}")
            # Use the correct Azure SDK API: agents.messages.list(thread_id)
            # This returns AsyncItemPaged, which needs to be iterated with async for
            messages_paged = agents_client.messages.list(thread_id=thread_id)
            
            # Extract file_ids from messages
            found_file_ids = []
            msg_idx = 0
            async for message in messages_paged:
                print(f"  Message {msg_idx}: role={message.role if hasattr(message, 'role') else 'unknown'}")
                
                # Check for file_ids in attachments
                if hasattr(message, 'attachments') and message.attachments:
                    print(f"    Found {len(message.attachments)} attachments")
                    for att_idx, attachment in enumerate(message.attachments):
                        if hasattr(attachment, 'file_id'):
                            file_id = attachment.file_id
                            found_file_ids.append(file_id)
                            print(f"    🔥 ATTACHMENT[{att_idx}] file_id: {file_id}")
                
                # Check for file_ids directly on message
                if hasattr(message, 'file_ids') and message.file_ids:
                    print(f"    Found {len(message.file_ids)} file_ids in message")
                    for file_id in message.file_ids:
                        if file_id not in found_file_ids:
                            found_file_ids.append(file_id)
                        print(f"    🔥 MESSAGE file_id: {file_id}")
                
                msg_idx += 1
            
            # Map found file_ids to sandbox files
            if found_file_ids:
                print(f"\n✅ Found {len(found_file_ids)} file_id(s) in thread messages")
                print(f"Files to map: {outputs['files']}")
                
                # Update outputs with actual file_ids
                for file_info in outputs['files']:
                    if isinstance(file_info, dict) and file_info.get('file_id') is None:
                        # This is a sandbox file without file_id - assign the first available
                        if len(found_file_ids) > 0:
                            file_info['file_id'] = found_file_ids.pop(0)
                            print(f"  ✅ Mapped sandbox file '{file_info['file_name']}' to file_id: {file_info['file_id']}")
            else:
                print("⚠️ No file_ids found in thread messages")
                
    except Exception as e:
        print(f"❌ Error fetching file_ids from thread: {e}")
        import traceback
        traceback.print_exc()
    
    print(f"{'='*80}\n")
    
    # NOTE: Do NOT deduplicate tools here - it's already done in extract_code_interpreter_outputs()
    # Deduplicating here without updating tool_details breaks the 1:1 mapping!
    
    # Clean up the response text: remove sandbox file links since we provide proper download buttons
    response_text = str(result)
    
    # Remove markdown links with sandbox:// paths
    import re
    # Pattern: [Link text](sandbox:/mnt/data/filename.ext)
    response_text = re.sub(r'\[Download[^\]]*\]\(sandbox:/mnt/data/[^\)]+\)', '', response_text)
    # Also remove any remaining sandbox:// references
    response_text = re.sub(r'sandbox:/mnt/data/[^\s\)]+', '', response_text)
    # Clean up extra whitespace and newlines
    response_text = re.sub(r'\n\s*\n\s*\n', '\n\n', response_text).strip()

    return {
        "response": response_text,
        "outputs": outputs
    }


async def download_file(file_id: str) -> tuple[bytes, str, str]:
    """Download a file from the agent and return content, file name, and file type.
    
    Returns:
        tuple: (file_content, file_name, file_type) or (None, None, None) on error
    """
    global chat_client
    try:
        # Access the agents client from project_client
        if hasattr(chat_client, 'project_client') and chat_client.project_client:
            project_client = chat_client.project_client
            # Use the agents API to download file content
            agents_client = project_client.agents
            
            # Try to get file metadata first
            file_name = None
            file_type = None
            try:
                file_obj = await agents_client.files.get(file_id)
                if hasattr(file_obj, 'filename'):
                    file_name = file_obj.filename
                    # Extract extension from filename
                    if '.' in file_name:
                        file_type = file_name.split('.')[-1]
                print(f"DEBUG: File metadata - name: {file_name}, type: {file_type}")
            except Exception as e:
                print(f"DEBUG: Could not get file metadata for {file_id}: {e}")
            
            # Download file content
            stream = await agents_client.files.get_content(file_id)
            
            # Collect chunks from the async generator
            chunks = []
            async for chunk in stream:
                if isinstance(chunk, (bytes, bytearray)):
                    chunks.append(chunk)
            
            file_content = b''.join(chunks)
            
            # If file_name not available from metadata, infer from content or use default
            if not file_name:
                file_name = f'agent_file_{file_id[:8]}'
            
            # If file_type not available, infer from content signature
            if not file_type and file_content:
                if file_content[:4] == b'PK\x03\x04':  # ZIP-based files
                    if file_content[30:50].find(b'ppt') != -1:
                        file_type = 'pptx'
                    elif file_content[30:50].find(b'word') != -1:
                        file_type = 'docx'
                    elif file_content[30:50].find(b'xl') != -1:
                        file_type = 'xlsx'
                    else:
                        file_type = 'zip'
                elif file_content[:4] == b'%PDF':
                    file_type = 'pdf'
                elif file_content[:4] == b'\x89PNG':
                    file_type = 'png'
                elif file_content[:2] == b'\xff\xd8':
                    file_type = 'jpg'
                elif file_content[:4] == b'GIF8':
                    file_type = 'gif'
                else:
                    # Try to detect CSV
                    try:
                        text_sample = file_content[:1000].decode('utf-8', errors='ignore')
                        if ',' in text_sample and '\n' in text_sample:
                            file_type = 'csv'
                        else:
                            file_type = 'bin'
                    except:
                        file_type = 'bin'
            
            # Add extension to file_name if not present
            if file_name and file_type and not file_name.endswith(f'.{file_type}'):
                file_name = f"{file_name}.{file_type}"
            
            return file_content, file_name, file_type
        else:
            print(f"Error: Unable to access project_client for file download")
            return None, None, None
    except Exception as e:
        print(f"Error downloading file {file_id}: {e}")
        import traceback
        traceback.print_exc()
        return None, None, None


async def initialize_agent():
    """Initialize the Azure AI Agent and client."""
    global chat_client, agent, agent_initialized

    if agent_initialized:
        return
    
    credential = AzureCliCredential()
    chat_client = AzureAIAgentClient(async_credential=credential)
    await chat_client.__aenter__()
    
    # Enable Azure AI observability
    await chat_client.setup_azure_ai_observability()
    
    agent = chat_client.create_agent(
        name="PGSQLAgent",

        instructions = (
            "You are an autonomous AI agent for PostgreSQL data analysis and recommendations. "
     
            "### Core Principles "
            "- Reason about what information you need before taking action "
            "- Read tool descriptions carefully - they tell you prerequisites and dependencies "
            "- When tools fail, analyze why and gather missing information "
            "- Never assume database structure - discover it using available tools "
            "- Present only final results to users, not your reasoning process "
            "- Be persistent: if a query fails, analyze the error, explore the schema, and retry with corrected logic "
            "- Treat empty results as opportunities to refine your approach, not as dead ends "
            
            "### File Generation Best Practices "
            "- When creating Office documents (PowerPoint, Word, Excel) with embedded media (images, charts): "
            "    * Save media files to disk first (as PNG, JPG, etc.) "
            "    * Then use appropriate libraries (python-pptx, python-docx, openpyxl) to embed the saved files "
            "    * Use file-based insertion methods (e.g., add_picture(filepath)) rather than in-memory or URL-based approaches "
            "    * This ensures all embedded content is properly included in the final document "
            
            "### Core Behavior Principles "
            "- Understand the user’s intent first. "
            "- Perform all reasoning internally; NEVER expose intermediate steps or tool logic in the final response. "
            "- Dynamically decide when and how to use tools based on the query and context. "
            "- Use tools only when they add value to the answer. "
            "- If critical information cannot be inferred after exploration, politely ask clarifying questions. "
            
            "### Tool Usage "
            "- Read each tool's description carefully to understand: "
            "    * What the tool does "
            "    * What information it requires (prerequisites) "
            "    * When to use it vs other tools "
            "    * What information it does NOT need "
            "    * Parameter value guidance (e.g., threshold values based on query type) "
            "- Follow the prerequisite workflow stated in tool descriptions EXACTLY "
            "- If a tool says 'Does NOT need X', do not call X before calling that tool "
            "- For tools with threshold/similarity parameters: ANALYZE the search term first, then choose appropriate values as instructed "
            "- When a tool fails or returns 'No output available': "
            "    * Re-read the tool's description "
            "    * Check if you followed the prerequisite workflow "
            "    * Check if you used appropriate parameter values (e.g., threshold too strict?) "
            "    * Identify what prerequisite information you're missing "
            "    * Use appropriate discovery/exploration tools to get that information "
            "    * Retry with complete information "
            "- Empty results from semantic search with DEFAULT parameters may mean wrong threshold - check tool description for guidance "
            
            "### Schema Discovery & Query Construction "
            "- When exploring unfamiliar data relationships: "
            "    1. Call get_database_schemas to see all schemas and their descriptive comments "
            "    2. Read the schema comments to identify which schemas are relevant to the user's query "
            "    3. Call get_table_schemas for EACH relevant schema to see tables and columns "
            "    4. Read column names and comments to understand data types and relationships "
            "    5. Build SQL queries using the EXACT table and column names discovered "
            "- NEVER assume column names or data types - always verify first "
            "- When joining tables across schemas, check BOTH schemas to identify the join keys "
            "- If a column name seems missing (e.g., looking for 'product_name' but only see 'name'), re-examine the schema output "
            
            "### Iterative Query Refinement (CRITICAL) "
            "- When a SQL query returns empty results or fails: "
            "    1. DO NOT give up immediately "
            "    2. Analyze the error message or empty result "
            "    3. Re-examine the schemas involved using get_table_schemas "
            "    4. Check if you used correct table names, column names, data types, or join conditions "
            "    5. Look for alternative columns that might contain the data (e.g., 'productnumber' vs 'product_number', 'productid' as integer vs string) "
            "    6. Verify foreign key relationships by examining column names and comments "
            "    7. Adjust the SQL query based on discoveries "
            "    8. Retry the query with corrections "
            "    9. Repeat this cycle up to 3-4 times until successful or until you've exhausted all reasonable approaches "
            "- Common query issues to check: "
            "    * Using text identifiers (e.g., product names/numbers) in tables that expect numeric IDs → need to join through a lookup table "
            "    * Incorrect schema qualification (e.g., missing schema prefix like 'sales.order' vs 'order') "
            "    * Case sensitivity in WHERE clauses "
            "    * Wrong join conditions (joining on mismatched data types) "
            "    * Missing intermediate tables in multi-table relationships "
            "- After 3-4 failed attempts, explain to the user what you tried and what schema limitations you discovered "
            
            "### Error Recovery "
            "- Query failures are learning opportunities - each failure reveals schema information "
            "- Use schema discovery tools iteratively to refine your understanding "
            "- Track what you've tried and avoid repeating the same mistake "
            "- If multiple retry attempts fail, provide a detailed explanation of: "
            "    * What queries you attempted "
            "    * What schema exploration you performed "
            "    * What limitations or missing data you discovered "
            "    * What alternative approaches the user might consider "
            
            "### Response Formatting "
            "- Present ONLY the final answer, not your internal reasoning or retry attempts. "
            "- Use headings and subheadings for clarity. "
            "- Use tables for query results with column headers, right-aligned numbers, thousand separators, and 2 decimal precision unless otherwise requested. "
            "- Highlight key metrics in **bold**. "
            "- Provide a short summary before tables or charts. "
            "- For large outputs, summarize insights and show aggregated metrics. "
            "- For combined outputs (text + table/chart), keep it concise and professional. "
            "- If you exhausted all retry attempts, present your findings clearly and suggest next steps. "
            
            "### Decision Principle "
            "- Act like an expert analyst: reason internally, plan, and execute. "
            "- Do not hard-code steps; dynamically decide which tools to use based on the query. "
            "- Be persistent and adaptive - the database schema is your guide, not assumptions. "
            "- Always aim for clarity, accuracy, and helpfulness in the final response."
        ),

        tools=[
            HostedMCPTool(
                name=os.getenv("MCP_SERVER_LABEL"),
                url=os.getenv("MCP_SERVER_URL"),
                approval_mode="never_require",
                allowed_tools=MCP_ALLOWED_TOOLS
            ),
            HostedCodeInterpreterTool(),
        ],
    )

    agent_initialized = True


@app.route('/')
def index():
    """Render the main chat interface."""
    return render_template('chat.html')


@app.route('/api/chat', methods=['POST'])
def chat():
    """Handle chat messages."""
    data = request.json
    user_message = data.get('message', '')
    session_id = data.get('session_id', 'default')
    
    if not user_message:
        return jsonify({"error": "No message provided"}), 400
    
    try:
        if not agent_initialized or agent is None:
            run_in_agent_loop(initialize_agent())

        # Get or create thread for this session
        if session_id not in threads:
            threads[session_id] = agent.get_new_thread()
        
        thread = threads[session_id]
        
        # Process the query on the dedicated agent loop
        result = run_in_agent_loop(handle_agent_query(user_message, thread))
        
        # Process images if any
        images_base64 = []
        if result["outputs"]["images"]:
            print(f"DEBUG: Found {len(result['outputs']['images'])} images to process")
            for file_id in result["outputs"]["images"]:
                print(f"DEBUG: Downloading image file_id: {file_id}")
                file_content, file_name, file_type = run_in_agent_loop(download_file(file_id))
                if file_content:
                    # Convert to base64 for embedding in HTML
                    encoded = base64.b64encode(file_content).decode('utf-8')
                    print(f"DEBUG: Successfully encoded image, length: {len(encoded)}")
                    images_base64.append({
                        "file_id": file_id,
                        "data": f"data:image/png;base64,{encoded}"
                    })
                else:
                    print(f"DEBUG: Failed to download file_id: {file_id}")
        
        print(f"DEBUG: Returning {len(images_base64)} images in response")
        
        # Prepare file download information with enhanced metadata
        files_info = []
        if result["outputs"].get("files"):
            print(f"\n📦 Preparing {len(result['outputs']['files'])} files for download:")
            for idx, file_entry in enumerate(result["outputs"]["files"]):
                # Handle both dict and string file_id formats
                if isinstance(file_entry, dict):
                    file_id = file_entry.get("file_id")
                    file_name = file_entry.get("file_name")
                    file_type = file_entry.get("file_type")
                    print(f"  [{idx}] Dict format: id={file_id}, name={file_name}, type={file_type}")
                else:
                    file_id = file_entry
                    file_name = None
                    file_type = None
                    print(f"  [{idx}] String format: id={file_id}")
                
                # If file name/type not available, try to fetch it
                if not file_name or not file_type:
                    try:
                        _, fetched_name, fetched_type = run_in_agent_loop(download_file(file_id))
                        if not file_name and fetched_name:
                            file_name = fetched_name
                        if not file_type and fetched_type:
                            file_type = fetched_type
                    except Exception as e:
                        print(f"DEBUG: Could not fetch file metadata for {file_id}: {e}")
                
                # Default file name if still not available
                if not file_name:
                    file_name = f"Generated File ({file_id[:8] if file_id else 'unknown'})"
                    if file_type:
                        file_name += f".{file_type}"
                
                files_info.append({
                    "file_id": file_id,
                    "file_name": file_name,
                    "file_type": file_type,
                    "download_url": f"/api/download-file/{file_id}"
                })
        
        # VERIFICATION: Ensure tools and tool_details are perfectly aligned
        tools_list = result["outputs"].get("tools", [])
        details_list = result["outputs"].get("tool_details", [])
        print(f"\n{'='*80}")
        print(f"API RESPONSE VERIFICATION")
        print(f"{'='*80}")
        print(f"Sending {len(tools_list)} tools and {len(details_list)} tool_details to frontend")
        if len(tools_list) != len(details_list):
            print(f"⚠️  ERROR: Array length mismatch! This will cause incorrect tool mapping in UI!")
        for idx in range(max(len(tools_list), len(details_list))):
            tool = tools_list[idx] if idx < len(tools_list) else "MISSING"
            detail = details_list[idx] if idx < len(details_list) else None
            if detail:
                detail_name = f"{detail['server']}:{detail['name']}"
                match = "✓" if tool == detail_name else "✗ MISMATCH"
                print(f"  [{idx}] {match} Tool='{tool}', Detail='{detail_name}'")
            else:
                print(f"  [{idx}] ✗ Tool='{tool}', Detail=MISSING")
        print(f"{'='*80}\n")
        
        return jsonify({
            "response": result["response"],
            "images": images_base64,
            "files": files_info,
            "tools": tools_list,
            "tool_details": details_list,
            "timestamp": datetime.now().isoformat()
        })
    
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/download-file/<file_id>', methods=['GET'])
def download_file_endpoint(file_id):
    """Download a file generated by the agent."""
    try:
        file_content, file_name, file_type = run_in_agent_loop(download_file(file_id))
        if file_content:
            # Determine MIME type from file_type
            mime_type_map = {
                'pptx': 'application/vnd.openxmlformats-officedocument.presentationml.presentation',
                'docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
                'xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                'pdf': 'application/pdf',
                'png': 'image/png',
                'jpg': 'image/jpeg',
                'jpeg': 'image/jpeg',
                'gif': 'image/gif',
                'csv': 'text/csv',
                'zip': 'application/zip',
                'bin': 'application/octet-stream'
            }
            
            mimetype = mime_type_map.get(file_type, 'application/octet-stream')
            
            # Use the inferred file name or default
            download_name = file_name if file_name else f'agent_file_{file_id[:8]}.{file_type or "bin"}'
            
            from flask import send_file
            import io
            return send_file(
                io.BytesIO(file_content),
                mimetype=mimetype,
                as_attachment=True,
                download_name=download_name
            )
        else:
            return jsonify({"error": "File not found"}), 404
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/new-session', methods=['POST'])
def new_session():
    """Create a new chat session."""
    import uuid
    if not agent_initialized or agent is None:
        run_in_agent_loop(initialize_agent())
    session_id = str(uuid.uuid4())
    threads[session_id] = agent.get_new_thread()
    return jsonify({"session_id": session_id})


@app.route('/api/clear-session', methods=['POST'])
def clear_session():
    """Clear a chat session."""
    data = request.json
    session_id = data.get('session_id', 'default')
    
    if not agent_initialized or agent is None:
        run_in_agent_loop(initialize_agent())

    if session_id in threads:
        del threads[session_id]
        threads[session_id] = agent.get_new_thread()
    
    return jsonify({"status": "success"})


if __name__ == '__main__':
    # Initialize agent before starting the app
    run_in_agent_loop(initialize_agent())

    # Start Flask app
    app.run(debug=True, host='0.0.0.0', port=5002, use_reloader=False)
