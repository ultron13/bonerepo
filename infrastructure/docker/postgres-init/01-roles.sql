-- plimsoll_owner owns the schema and runs migrations.
-- plimsoll_app is the runtime role. It owns nothing, so row-level security
-- applies to it -- PostgreSQL never applies policies to a table's owner.
CREATE ROLE plimsoll_owner LOGIN PASSWORD 'plimsoll_owner_dev';
CREATE ROLE plimsoll_app   LOGIN PASSWORD 'plimsoll_app_dev';

ALTER DATABASE plimsoll OWNER TO plimsoll_owner;

\connect plimsoll
GRANT USAGE ON SCHEMA public TO plimsoll_app;
ALTER SCHEMA public OWNER TO plimsoll_owner;
