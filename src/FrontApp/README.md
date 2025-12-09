# FrontApp: Flask UI for PostgreSQL Semantic Search & Natural Language to SQL.

This app provides a chat interface for interacting with your PostgreSQL data using Azure AI Foundry Agents and Microsoft Agent Framework.

## 1. Configure Environment Variables

Create a `.env` file in this folder using the provided template (e.g., `sample.env`).  
Example `.env` content:

```
AZURE_AI_AGENT_MODEL_DEPLOYMENT_NAME=<your-model-deployment-name>
AZURE_AI_AGENT_ENDPOINT=<your-azure-ai-endpoint>
MCP_SERVER_URL=<your-mcp-server-url>
MCP_SERVER_LABEL=<your-mcp-server-label>
APPLICATIONINSIGHTS_CONNECTION_STRING=<optional-azure-monitor-connection-string>
```

**Variable descriptions:**
- `AZURE_AI_AGENT_MODEL_DEPLOYMENT_NAME`and `AZURE_AI_MODEL_DEPLOYMENT_NAME`: The name of your deployed Azure OpenAI model (e.g., GPT-4, Ada embedding) used by the agent for chat and semantic search.
- `AZURE_AI_AGENT_ENDPOINT` and `AZURE_AI_PROJECT_ENDPOINT`: The endpoint URL for your Azure OpenAI resource (e.g., `https://<your-resource>.openai.azure.com/`).
- `AZURE_AI_AGENT_PROJECT_NAME`: the name of your Azure AI Project
- `MCP_SERVER_URL`: The URL of your MCP server that provides access to your PostgreSQL database
    - Container Apps - sample URL
    
        https://aca-account-name.westeurope.azurecontainerapps.io/sse

- `MCP_SERVER_LABEL`: A label or identifier for your MCP server connection (used by the agent to select the right backend).
- `APPLICATIONINSIGHTS_CONNECTION_STRING`: Azure Monitor Application Insights connection string for telemetry and tracing.

- Replace each `<...>` value with your actual configuration.
- The `APPLICATIONINSIGHTS_CONNECTION_STRING` is optional and only needed for telemetry.

## 2. Install Dependencies

Ensure you have Python 3.10+ and install required packages:

```sh
pip install -r requirements.txt
```
## 3. Authenticate with Azure CLI

Before starting, authenticate with Azure CLI with an account accessing to AI Foundry:

```sh
az login
```

## 4. Start the App

Run the Flask app:

```sh
python flask_chatbot_app.py
```



---

**Note:**  
- The app uses the variables from `.env` for configuration.
- For troubleshooting, check the Flask logs and ensure all environment variables are set.
