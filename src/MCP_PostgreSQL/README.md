# Summary

This directory provides resources and components for implementing a Management Control Point (MCP) for PostgreSQL, enabling semantic search and NL2SQL and data querying through two approaches:
- Container (FastMCP) MCP Server (Python)

## MCP_PostgreSQL

- **azure_postgresql_mcp_sse.py**: Python script for rapid semantic search and data processing with PostgreSQL.
- **README.md**: Documentation for the FastMCP_PostgreSQL module.
- **sample.env**: Sample environment configuration file for local setup.



# Azure PostgreSQL MCP Server

This Python server exposes various management and query capabilities for Azure Database for PostgreSQL - Flexible Server via HTTP Server-Sent Events (SSE).

## Features

This MCP server exposes the following capabilities:

### Tools

- `get_databases`: Gets the list of all databases in a server instance
- `get_table_schemas`: Gets schemas of all tables in a specific schema
- `query_data`: Runs read queries on a database
- `get_similarproducts`: Gets similar products based on a string request
- `update_values` : perform update query on a database

## Setup Instructions

### 1. Environment Variables

Before running the server, set the following environment variables. You can use the provided sample.env template:

The server uses python-dotenv to load variables from .env automatically.
Alternatively, set them manually in your PowerShell session:

#### Example sample.env
```
PGHOST=<Fully qualified name of your Azure Database for PostgreSQL instance>
PGUSER=<Your Azure Database for PostgreSQL username>
PGPASSWORD=<Your password>
MCP_HOST=localhost
MCP_PORT=8003
```

### 2. Local Deployment

#### Run directly with Python:
``` powershell
cd .src\MCP_PostgreSQL\
# Run the server
What is the sales amount per categories and year 
python azure_postgresql_mcp_sse.py
```


### 3. Remote Deployment to Azure Container Apps
#### How to set the Variables in powershell

``` powershell
$env:PGHOST="your-server.postgres.database.azure.com"
$env:PGUSER="your-username"
$env:PGPASSWORD="your-password"
$env:MCP_HOST="localhost"
$env:MCP_PORT="8003"
$env:CONTAINER_NAME="azure-postgresql-mcp001"
$env:RESOURCE_GROUP="your-resource-group"
$env:CONTAINER_APP_NAME="yourcontainerappname"
$env:REGISTRY_NAME="yourregistryname"
$env:LOCATION ="your-location"
```

#### Deploy to Azure Container Apps

##### Step 1: Build and Push to Azure Container Registry

```powershell
# Build the Docker image
docker build -t "$($env:CONTAINER_NAME):latest" .

#### Push to Azure Container Registry (ACR):
# Login to Azure
az login

# Create a resource group (if needed)
az group create --name $env:RESOURCE_GROUP --location $env:LOCATION

# Create Azure Container Registry
az acr create --resource-group $env:RESOURCE_GROUP --name $env:REGISTRY_NAME --sku Basic

# Login to ACR
az acr login --name $env:REGISTRY_NAME

# Get ACR login server 
$ACR_LOGIN_SERVER = az acr show --name $env:REGISTRY_NAME --query loginServer --output tsv

# Tag your image 
docker tag azure-postgresql-mcp:latest "$($ACR_LOGIN_SERVER)/$($env:CONTAINER_NAME):latest"

# Push to ACR 
docker push "$($ACR_LOGIN_SERVER)/$($env:CONTAINER_NAME):latest"

```

##### Step 2: Create Container Apps Environment

```powershell
# Create Container Apps environment
az containerapp env create --name $env:CONTAINER_APP_NAME --resource-group $env:RESOURCE_GROUP --location $env:LOCATION

```

#### Step 3: Setup ACR Authentication

