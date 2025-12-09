-- 1.Create extensions required for semantic search

CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_diskann CASCADE;
CREATE EXTENSION IF NOT EXISTS azure_ai;

SELECT * FROM pg_available_extensions WHERE name in ('azure_ai', 'vector', 'pg_diskann');

-- 2. define settings for azure AI model
select azure_ai.set_setting('azure_openai.endpoint', 'https://xxxx.openai.azure.com/'); 
select azure_ai.set_setting('azure_openai.subscription_key', 'xxxxx');



-- 3. Review the data used for generating embeddings
SELECT 
    p.name AS productname,
    ps.name AS productsubcategoryname,
    p.productnumber,
    p.color,
    pd.description,
    p.listprice
    FROM 
        production.product p
    LEFT JOIN production.productsubcategory ps 
        ON p.productsubcategoryid = ps.productsubcategoryid
    LEFT JOIN production.productmodel pm 
        ON p.productmodelid = pm.productmodelid
    LEFT JOIN production.productmodelproductdescriptionculture pmpdc 
        ON pm.productmodelid = pmpdc.productmodelid AND pmpdc.cultureid = 'en'
    LEFT JOIN production.productdescription pd 
        ON pmpdc.productdescriptionid = pd.productdescriptionid


drop table if exists production.productembeddings;

-- 4. Create the table to store the embeddings
CREATE TABLE production.productembeddings (
    id SERIAL PRIMARY KEY,
    productid INT NOT NULL,
    embedding VECTOR(1536) NOT NULL,
    CONSTRAINT fk_product FOREIGN KEY (productid)
        REFERENCES production.product(productid)
);




-- 5. Create the index for the embeddings
CREATE INDEX productembeddings_embedding_diskann_idx
ON production.productembeddings
USING diskann(embedding vector_cosine_ops);



-- 6. generate embeddings for products
INSERT INTO production.productembeddings (productid, embedding)
    SELECT 
        p.productid,
        azure_openai.create_embeddings(
            'text-embedding-ada-002',
            'Product: ' || p.name ||
            ' - Subcategory: ' || COALESCE(ps.name, 'No Category') ||
            ' - Number: ' || p.productnumber ||
            CASE 
                WHEN pd.description IS NOT NULL 
                THEN ' - Description: ' || pd.description 
                ELSE ''
            END ||
            CASE 
                WHEN p.color IS NOT NULL 
                THEN ' - Color: ' || p.color 
                ELSE ''
            END ||
            ' - Price: $' || p.listprice::text
        ) AS embedding
    FROM 
        production.product p
    LEFT JOIN production.productsubcategory ps 
        ON p.productsubcategoryid = ps.productsubcategoryid
    LEFT JOIN production.productmodel pm 
        ON p.productmodelid = pm.productmodelid
    LEFT JOIN production.productmodelproductdescriptionculture pmpdc 
        ON pm.productmodelid = pmpdc.productmodelid AND pmpdc.cultureid = 'en'
    LEFT JOIN production.productdescription pd 
        ON pmpdc.productdescriptionid = pd.productdescriptionid
    WHERE p.name IS NOT NULL
      AND p.productnumber IS NOT NULL;

SELECT * FROM production.productembeddings;


-- 7. test the similar products search
WITH vars AS (
    SELECT 'multi-activities bikes '::varchar AS search_term,
           azure_openai.create_embeddings('text-embedding-ada-002', 'multi-activities bikes')::vector AS search_embedding
)
SELECT
        p.name AS productname,
        ps.name AS productsubcategoryname,
        p.productnumber,
        p.color,
        pd.description,
        p.listprice,
        pe.embedding <=> vars.search_embedding AS similarity
FROM production.productembeddings pe
INNER JOIN production.product p ON pe.productid = p.productid
LEFT JOIN production.productsubcategory ps ON p.productsubcategoryid = ps.productsubcategoryid
LEFT JOIN production.productmodel pm ON p.productmodelid = pm.productmodelid
LEFT JOIN production.productmodelproductdescriptionculture pmpdc 
    ON pm.productmodelid = pmpdc.productmodelid AND pmpdc.cultureid = 'en'
LEFT JOIN production.productdescription pd ON pmpdc.productdescriptionid = pd.productdescriptionid
CROSS JOIN vars
WHERE pe.embedding <=> vars.search_embedding < 0.25
ORDER BY similarity
LIMIT 20;

