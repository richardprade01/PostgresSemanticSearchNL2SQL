"""
Copyright (c) Microsoft Corporation.
Licensed under the MIT License.
"""

"""
HTTP SSE MCP server for Azure Database for PostgreSQL - Flexible Server.

This server exposes the following capabilities via HTTP Server-Sent Events:

Tools:
- create_table: Creates a table in a database.
- drop_table: Drops a table in a database.
- get_databases: Gets the list of all the databases in a server instance.
- get_schemas: Gets schemas of all the tables.
- get_server_config: Gets the configuration of a server instance. [Available with Microsoft EntraID]
- get_server_parameter: Gets the value of a server parameter. [Available with Microsoft EntraID]
- query_data: Runs read queries on a database.
- update_values: Updates or inserts values into a table.
- get_similar_products: Gets similar products based on a string request.

Resources:
- databases: Gets the list of all databases in a server instance.

To run the HTTP SSE server using PowerShell, expose the following variables:

```
$env:PGHOST="<Fully qualified name of your Azure Database for PostgreSQL instance>"
$env:PGUSER="<Your Azure Database for PostgreSQL username>"
$env:PGPASSWORD="<Your password>"
$env:MCP_HOST="localhost"
$env:MCP_PORT="8000"
```

Run the HTTP SSE MCP Server using the following command:

```
python azure_postgresql_mcp_sse.py
```

The server will be available at: http://localhost:8000

For detailed usage instructions, please refer to the README.md file.

"""

import json
import logging
import os
import sys
import urllib.parse
from typing import Optional

import psycopg
import uvicorn
from azure.identity import DefaultAzureCredential
from azure.mgmt.postgresqlflexibleservers import PostgreSQLManagementClient
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from mcp.server.fastmcp.resources import FunctionResource
from mcp.server import Server
from mcp.types import Resource, Tool
import asyncio
from dotenv import load_dotenv
import os
import asyncio
import logging
import httpx
import requests
from typing import Any
from fastmcp import FastMCP
from dotenv import load_dotenv

# Load environment variables from .env file if it exists
load_dotenv()
logger = logging.getLogger("azure")
logger.setLevel(logging.ERROR)

# Create FastMCP instance
MCP_NAME = "Azure PostgreSQL MCP SSE Server"
mcp = FastMCP(name=MCP_NAME)
# Global connection configuration
_aad_in_use = os.environ.get("AZURE_USE_AAD")
_dbhost = os.environ.get("PGHOST")
_dbuser = os.environ.get("PGUSER")
_password = os.environ.get("PGPASSWORD")
_credential = None
_postgresql_client = None
_subscription_id = None
_resource_group_name = None
_server_name = None

def get_databases_internal() -> str:
    """Internal function which gets the list of all databases in a server instance."""
    try:
        with psycopg.connect(
            f"host={_dbhost} user={_dbuser} dbname='postgres' password={_password}"
        ) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT datname FROM pg_database WHERE datistemplate = false;"
                )
                colnames = [desc[0] for desc in cur.description]
                dbs = cur.fetchall()
                return json.dumps(
                    {
                        "columns": str(colnames),
                        "rows": "".join(str(row) for row in dbs),
                    }
                )
    except Exception as e:
        logger.error(f"Error: {str(e)}")
        return ""


def get_databases_resource():
    """Gets list of databases as a resource"""
    return get_databases_internal()

@mcp.tool(name="get_databases",
    description="Gets the list of all the databases in a server instance.")
def get_databases():
    """Gets the list of all the databases in a server instance."""
    return get_databases_internal()


@mcp.tool(name="get_table_schemas",
    description="Gets tables and columns for a given schema in a database. PREREQUISITE: Use get_database_schemas first to get available schema names (e.g., 'production', 'sales', 'person'). Call this tool MULTIPLE times if your query involves multiple schemas.")
