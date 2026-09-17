\getenv app_password APP_DB_PASSWORD
SELECT format('CREATE ROLE app LOGIN PASSWORD %L', :'app_password') \gexec
GRANT USAGE, CREATE ON SCHEMA public TO app;
