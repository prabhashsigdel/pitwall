import duckdb

con = duckdb.connect("data/f1.duckdb")
con.sql("CALL start_ui()")
print("DuckDB UI running at http://localhost:4213")
input("Press Enter to close it (do this before running any scripts)...")
con.close()
