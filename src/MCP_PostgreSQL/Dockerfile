# Use Python 3.11 slim image as base
FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements file and install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the application file
COPY azure_postgresql_mcp_sse.py .

# Expose the default port (based on your code, likely 8003)
EXPOSE 8003

# Set environment variables
ENV MCP_HOST=0.0.0.0
ENV MCP_PORT=8003

# Command to run the application
CMD ["python", "azure_postgresql_mcp_sse.py"]