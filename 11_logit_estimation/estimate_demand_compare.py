############################################################################################################
# Import necessary packages
import numpy as np
import pandas as pd
import pyblp
import statsmodels.api as sm
import time
import os
import pickle
import sys
import pathlib
from scipy.optimize import minimize
import platform
from time import sleep

from linearmodels.iv import IV2SLS # this is to check IV results

from Reference import functions_v2 as exp_functions
import functions_rlp as rlp_functions 

# silence warnings
import warnings
warnings.filterwarnings("ignore")

# Pyblp settings
pyblp.options.verbose = False

############################################################################################################
# Settings
version = "CONNECTICUT"
model = 'logit'
integ = 'halton'
dynamic = False
incl_2021 = True
# rlp_market = 'model_year'
rlp_market ='county_model_year'
date_time = time.strftime("%m%d-%H%M")
zms_replaced_with = 0.01

############################################################################################################
# Set up directories
if platform.platform()[0:5] == 'macOS':
    cd = pathlib.Path().resolve().parent
    str_project = cd / "Documents" 
    str_data = str_project / "tobin_working_data"
    str_rlp = str_data / "rlpolk_data"
    str_sales_vin_characteristic = str_rlp / "rlp_with_dollar_per_mile.csv"
    output_folder = str_project / str_data / "outputs"
    str_mapping = str_rlp / "brand_to_oem_generated.csv"
    estimation_test = str_data / "estimation_data_test"
    # str_rlp_new = str_rlp / "rlp_with_dollar_per_mile_replaced_myear_20240413_064557_no_lease.csv" # NO LEASES
    str_rlp_new = str_rlp / "rlp_with_dollar_per_mile_replaced_myear_county_20240415_170934_no_lease_zms.csv" # NO LEASES + COUNTY + ZMS
    # str_rlp_new = str_rlp / "rlp_with_dollar_per_mile_replaced_myear_county_20240416_141637_inc_leases_zms.csv" # LEASES + COUNTY + ZMS
    # str_rlp_new = str_rlp / "rlp_with_dollar_per_mile_replaced_myear_20240411_183046.csv"

# Create subfolder for the outputs
output_subfolder = output_folder / f'comparison_outputs_{rlp_market}_{date_time}'
os.mkdir(output_subfolder)

# Create subfolder for the data
estimation_data_subfolder = estimation_test / f'comparison_data_{rlp_market}_{date_time}'
os.mkdir(estimation_data_subfolder)

############################################################################################################
# Set up logging
import logging
logging.basicConfig(filename= output_subfolder / f'estimate_demand_compare_{date_time}.log', level=logging.INFO, format='%(asctime)s - %(message)s')

def my_handler(type, value, tb):
    logging.exception("Uncaught exception: {0}".format(str(value)))

sys.excepthook = my_handler

description_template = f"""
Parameters:
-----------------------------------------------------------------------------------
Input: {str_rlp_new}
Output: {output_subfolder}
Estimation Data: {estimation_data_subfolder}
Replace ZMS with: {zms_replaced_with}
-----------------------------------------------------------------------------------
We have updated the code to incorporate removing makes and model years within the functions.
We test to ensure we get the same results as comparison_outputs_logit_county_model_year_0416-1336.csv.

NOTE: this differs from comparison_outputs_county_model_year_0417-0826 only in that we drop the makes
at the end of the function, as opposed to at the beginning. That should bring us closer to the original version. 
"""

description = description_template

if not description:
    raise ValueError("Please provide a description of the estimation")

logging.info("\n"+ description + "\n----------------------------------------------------------")
############################################################################################################
# Helper functions
def remove_makes(df, makes):
    for make in makes:
        df = df[df["make"]!=make]
    return df

############################################################################################################
# We prepare the Experian data for estimation
def prepare_experian_data(makes_to_remove = None):
    # read in VIN data - file of approx 100000 rows, including car characteristics
    exp_vin_data = exp_functions.read_vin_data(str_project,str_data,version,dynamic)

    # read in census data
    exp_census_data = exp_functions.read_census_data(str_project,str_data,version)

    # haircut first and potentially last market based on missing addl data - alters the census data
    exp_census_data = exp_functions.haircut_interpolate_census(str_project,str_data,exp_census_data,incl_2021)

    # Join
    exp_mkt_data = exp_functions.merge_vin_census(exp_vin_data,exp_census_data,version,dynamic)

    # Various cleaning functions, add a time trend, add a clustering ID for standard errors
    exp_mkt_data = exp_functions.clean_market_data(exp_mkt_data,version)

    # calculate outside good share
    exp_mkt_data = exp_functions.calc_outside_good(exp_mkt_data,version)

    # generate PyBLP instruments
    exp_mkt_data = exp_functions.generate_pyblp_instruments(exp_mkt_data)

    # remove makes
    if makes_to_remove:
        exp_mkt_data = remove_makes(exp_mkt_data, makes_to_remove)

    return exp_mkt_data