def get_table_schemas(database: str, schema: str) -> str:
    """Gets tables and columns for a given schema in a database."""
    try:
        with psycopg.connect(
            f"host={_dbhost} user={_dbuser} dbname='{database}' password={_password}"
        ) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT table_name, column_name, data_type FROM information_schema.columns "
                    "WHERE table_schema = %s ORDER BY table_name, ordinal_position;",
                    (schema,)
                )
                colnames = [desc[0] for desc in cur.description]
                tables = cur.fetchall()
                return json.dumps(
                    {
                        "columns": str(colnames),
                        "rows": "".join(str(row) for row in tables),
                    }
                )
    except Exception as e:
        logger.error(f"Error: {str(e)}")
        return ""

@mcp.tool(name="query_data",
    description="Runs read-only SQL queries. PREREQUISITE WORKFLOW: 1) Call get_database_schemas to see available schemas. 2) Call get_table_schemas for EACH schema involved in your query. 3) Verify exact column names exist in the schema you checked. 4) For joins across schemas (e.g., sales.salesperson + person.person), you MUST call get_table_schemas on BOTH 'sales' AND 'person' schemas.")
def query_data(dbname: str, s: str) -> str:
    """Runs read queries on a database.
    
    CRITICAL: Never assume column names. Always call get_table_schemas first.
    For multi-table/multi-schema queries, check ALL schemas involved.
    """
    try:
        with psycopg.connect(
            f"host={_dbhost} user={_dbuser} dbname='{dbname}' password={_password}"
        ) as conn:
            with conn.cursor() as cur:
                cur.execute(s)
                rows = cur.fetchall()
                colnames = [desc[0] for desc in cur.description]
                return json.dumps(
                    {
                        "columns": str(colnames),
                        "rows": ",".join(str(row) for row in rows),
                    }
                )
    except Exception as e:
        logger.error(f"Error: {str(e)}")
        return ""


@mcp.tool(name="get_similarproducts",
    description="Finds similar products using AI-powered semantic search with vector embeddings. BEST method for product recommendations. PREREQUISITE: Use get_databases first. IMPORTANT: Does NOT need schema exploration (get_database_schemas or get_table_schemas) - works directly with embedded product data. CRITICAL THRESHOLD GUIDANCE: Analyze search term complexity FIRST, then choose appropriate min_similarity: (1) Generic/broad terms (multi-activity, sports, outdoor, fitness) → ALWAYS use min_similarity=0.50 (2) Specific products (road bike, water bottle) → use min_similarity=0.75. DEFAULT 0.75 WILL FAIL for generic terms. Example: 'multi-activities' REQUIRES min_similarity=0.50 to get results.")
def get_similar_products(
    database: str, 
    search_text: str,
    min_stock: int = 0,
    top_n: int = 20,
    min_similarity: float = 0.75
) -> str:
    """Gets similar products using AI semantic search with optional filters.
    
    Args:
        database: The database name (use get_databases first)
        search_text: Natural language description (e.g., 'bike accessories', 'cycling gear', 'outdoor equipment')
        min_stock: Minimum safety stock level (default: 0 = no filter). Use 500+ for high-stock items.
        top_n: Maximum number of results to return (default: 20)
        min_similarity: Similarity threshold 0.0-1.0 (default: 0.75 = strict matching)
            IMPORTANT: 0.75 is strict - may return 0 results for generic terms
            - Use 0.50-0.60 for broad/generic searches ('multi-purpose', 'outdoor activities')
            - Use 0.75-0.90 for specific product searches ('road bike', 'water bottle')
            If search returns empty, retry with lower threshold (e.g., 0.50)
    """
    try:
        with psycopg.connect(
            f"host={_dbhost} user={_dbuser} dbname='{database}' password={_password}"
        ) as conn:
            with conn.cursor() as cur:
                # Explicit type casts to match function signature: varchar, smallint, int, decimal
                cur.execute(
                    "SELECT * FROM search_products(%s::varchar, %s::smallint, %s::int, %s::decimal);", 
                    (search_text, min_stock, top_n, min_similarity)
                )
                colnames = [desc[0] for desc in cur.description]
                tables = cur.fetchall()
                return json.dumps(
                    {
                        "columns": str(colnames),
                        "rows": "".join(str(row) for row in tables),
                    }
                )
    except Exception as e:
        logger.error(f"Error in get_similarproducts for database '{database}': {str(e)}")
        return json.dumps({"error": f"Failed to search products in database '{database}': {str(e)}"})