```powershell
# Enable admin user for ACR (required for Container Apps to pull images)
az acr update --name $env:REGISTRY_NAME --admin-enabled true

# Get ACR credentials 
$ACR_USERNAME = az acr credential show --name $env:REGISTRY_NAME --query username --output tsv
$ACR_PASSWORD = az acr credential show --name $env:REGISTRY_NAME --query passwords[0].value --output tsv
```

#### Step 4: Deploy Container App

```powershell
# Create the container app 
az containerapp create `
  --name $env:CONTAINER_NAME `
  --resource-group $env:RESOURCE_GROUP `
  --environment $env:CONTAINER_APP_NAME `
  --image "$($ACR_LOGIN_SERVER)/$($env:CONTAINER_NAME):latest" `
  --min-replicas 1 `
  --max-replicas 3 `
  --cpu 1.0 `
  --memory 2.0Gi `
  --target-port $env:MCP_PORT `
  --ingress 'external' `
  --registry-server $ACR_LOGIN_SERVER `
  --registry-username $ACR_USERNAME `
  --registry-password $ACR_PASSWORD `
  --env-vars `
    PGHOST=$env:PGHOST `
    PGUSER=$env:PGUSER `
    PGPASSWORD=$env:PGPASSWORD
```

#### Step 5: Get the Container App URL

```powershell
# Get the application URL 
az containerapp show `
  --name $env:CONTAINER_NAME `
  --resource-group $env:RESOURCE_GROUP `
  --query properties.configuration.ingress.fqdn `
  --output tsv

```
Your MCP server will be available at: `https://<your-app-url>/sse`
You can test the MCP with [Inspector](https://modelcontextprotocol.io/docs/tools/inspector) Tool 
Launch Inspector, command npx @modelcontextprotocol/inspector  

### 4. Configure VSCode MCP

Add to your VSCode MCP configuration (`mcp.json`):

> **How to locate `mcp.json`:**
> 1. In Visual Studio Code, go to the **Settings** icon (⚙️) in the bottom left corner of the window.
> 2. Select **Profiles** from the menu.
> 3. Look for the `mcp.json` configuration file within your profile settings.

#### For Local Development:
```json
{
  "servers": {
    "PostgreSQL_Local": {
      "name": "PostgreSQL Local",
      "url": "http://localhost:8003/sse",
      "type": "http"
    }
  }
}
```
or
#### For Azure Container Apps:
```json
{
  "servers": {
    "PostgreSQL_Azure": {
      "name": "PostgreSQL Azure",
      "url": "https://<your-app-url>/sse",
      "type": "http"
    }
  }
}
```
> 4. Start the MCP.

### 5. Test with GitHub Copilot Agent

You can interact with your deployed MCP server using GitHub Copilot Agent in VSCode. Here’s a step-by-step guide:

#### Step 1: Install and Configure GitHub Copilot Agent

- Ensure you have the [GitHub Copilot Chat extension](https://marketplace.visualstudio.com/items?itemName=GitHub.copilot-chat) installed in VSCode.
#### Step 2: Add MCP Server to Copilot Agent

- Open the Copilot Chat sidebar in VSCode.
- Click the "tool" (🔧) icons in the sidebar to manage endpoints.
- Add your MCP server endpoint (e.g., `MCPServer:PostgreSQL Local` or `PostgreSQL Azure`) using the UI.
- After connecting, open the Agent tool and ensure your MCP server is selected as the active endpoint before running queries.

#### Step 3: Run Semantic Search Query

Ask Copilot Agent in chat:

```
advise me 3 products suitable bikes for multi-activities
```

- The agent will use the semantic search capability of your MCP server to recommend products.

#### Step 4: Run NL2SQL Query

Follow up in chat with:

```
tell me the total sales for these products
```

- The agent will translate your natural language request into SQL and query the MCP server for the answer.

#### Step 5: Review Results

- Copilot Agent will display the results directly in the chat window.
- You can iterate with further questions or queries as needed.

> **Tip:** Ensure your MCP server is reachable from your development environment and that your environment variables are set correctly for authentication.
