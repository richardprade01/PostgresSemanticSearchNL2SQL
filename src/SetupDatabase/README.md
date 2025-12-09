# Setup Instructions for Semantic Search on Azure PostgreSQL Flexible Server

This guide describes how to configure an Azure PostgreSQL Flexible Server with the required extensions, restore the AdventureWorks database, and set up semantic search using the provided SQL script.

## Prerequisites

- Azure PostgreSQL Flexible Server (version 16+)
- Sufficient permissions to install extensions
- AdventureWorks backup file: `AdventureWorksPG.gz`
- Access to Azure OpenAI (endpoint and subscription key)



## 1. Configure Azure PostgreSQL Flexible Server

1. **Create a PostgreSQL Flexible Server**  
   Use the Azure Portal or CLI to create a PostgreSQL Flexible Server at least version 16.

2. **Enable Extensions at server level**  
   1. Navigate to the 'Server Parameters' section for the Azure Database for PostgreSQL Flexible Server.
   2. Find the azure.extensions option from the Server parameters list and enable 
      - `vector`
      - `pg_diskann`
      - `azure_ai`
      - `TABLEFUNC`
      - `UUID-OSSP`
 
## 2. Setup the AdventureWorks Database

You can refer to this official microsoft repository for setting up the database : 
https://github.com/Azure-Samples/postgresql-samples-databases/tree/main/postgresql-adventureworks

 ### Restore AdventureWorks Database Using pg_restore

To restore the database from a `.backup` file (e.g., `AdventureWorksPG.gz`), use the following command:

```sh
 pg_restore -U <username> -C -d postgres -h <host> -p <port> --verbose --no-owner --no-privileges "<path>\AdventureWorksPG.gz"
```
Note: If you are not using Version 17 tools, add to define PGPASSWORD as environment variable before.  
Replace `<username>`, `<database>`, `<host>`, and `<port>` with your PostgreSQL server details.

## 3. Configure the database to be AI ready
- Follow the different steps in the script [database.sql](database.sql)
   
   - Get the Azure OpenAI Endpoint from your AI Foundry project folder, the URL has the following pattern:
   https://AI-Foundry-name.openai.azure.com/

   - Edit `database.sql` to set your Azure OpenAI endpoint and subscription key:

   ```sql
   select azure_ai.set_setting('azure_openai.endpoint', '<your-endpoint>');
   select azure_ai.set_setting('azure_openai.subscription_key', '<your-key>');
   ```
  
- Run the database.sql script script is organized in 9 steps:

   1. **Create extensions required for semantic search**  
      Installs the necessary PostgreSQL extensions: `vector`, `pg_diskann`, and `azure_ai`.

   2. **Define settings for Azure AI model**  
      Configures the Azure OpenAI endpoint and subscription key for embedding generation.

   3. **Review the data used**  
      Shows the product data from that will be embedded.

   4. **Create the table to store the embeddings**  
      Creates the `productembeddings` table to store product keys and their vector embeddings.

   5. **Create the index for the embeddings**  
      Adds a DiskANN index to the embeddings column for fast similarity search.

   6. **Generate embeddings for products**  
      Inserts embeddings for each product using Azure OpenAI, skipping rows with null values.

   7. **Test the similar products search**  
      Demonstrates a semantic similarity search using a sample search term.

   8. **Create a function to encapsulate the search logic**  
      Defines a SQL function `search_products` to perform semantic search for any input text.

   9. **Create a procedure to insert new products and their embeddings**  
      Adds a procedure `GenerateEmbeddingsProduct` to insert embeddings for new products not yet embedded.

---

**Note:**  
- Ensure your server has outbound access to Azure OpenAI.
- Adjust vector dimensions if using a different embedding model.
- Different Schemas:
   1. Person
      Comment: Centralizes identity and contact information for all business entities—employees, customers, vendors, and store contacts. It includes personal details, addresses, phone numbers, and email preferences.
   2. HumanResources
   Comment: Manages employee-related data such as job titles, departments, pay history, and shift schedules. It supports HR operations and organizational structure tracking.
   3. Production
   Comment: Covers the manufacturing and product lifecycle, including product definitions, bills of materials, work orders, and inventory. It’s key for managing production workflows and product data.
   4. Purchasing
   Comment: Handles procurement processes, vendor relationships, and purchase orders. It supports supply chain management and vendor performance tracking.
   5. Sales
   Comment: Tracks customer interactions, sales orders, promotions, and territories. It’s essential for revenue analysis, customer segmentation, and sales performance reporting.
