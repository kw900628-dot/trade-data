import FinanceDataReader as fdr
import pandas as pd

try:
    print("Attempting to fetch KOSPI list via FDR...")
    df = fdr.StockListing('KOSPI')
    print(f"Success! Fetched {len(df)} rows from FDR.")
except Exception as e:
    print(f"FDR Failed: {e}")

try:
    print("\nReading local CSV...")
    df_csv = pd.read_csv('kospi_stocks.csv')
    print(f"CSV contains {len(df_csv)} rows.")
except Exception as e:
    print(f"CSV Read Failed: {e}")
