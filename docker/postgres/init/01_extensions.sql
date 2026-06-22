-- Extensions required by the ICI platform
-- Runs once on first container start (postgres entrypoint convention)

\connect ici

CREATE EXTENSION IF NOT EXISTS "uuid-ossp";      -- gen_random_uuid()
CREATE EXTENSION IF NOT EXISTS "pgcrypto";        -- crypt(), digest()
CREATE EXTENSION IF NOT EXISTS "pg_trgm";         -- trigram similarity search
CREATE EXTENSION IF NOT EXISTS "btree_gin";       -- GIN on btree-able types
CREATE EXTENSION IF NOT EXISTS "btree_gist";      -- GiST on btree-able types
CREATE EXTENSION IF NOT EXISTS "vector";          -- pgvector (1536-dim embeddings)
CREATE EXTENSION IF NOT EXISTS "unaccent";        -- accent-insensitive search
CREATE EXTENSION IF NOT EXISTS "pg_stat_statements"; -- query performance monitoring

-- Trigram index operator class needed for LIKE queries on supplier names
-- (Used by SemanticSearchService.keyword_search)

-- Grant the app user appropriate permissions
GRANT ALL PRIVILEGES ON DATABASE ici TO ici;
GRANT ALL ON SCHEMA public TO ici;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO ici;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON SEQUENCES TO ici;