############################################################################################################
# We now prepare the RLP data, aiming to make it as similar as possible to the Experian data
def prepare_rlp_data(df, makes_to_remove = None, mkt_def = "model_year", year_to_drop = None, zms_replaced_with = 0.001):
    # Drop relevant market years
    df = df.loc[~((df["model_year"]==2016) | (df["model_year"]==2017) | (df["model_year"]==2023))].reset_index(drop=True)
    if year_to_drop:
        df = df.loc[df["model_year"]!=year_to_drop].reset_index(drop=True)

    # Replace ZMS with a small number
    df.loc[df["veh_count"]==0, "veh_count"] = zms_replaced_with

    # Set the product IDs
    df["product_ids"] = df.make + "_" + df.model + "_" + df.model_year.astype(str) + "_" + df.trim + "_" + df.fuel + "_" + df.range_elec.astype(str).str[0:3]
    
    # Set the market IDs
    if mkt_def == "model_year":
        df["market_ids"] = df["model_year"]
    elif mkt_def == "county_model_year":
        df["market_ids"] = df["county_name"] + "_" + df["model_year"].astype(str)

    # Generate firm IDs and fueltype dummies
    df = rlp_functions.generate_firm_ids(df, str_mapping)
    df = rlp_functions.generate_fuel_type_dummies(df)

    # Read in census data - contains total number of households for each county in Connecticut
    if mkt_def == "model_year":
        census_data = pd.read_csv(str_data / "other_data" / "hhs_by_year.csv")
    elif mkt_def == "county_model_year":
        census_data = pd.read_csv(str_data / "other_data" / "hhs_by_year_counties.csv")

    # Merge the VIN data with the census data
    mkt_data = rlp_functions.merge_vin_census(df, census_data, rlp_market)

    # Clean the market data
    mkt_data = rlp_functions.clean_market_data(mkt_data, rlp_market)

    # Calculate the share of the outside good
    mkt_data = rlp_functions.calc_outside_good(mkt_data)

    # Add instruments 
    mkt_data = rlp_functions.generate_pyblp_instruments(mkt_data)

    # Rename columns
    mkt_data = mkt_data.rename(columns = {'log_hp_wt':'log_hp_weight', 'drive_type':'drivetype', 'body_type':'bodytype'})

    # Remove makes
    if makes_to_remove:
        mkt_data = remove_makes(mkt_data, makes_to_remove)

    return mkt_data

############################################################################################################
rlp_df = pd.read_csv(str_rlp_new)
exp_mkt_data = prepare_experian_data(makes_to_remove=["Polestar", "Smart", "Lotus", "Scion", "Maserati"])

rlp_mkt_data = prepare_rlp_data(rlp_df, 
                                makes_to_remove = ["Polestar", "Smart", "Lotus", "Scion", "Maserati"],
                                mkt_def = rlp_market, zms_replaced_with = zms_replaced_with)



############################################################################################################
if False:
    rlp_mkt_data = rlp_mkt_data[rlp_mkt_data["make"]!="Polestar"]
    exp_mkt_data = exp_mkt_data[exp_mkt_data["make"]!="Polestar"]

    # Remove Smart from both
    rlp_mkt_data = rlp_mkt_data[rlp_mkt_data["make"]!="Smart"]
    exp_mkt_data = exp_mkt_data[exp_mkt_data["make"]!="Smart"]

    # Drop Lotus from both
    rlp_mkt_data = rlp_mkt_data[rlp_mkt_data["make"]!="Lotus"]
    exp_mkt_data = exp_mkt_data[exp_mkt_data["make"]!="Lotus"]

    # Drop scion from experian
    exp_mkt_data = exp_mkt_data[exp_mkt_data["make"]!="Scion"]

    # Drop make Genesis from both
    # exp_mkt_data = exp_mkt_data[exp_mkt_data["make"]!="Genesis"]
    # rlp_mkt_data = rlp_mkt_data[rlp_mkt_data["make"]!="Genesis"]

    # Drop Maserati from both
    exp_mkt_data = exp_mkt_data[exp_mkt_data["make"]!="Maserati"]
    rlp_mkt_data = rlp_mkt_data[rlp_mkt_data["make"]!="Maserati"]


####################
# run PyBLP models #
####################
exp_mkt_data_keep = exp_mkt_data.copy()
rlp_mkt_data_keep = rlp_mkt_data.copy()


