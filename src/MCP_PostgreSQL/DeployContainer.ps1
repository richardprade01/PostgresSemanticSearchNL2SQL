$env:PGHOST="your-server.postgres.database.azure.com"
$env:PGUSER="your-username"
$env:PGPASSWORD="your-password"
$env:MCP_HOST="localhost"
$env:MCP_PORT="8003"
$env:CONTAINER_NAME="azure-postgresql-mcp001"
$env:RESOURCE_GROUP="your-resource-group"
$env:CONTAINER_APP_NAME="yourcontainerappname"
$env:REGISTRY_NAME="yourregistryname"
$env:LOCATION ="France Central"


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


# Create Container Apps environment
az containerapp env create --name $env:CONTAINER_APP_NAME --resource-group $env:RESOURCE_GROUP --location $env:LOCATION

# Enable admin user for ACR (required for Container Apps to pull images)
az acr update --name $env:REGISTRY_NAME --admin-enabled true

# Get ACR credentials 
$ACR_USERNAME = az acr credential show --name $env:REGISTRY_NAME --query username --output tsv
$ACR_PASSWORD = az acr credential show --name $env:REGISTRY_NAME --query passwords[0].value --output tsv


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


az containerapp show `
  --name $env:CONTAINER_NAME `
  --resource-group $env:RESOURCE_GROUP `
  --query properties.configuration.ingress.fqdn `
  --output tsv