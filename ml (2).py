# -*- coding: utf-8 -*-
"""ml.ipynb

Automatically generated by Colab.

Original file is located at
    https://colab.research.google.com/drive/1-g25fvG-2q4XHZwZDWCeVz2zlGaYBODr
"""

# In Cloud Composer, add snowflake-connector-python to PYPI Packages
from airflow import DAG
from airflow.models import Variable
from airflow.decorators import task
from datetime import datetime
import snowflake.connector

# Snowflake connection function
def return_snowflake_conn():
    user_id = Variable.get('snowflake_userid')
    password = Variable.get('snowflake_password')
    account = Variable.get('snowflake_account')

    # Establish a connection to Snowflake
    conn = snowflake.connector.connect(
        user=user_id,
        password=password,
        account=account,  # Example: 'xyz12345.us-east-1'
        warehouse='compute_wh',
        database='dev2'  # Updated to the 'dev2' database
    )
    # Create a cursor object
    return conn.cursor()

@task
def create_forecast_function(cur, forecast_function_name):
    """
    Create the predict_stock_price function if it does not exist.
    """
    create_function_sql = f"""
    CREATE OR REPLACE FUNCTION {forecast_function_name}(input_data VARIANT)
    RETURNS TABLE (date TIMESTAMP_NTZ, close FLOAT, symbol STRING)
    LANGUAGE PYTHON
    RUNTIME_VERSION = '3.8'
    HANDLER = 'handler'
    AS $$
    def handler(input_data):
        import pandas as pd
        # Your model prediction logic here
        # ...
        return pd.DataFrame(...)  # Return a DataFrame with 'date', 'close', 'symbol'
    $$;
    """

    try:
        cur.execute(create_function_sql)
    except Exception as e:
        print("Function creation error:", e)
        raise

@task
def train(cur, train_input_table, train_view, forecast_function_name):
    """
    Create a view with training-related columns and train a forecast model.
    """
    create_view_sql = f"""
    CREATE OR REPLACE VIEW {train_view} AS
    SELECT DATE, CLOSE, SYMBOL
    FROM {train_input_table};"""

    create_model_sql = f"""
    CREATE OR REPLACE SNOWFLAKE.ML.FORECAST {forecast_function_name} (
        INPUT_DATA => SYSTEM$REFERENCE('VIEW', '{train_view}'),
        SERIES_COLNAME => 'SYMBOL',
        TIMESTAMP_COLNAME => 'DATE',
        TARGET_COLNAME => 'CLOSE',
        CONFIG_OBJECT => {{ 'ON_ERROR': 'SKIP' }}
    );"""

    try:
        cur.execute(create_view_sql)
        cur.execute(create_model_sql)
        # Inspect the accuracy metrics of your model.
        cur.execute(f"CALL {forecast_function_name}!SHOW_EVALUATION_METRICS();")
    except Exception as e:
        print(e)
        raise

@task
def predict(cur, forecast_function_name, train_input_table, forecast_table, final_table):
    """
    Generate predictions, store them in a forecast table, and combine with historical data.
    """
    make_prediction_sql = f"""
    BEGIN
        CALL {forecast_function_name}!FORECAST(
            FORECASTING_PERIODS => 7,
            CONFIG_OBJECT => {{'prediction_interval': 0.95}}
        );
        LET x := SQLID;
        CREATE OR REPLACE TABLE {forecast_table} AS SELECT * FROM TABLE(RESULT_SCAN(:x));
    END;"""

    create_final_table_sql = f"""
    CREATE OR REPLACE TABLE {final_table} AS
    SELECT SYMBOL, DATE, CLOSE AS actual, NULL AS forecast, NULL AS lower_bound, NULL AS upper_bound
    FROM {train_input_table}
    UNION ALL
    SELECT REPLACE(series, '"', '') AS SYMBOL, ts AS DATE, NULL AS actual, forecast, lower_bound, upper_bound
    FROM {forecast_table};"""

    try:
        cur.execute(make_prediction_sql)
        cur.execute(create_final_table_sql)
    except Exception as e:
        print(e)
        raise

# Airflow DAG definition
with DAG(
    dag_id='StockPriceTrainPredict4',  # Updated DAG name
    start_date=datetime(2024, 9, 21),
    catchup=False,
    tags=['ML', 'ELT', 'stock'],
    schedule_interval='30 2 * * *'  # Scheduled to run daily at 2:30 AM
) as dag:

    train_input_table = "dev2.rawdata2.stock_prices"  # Updated schema and table name
    train_view = "dev2.adhoc.stock_data_view"  # Temporary view for training
    forecast_table = "dev2.adhoc.stock_data_forecast"  # Table to store forecasted data
    forecast_function_name = "dev2.analytics.predict_stock_price"  # Forecasting function name
    final_table = "dev2.analytics.stock_data_final"  # Final combined table

    # Snowflake connection
    cur = return_snowflake_conn()

    # Task: Create forecast function if it doesn't exist
    create_forecast_function(cur, forecast_function_name)

    # Task: Train the model
    train(cur, train_input_table, train_view, forecast_function_name)

    # Task: Generate predictions and combine with historical data
    predict(cur, forecast_function_name, train_input_table, forecast_table, final_table)