-- 8. create a function to encapsulate the search logic
-- Adapted from SQL Server's find_relevant_products_vector_search procedure
-- Key differences from SQL Server version:
--   1. PostgreSQL uses FUNCTION instead of PROCEDURE for returning data
--   2. Cosine distance (<=> operator) instead of vector_search() function
--   3. Distance semantics: PostgreSQL distance (0=identical, 2=opposite) vs SQL Server similarity (1=identical, 0=opposite)
--   4. Conversion formula: SQL Server's (1-distance) > 0.3 becomes PostgreSQL's distance < 0.7 OR (1-distance) > 0.3
--   5. DEFAULT parameters instead of SQL Server's @param = default syntax
--   6. Named parameters using => syntax for clarity
-- Compute the embedding once per function call for efficiency.
CREATE OR REPLACE FUNCTION search_products(
    searchtext varchar,
    min_stock smallint DEFAULT 0,
    top_n int DEFAULT 20,
    min_similarity decimal DEFAULT 0.75  -- PostgreSQL cosine distance: lower = more similar, so 1-distance > 0.75 means distance < 0.25
)
RETURNS TABLE (
    productname varchar,
    productsubcategoryname varchar,
    productnumber varchar,
    description varchar,
    color varchar,
    listprice numeric,
    safetystocklevel smallint,
    similarity FLOAT
)
LANGUAGE SQL
STABLE
AS $$
    WITH embedding AS (
        SELECT azure_openai.create_embeddings('text-embedding-ada-002', searchtext)::vector AS search_embedding
    )
    SELECT
        p.name::varchar,
        COALESCE(ps.name, 'No Category')::varchar,
        p.productnumber::varchar,
        pd.description::varchar,
        p.color::varchar,
        p.listprice,
        p.safetystocklevel,
        (pe.embedding <=> embedding.search_embedding)::FLOAT AS similarity
    FROM production.productembeddings pe
    INNER JOIN production.product p ON pe.productid = p.productid
    LEFT JOIN production.productsubcategory ps ON p.productsubcategoryid = ps.productsubcategoryid
    LEFT JOIN production.productmodel pm ON p.productmodelid = pm.productmodelid
    LEFT JOIN production.productmodelproductdescriptionculture pmpdc 
        ON pm.productmodelid = pmpdc.productmodelid AND pmpdc.cultureid = 'en'
    LEFT JOIN production.productdescription pd ON pmpdc.productdescriptionid = pd.productdescriptionid
    CROSS JOIN embedding
    WHERE (1 - (pe.embedding <=> embedding.search_embedding)) > min_similarity  -- Convert distance to similarity
      AND p.safetystocklevel >= min_stock
    ORDER BY similarity
    LIMIT top_n;
$$;

-- Example usage:
-- Default search (top 20 results, any stock level, 75% similarity threshold)
-- SELECT * FROM search_products('bike accessories');

-- Search with custom parameters (top 10, min stock 500, 70% similarity)
-- SELECT * FROM search_products('outdoor gear', 500, 10, 0.70);

-- Search for high-stock items only
-- SELECT * FROM search_products('cycling equipment', min_stock => 500);

-- 9. Create procedure to insert new products and their embeddings
CREATE OR REPLACE PROCEDURE GenerateEmbeddingsProduct()
LANGUAGE SQL
AS $$
    INSERT INTO production.productembeddings (productid, embedding)
    SELECT 
        p.productid,
        azure_openai.create_embeddings(
            'text-embedding-ada-002',
            'Product: ' || p.name ||
            ' - Subcategory: ' || COALESCE(ps.name, 'No Category') ||
            ' - Number: ' || p.productnumber ||
            CASE 
                WHEN pd.description IS NOT NULL 
                THEN ' - Description: ' || pd.description 
                ELSE ''
            END ||
            CASE 
                WHEN p.color IS NOT NULL 
                THEN ' - Color: ' || p.color 
                ELSE ''
            END ||
            ' - Price: $' || p.listprice::text
        ) AS embedding
    FROM production.product p
    LEFT JOIN production.productsubcategory ps ON p.productsubcategoryid = ps.productsubcategoryid
    LEFT JOIN production.productmodel pm ON p.productmodelid = pm.productmodelid
    LEFT JOIN production.productmodelproductdescriptionculture pmpdc 
        ON pm.productmodelid = pmpdc.productmodelid AND pmpdc.cultureid = 'en'
    LEFT JOIN production.productdescription pd ON pmpdc.productdescriptionid = pd.productdescriptionid
    WHERE p.productid NOT IN (SELECT productid FROM production.productembeddings)
      AND p.name IS NOT NULL
      AND p.productnumber IS NOT NULL;
$$;

-- ============================================================================
-- SUMMARY: SQL Server to PostgreSQL Adaptation
-- ============================================================================
-- 
-- ORIGINAL (SQL Server):
--   - PROCEDURE with OUTPUT results
--   - vector_search() function with TABLE parameter
--   - @parameter naming convention
--   - Similarity metric: (1-distance) where 1=identical, 0=opposite
--
-- ADAPTED (PostgreSQL):
--   - FUNCTION with RETURNS TABLE
--   - Direct <=> cosine distance operator (pgvector extension)
--   - DEFAULT parameter syntax
--   - Distance metric: lower values = more similar (0=identical, 2=opposite)
--   - Conversion: SQL Server (1-distance) > 0.3 ≈ PostgreSQL distance < 0.7
--
-- NEW FEATURES:
--   1. Flexible parameters with PostgreSQL named argument syntax (param => value)
--   2. Includes safetystocklevel in results for stock analysis
--   3. Default min_similarity = 0.75 (more permissive than original 0.3)
--      This translates to distance < 0.25, suitable for broad semantic search
--   4. Default top_n = 20 (increased from 10) for better product discovery
--
-- USAGE EXAMPLES:
--   SELECT * FROM search_products('cycling accessories');
--   SELECT * FROM search_products('outdoor gear', 500, 10, 0.70);
--   SELECT * FROM search_products('bike', min_stock => 500, top_n => 5);
-- ============================================================================