def exec_and_commit(dbname: str, s: str) -> None:
    """Internal function to execute and commit transaction."""
    try:
        with psycopg.connect(
            f"host={_dbhost} user={_dbuser} dbname='{dbname}' password={_password}"
        ) as conn:
            with conn.cursor() as cur:
                cur.execute(s)
                conn.commit()
    except Exception as e:
        logger.error(f"Error: {str(e)}")

@mcp.tool(name="update_values",
    description="Updates or inserts values into a table. PREREQUISITE: You must know the exact table and column names. Use get_table_schemas first to verify the table structure.")
def update_values(dbname: str, s: str) -> str:
    """Updates or inserts values into a table.
    
    IMPORTANT: Requires exact table/column names. Use get_table_schemas first.
    """
    exec_and_commit(dbname, s)
    return "Values updated successfully"

@mcp.tool(name="create_table",
    description="Creates a table in a database.")
def create_table(dbname: str, s: str) -> str:
    """Creates a table in a database."""
    exec_and_commit(dbname, s)
    return "Table created successfully"

@mcp.tool(name="drop_table",
    description="Drops a table in a database.")
def drop_table(dbname: str, s: str) -> str:
    """Drops a table in a database."""
    exec_and_commit(dbname, s)
    return "Table dropped successfully"

@mcp.tool(name="get_server_config",
    description="Gets the configuration of a server instance. [Available with Microsoft EntraID]")
def get_server_config() -> str:
    """Gets the configuration of a server instance. [Available with Microsoft EntraID]"""
    if _aad_in_use == "True":
        try:
            server = _postgresql_client.servers.get(
                _resource_group_name, _server_name
            )
            return json.dumps(
                {
                    "server": {
                        "name": server.name,
                        "location": server.location,
                        "version": server.version,
                        "sku": server.sku.name,
                        "storage_profile": {
                            "storage_size_gb": server.storage.storage_size_gb,
                            "backup_retention_days": server.backup.backup_retention_days,
                            "geo_redundant_backup": server.backup.geo_redundant_backup,
                        },
                    },
                }
            )
        except Exception as e:
            logger.error(f"Failed to get PostgreSQL server configuration: {e}")
            raise e
    else:
        raise NotImplementedError(
            "This tool is available only with Microsoft EntraID"
        )

@mcp.tool(name="get_server_parameter",
    description="Gets the value of a server parameter. [Available with Microsoft EntraID]")
def get_server_parameter(parameter_name: str) -> str:
    """Gets the value of a server parameter. [Available with Microsoft EntraID]"""
    if _aad_in_use == "True":
        try:
            configuration = _postgresql_client.configurations.get(
                _resource_group_name, _server_name, parameter_name
            )
            return json.dumps(
                {"param": configuration.name, "value": configuration.value}
            )
        except Exception as e:
            logger.error(
                f"Failed to get PostgreSQL server parameter '{parameter_name}': {e}"
            )
            raise e
    else:
        raise NotImplementedError(
            "This tool is available only with Microsoft EntraID"
        )

@mcp.tool(name="get_database_schemas",
    description="Retrieves schemas of a specific database.")
def get_database_schemas(database: str) -> str:
    """Retrieves schemas of a specific database."""
    try:
        with psycopg.connect(
            f"host={_dbhost} user={_dbuser} dbname='{database}' password={_password}"
        ) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT n.nspname AS schema_name, obj_description(n.oid, 'pg_namespace') AS schema_comment "
                    "FROM pg_catalog.pg_namespace n;"
                )
                schemas = cur.fetchall()
                return json.dumps(
                    {
                        "schemas": [schema[0] for schema in schemas],
                        "schema_details": [
                            {
                                "name": schema[0],
                                "comment": schema[1],
                            }
                            for schema in schemas
                        ],
                    }
                )
    except Exception as e:
        logger.error(f"Error: {str(e)}")
        return ""

if __name__ == "__main__":
    logger.info(f"Starting MCP server")
    host = os.environ.get("MCP_HOST", "127.0.0.1")
    port = int(os.environ.get("MCP_PORT", "8003"))
    mcp.run(transport="sse", host=host, port=port)

