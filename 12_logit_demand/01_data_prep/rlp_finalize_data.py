####################################################################################################
# Sources
# https://afdc.energy.gov/vehicles/electric_emissions_sources.html
phev_elec_share = 0.563

####################################################################################################
# Import libraries
import pathlib
import pandas as pd
import numpy as np
from itertools import combinations, product
import os
from tqdm import tqdm
import requests
from datetime import datetime
import geopandas as gpd
import matplotlib.pyplot as plt
import warnings
import platform

# Warnings and display
pd.set_option('display.max_rows', 500)
pd.set_option('display.max_columns', 500)

####################################################################################################
# Setup paths
str_cwd = pathlib.Path().resolve().parent
str_dir = str_cwd / "Documents" / "tobin_working_data"
str_rlp_data = str_dir / "rlpolk_data"
rlp_data_file = "ct_decoded_full_attributes.csv"

# Import the final RLP data
print(f"Importing the RLP data to be finalized, located at {str_rlp_data / rlp_data_file}")
df_rlp = pd.read_csv(str_rlp_data / rlp_data_file)
len_rlp = len(df_rlp)

# Print columns
print(df_rlp.columns)

####################################################################################################
# Import the energy price data
str_energy = str_dir / "energy_calcs"
str_gas_processed = str_energy / "monthly_gas_prices_processed.csv"
str_diesel_processed = str_energy / "monthly_diesel_prices_processed.csv"
str_electricity_processed = str_energy / "yearly_electricity_prices_processed.csv"

# Read in data
df_gas = pd.read_csv(str_gas_processed)
df_diesel = pd.read_csv(str_diesel_processed)
df_electricity = pd.read_csv(str_electricity_processed)

####################################################################################################
# Import the vin decoder data
if False: # We do not import the horsepower data for now. 
    warnings.warn("Horsepower Data: We re-import the VIN Decoder in this file. This should be moved to merge_RLP_policy_data.py")
    str_vindecoder = str_dir / "vin_decoder"
    df_vindecoder = pd.read_csv(str_vindecoder / "DataOne_IDP_yale_school_of_the_environment.csv")
    df_engines = pd.read_csv(str_vindecoder / "DEF_ENGINE.csv")

    df_engines["matched"] = 1
    df_vindecoder.columns = df_vindecoder.columns.str.lower()
    df_engines.columns = df_engines.columns.str.lower()

    df_vindecoder = df_vindecoder.merge(df_engines, left_on = "def_engine_id", right_on = "engine_id", how = "left")
    assert(df_vindecoder["matched"].sum() == len(df_vindecoder)), "Vindecoder was not a 1:1 match"

    df_vindecoder = df_vindecoder[["vin_pattern", "engine_id", "max_hp", "max_hp_at", "matched"]]
    df_vindecoder.loc[:, "vin_pattern"] = df_vindecoder.loc[:, "vin_pattern"].str[0:9]

####################################################################################################
# Clean the RLP data
df_rlp.loc[:, "report_year"] = df_rlp.loc[:, "report_year_month"].astype(str).str[:4].astype(int)
df_rlp.loc[:, "report_month"] = df_rlp.loc[:, "report_year_month"].astype(str).str[4:].astype(int)
df_rlp = df_rlp.drop(columns=["report_year_month"])

# Check how many NAs there are in the combined col
assert(df_rlp["fuel"].isna().sum() == 0)

####################################################################################################
# Clean the energy data make all columns lower case
df_gas.columns = df_gas.columns.str.lower()
df_diesel.columns = df_diesel.columns.str.lower()
df_electricity.columns = df_electricity.columns.str.lower()

####################################################################################################
# Merge the RLP data with the energy data
df_rlp = df_rlp.merge(df_gas[["year", "month", "gas_price_21"]], left_on = ["report_year", "report_month"],
                      right_on = ["year", "month"], how = "left")

df_rlp = df_rlp.merge(df_diesel[["year", "month", "diesel_price_21"]], left_on = ["report_year", "report_month"],
                      right_on = ["year", "month"], how = "left")

df_rlp = df_rlp.merge(df_electricity[["year", "electricity_price_21"]], left_on = ["report_year"],
                        right_on = ["year"], how = "left")

assert(len(df_rlp) == len_rlp), "Length mismatch"


# Confirm the match worked and drop the extra columns
assert(df_rlp["year_x"].equals(df_rlp["year_y"])), "Year mismatch"
assert(df_rlp["month_x"].equals(df_rlp["month_y"])), "Month mismatch"
assert(df_rlp["year_x"].equals(df_rlp["year"])), "Year mismatch"
assert(df_rlp["year_x"].equals(df_rlp["report_year"])), "Year mismatch"
assert(df_rlp["month_x"].equals(df_rlp["report_month"])), "Month mismatch"
df_rlp = df_rlp.drop(columns = ["year_x", "month_x", "year_y", "month_y", "year"])

####################################################################################################
# Calculate the dollar per mile for gasoline and hybrid
mask = ((df_rlp["fuel"] == "gasoline")|(df_rlp["fuel"] == "hybrid"))
df_rlp.loc[mask, "dollar_per_mile"] = df_rlp.loc[mask, "gas_price_21"] / df_rlp.loc[mask, "combined"]
print(df_rlp.loc[mask, ["gas_price_21", "combined", "dollar_per_mile"]].head())

# Calculate the dollar per mile for diesel
mask = (df_rlp["fuel"] == "diesel")
df_rlp.loc[mask, "dollar_per_mile"] = df_rlp.loc[mask, "diesel_price_21"] / df_rlp.loc[mask, "combined"]

# Calculate the dollar per for flex fuel, using gas price
mask = (df_rlp["fuel"] == "flex fuel")
df_rlp.loc[mask, "dollar_per_mile"] = df_rlp.loc[mask, "gas_price_21"] / df_rlp.loc[mask, "combined"]

# Calculate the dollar per mile for electric EPA fueleconomy.gov says 33.7 kWh per gallon
warnings.warn("PHEV Dollar Per Mile Calc: We do not have mpg_elec and mpg_gas, so we used combined mpg", category=UserWarning)
mask = (df_rlp["fuel"] == "phev")
df_rlp.loc[mask, "dollar_per_mile"] = ((df_rlp.loc[mask, "electricity_price_21"] * phev_elec_share) 
                                       + (df_rlp.loc[mask, "gas_price_21"] * (1 - phev_elec_share)))/ df_rlp.loc[mask, "combined"]

# Drop any vehicles not in the categories above
dropped_dollar_per_mile = df_rlp["dollar_per_mile"].isna().sum()
df_rlp = df_rlp.dropna(subset = ["dollar_per_mile"])
warnings.warn(f"Dropped {dropped_dollar_per_mile} vehicles that are not gasoline, hybrid diesel, flex fuel, or phev", category=UserWarning)

####################################################################################################
# Add the horsepower data
warnings.warn("Cannot match the horsepower data at this stage. This should be moved to merge_RLP_policy_data.py", category=UserWarning)

####################################################################################################
# Drop VINs available in less than 


####################################################################################################
# Save the final data
str_rlp_final = str_rlp_data / "rlp_with_dollar_per_mile.csv"
df_rlp.to_csv(str_rlp_final, index = False)

####################################################################################################
# Save data per market (i.e. county)
str_rlp_final_market = str_rlp_data / "rlp_with_dollar_per_mile_market.csv"

# Group by product and market
df_rlp_market = df_rlp.groupby(["vin_pattern", "county_name"]).sum().reset_index()
df_rlp_market = df_rlp_market.drop(columns = ["report_year", "report_month"])
