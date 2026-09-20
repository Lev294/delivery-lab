\getenv replication_password REPLICATION_PASSWORD
SELECT format('CREATE ROLE replicator LOGIN REPLICATION PASSWORD %L', :'replication_password')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='replicator') \gexec
SELECT format('ALTER ROLE replicator PASSWORD %L', :'replication_password') \gexec
