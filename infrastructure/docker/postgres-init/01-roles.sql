-- plimsoll_owner owns the schema and runs migrations.
-- plimsoll_app is the runtime role. It owns nothing, so row-level security
-- applies to it -- PostgreSQL never applies policies to a table's owner.
CREATE ROLE plimsoll_owner LOGIN PASSWORD 'plimsoll_owner_dev';
CREATE ROLE plimsoll_app   LOGIN PASSWORD 'plimsoll_app_dev';

-- plimsoll_auth exists only to own the SECURITY DEFINER login lookup, which
-- must resolve a user before any organisation is known. FORCE ROW LEVEL
-- SECURITY subjects even a table's owner to its policies, so a lookup owned
-- by plimsoll_owner would return nothing at exactly that moment. The role
-- cannot log in and owns nothing but that one function.
-- BYPASSRLS is a role attribute and is never inherited through membership,
-- so granting it to plimsoll_owner does not make plimsoll_owner exempt.
CREATE ROLE plimsoll_auth NOLOGIN BYPASSRLS;
GRANT plimsoll_auth TO plimsoll_owner;

ALTER DATABASE plimsoll OWNER TO plimsoll_owner;

\connect plimsoll
GRANT USAGE ON SCHEMA public TO plimsoll_app;
GRANT USAGE ON SCHEMA public TO plimsoll_auth;
ALTER SCHEMA public OWNER TO plimsoll_owner;