def run_logit_model(exp_df, rlp_df, subfolder, estimation_data_folder, myear = "all"):
    # Set up the output dataframes
    output_logit = pd.DataFrame()
    output_ols = pd.DataFrame()

    params_master = ['prices', 'dollar_per_mile', 'electric', 'phev', 'hybrid', 'diesel', 'log_hp_weight', 'wheelbase', 'doors', 'range_elec', 'make', 'drivetype', 'bodytype']
    # params_master = ['prices', 'electric', 'phev', 'hybrid', 'diesel', 'log_hp_weight', 'wheelbase', 'doors', 'range_elec', 'make', 'drivetype', 'bodytype']
    specifications = [1, 2, 6, 7, 8, 9, 10]
    # specifications = [1, 5, 6, 7, 8, 9]


    if isinstance(myear, int):
        rlp_df = rlp_df[rlp_df["model_year"]==myear]

    # Save the market data
    exp_df.to_csv(estimation_data_folder / f'exp_mkt_data_{date_time}.csv',index = False)
    rlp_df.to_csv(estimation_data_folder / f'mkt_data_{rlp_market}_{date_time}_{myear}.csv',index = False)

    for j in specifications:
        output_specification_logit = pd.DataFrame()
        output_specification_ols = pd.DataFrame()
        for i, mkt_data in enumerate([exp_df, rlp_df]):
            if i == 0:
                data_source = "Experian"
                print(f"Running {data_source} data")
            elif i == 1:
                data_source = "RLP"
                print(f"Running {data_source} data")
            

            # Prepare the Logit specification
            if data_source == "RLP" and rlp_market == "county_model_year":
                params_logit = params_master[0:j] + [f'C({param})' for param in params_master[-3:]]+['C(county_name)']
                params_ols = params_master[0:j] + [param for param in params_master[-3:]]+['county_name']
            else:
                params_logit = params_master[0:j] + [f'C({param})' for param in params_master[-3:]]
                params_ols = params_master[0:j] + [param for param in params_master[-3:]]
            params_str = ' + '.join(params_logit)
            params_str = '0 + ' + params_str
            logit_formulation = pyblp.Formulation(params_str)

            # Log the datasource and specification
            logging.info(f"Data Source: {data_source}")
            logging.info(f"Specification: {params_str}")

            # Run the logit problem
            problem = pyblp.Problem(logit_formulation, mkt_data)
            logit_results_price_updated = problem.solve()

            # Prepare the OLS specification and Convert make, drivetype, and bodytype to dummies
            X = mkt_data[params_ols]
            if data_source == "RLP" and rlp_market == "county_model_year":
                X = pd.get_dummies(X, columns = ['make', 'drivetype', 'bodytype', 'county_name'], drop_first = True)
            else:
                X = pd.get_dummies(X, columns = ['make', 'drivetype', 'bodytype'], drop_first = True)
            Y = mkt_data['shares']
            X = sm.add_constant(X)
            ols_results = sm.OLS(Y.astype(float), X.astype(float)).fit()

            # save results
            df_logit = pd.DataFrame({'specification':j,
                                'data_source':data_source,
                                'param':logit_results_price_updated.beta_labels,
                                'value':logit_results_price_updated.beta.flatten(),
                                'se': logit_results_price_updated.beta_se.flatten()})
            
            df_ols = pd.DataFrame({'specification':j,
                                'data_source':data_source,
                                'param':ols_results.params.index,
                                'value':ols_results.params.values,
                                'se': ols_results.bse.values})
            
            output_specification_logit = pd.concat([output_specification_logit, df_logit], axis = 1)
            output_specification_ols = pd.concat([output_specification_ols, df_ols], axis = 1)

        output_logit = pd.concat([output_logit, output_specification_logit], axis = 0)
        output_ols = pd.concat([output_ols, output_specification_ols], axis = 0)

    output_logit.to_csv(subfolder / f'comparison_outputs_logit_{rlp_market}_{date_time}_{myear}.csv',index = False)
    output_ols.to_csv(subfolder / f'comparison_outputs_ols_{rlp_market}_{date_time}_{myear}.csv',index = False)


run_logit_model(exp_mkt_data, rlp_mkt_data, output_subfolder, estimation_data_subfolder, myear = "all_years")


if False:
    for myear in sorted(rlp_mkt_data["model_year"].unique()):
        print("Running model year: ", myear)
        try:
            run_logit_model(exp_mkt_data, rlp_mkt_data, output_subfolder, estimation_data_subfolder, myear)
        except:
            print("Error running model year: ", myear)
            logging.info(f"Error running model year: {myear}")