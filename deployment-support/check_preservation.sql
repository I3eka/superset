-- Read-only fingerprints of user content; no connection strings or chat text leave PostgreSQL.
SELECT 'dashboards', count(*), md5(string_agg(ROW(id, dashboard_title, position_json, json_metadata, published)::text, '|' ORDER BY id)) FROM dashboards;
SELECT 'charts', count(*), md5(string_agg(ROW(id, slice_name, params, query_context, datasource_id, datasource_type)::text, '|' ORDER BY id)) FROM slices;
SELECT 'datasets', count(*), md5(string_agg(ROW(id, table_name, sql, database_id)::text, '|' ORDER BY id)) FROM tables;
SELECT 'databases', count(*), md5(string_agg(ROW(id, database_name, sqlalchemy_uri)::text, '|' ORDER BY id)) FROM dbs;
SELECT 'ai_threads', count(*), md5(string_agg(ROW(id, uuid, title, agent_key, created_by_fk)::text, '|' ORDER BY id)) FROM ai_chat_threads;
SELECT 'ai_messages', count(*), md5(string_agg(ROW(id, uuid, thread_id, role, content, status, extra_json)::text, '|' ORDER BY id)) FROM ai_chat_messages;
SELECT 'ai_feedback', count(*), md5(string_agg(ROW(id, message_id, liked, comment)::text, '|' ORDER BY id)) FROM ai_chat_feedback;
