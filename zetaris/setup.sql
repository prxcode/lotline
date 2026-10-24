-- Register the three Postgres databases in Zetaris. Run one statement at a time in the SQL Editor.

CREATE DATASOURCE lotline_warehouse DESCRIBE BY "Food bank warehouse system" OPTIONS (
  jdbcdriver "org.postgresql.Driver",
  jdbcurl "jdbc:postgresql://host.docker.internal:5433/warehouse",
  username "lotline",
  password "<LOTLINE_DB_PASSWORD>",
  fetchsize "1000"
);

REGISTER DATASOURCE TABLES FROM lotline_warehouse;

CREATE DATASOURCE lotline_distribution DESCRIBE BY "Food bank distribution system" OPTIONS (
  jdbcdriver "org.postgresql.Driver",
  jdbcurl "jdbc:postgresql://host.docker.internal:5433/distribution",
  username "lotline",
  password "<LOTLINE_DB_PASSWORD>",
  fetchsize "1000"
);

REGISTER DATASOURCE TABLES FROM lotline_distribution;

CREATE DATASOURCE lotline_recalls DESCRIBE BY "Food bank recalls system" OPTIONS (
  jdbcdriver "org.postgresql.Driver",
  jdbcurl "jdbc:postgresql://host.docker.internal:5433/recalls",
  username "lotline",
  password "<LOTLINE_DB_PASSWORD>",
  fetchsize "1000"
);

REGISTER DATASOURCE TABLES FROM lotline_recalls;

-- Check: expect 180
SELECT COUNT(*) FROM lotline_warehouse.inventory;

-- Then in .env:
-- DATA_BACKEND=zetaris
-- ZETARIS_TABLES=inventory=lotline_warehouse.inventory,distributions=lotline_distribution.distributions,agencies=lotline_distribution.agencies,recalls=lotline_recalls.recalls,recall_products=lotline_recalls.recall_products
