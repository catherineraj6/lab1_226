# -*- coding: utf-8 -*-
"""forecast2

Automatically generated by Colab.

Original file is located at
    https://colab.research.google.com/drive/1vL64DjaDCwizhFgZSrqq5rZgAsHyRcn3
"""

from airflow import DAG
from airflow.models import Variable
from airflow.decorators import task
from datetime import datetime, timedelta
import snowflake.connector
import requests

# Snowflake connection function
def return_snowflake_conn():
    """Establishes connection to Snowflake."""
    conn = snowflake.connector.connect(
        user=Variable.get('snowflake_userid'),
        password=Variable.get('snowflake_password'),
        account=Variable.get('snowflake_account'),
        warehouse='compute_wh',
        database='dev'
    )
    return conn.cursor()

@task
def extract(symbol):
    """Extracts the last 90 days of stock data from Alpha Vantage API for a given symbol."""
    api_key = Variable.get('vantage_api_key')
    url_template = Variable.get("vantage_api_url")

    url = url_template.format(symbol=symbol, vantage_api_key=api_key)
    response = requests.get(url)
    data = response.json()

    time_series = data.get('Time Series (Daily)', {})
    last_90_days_data = {
        date: values for date, values in time_series.items() if
        datetime.strptime(date, "%Y-%m-%d") >= datetime.now() - timedelta(days=90)
    }

    return {'symbol': symbol, 'data': last_90_days_data}

@task
def transform(stock_data):
    """Transforms the raw API data into a structured format."""
    symbol = stock_data['symbol']
    data = stock_data['data']
    transformed_results = []

    for date, values in data.items():
        transformed = {
            'symbol': symbol,
            'date': date,
            'open': values['1. open'],
            'close': values['4. close'],
            'min': values['3. low'],
            'max': values['2. high'],
            'volume': values['5. volume']
        }
        transformed_results.append(transformed)

    return transformed_results

@task
def load(*transformed_data_lists):
    """Loads the transformed stock data into Snowflake."""
    target_table = "dev.raw_data.stock_prices"
    all_data = []

    # Combine all transformed data lists
    for transformed_data in transformed_data_lists:
        all_data.extend(transformed_data)

    if not all_data:
        print("No data to load.")
        return

    try:
        cur = return_snowflake_conn()
        cur.execute("BEGIN;")

        cur.execute(f"""
        CREATE OR REPLACE TABLE {target_table} (
            symbol VARCHAR,
            date DATE,
            open NUMBER,
            close NUMBER,
            min NUMBER,
            max NUMBER,
            volume NUMBER
        )
        """)

        insert_sql = f"""
        INSERT INTO {target_table} (symbol, date, open, close, min, max, volume)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        """

        for record in all_data:
            cur.execute(insert_sql, (
                record['symbol'],
                record['date'],
                record['open'],
                record['close'],
                record['min'],
                record['max'],
                record['volume']
            ))

        cur.execute("COMMIT;")
        print("Data loaded successfully.")

    except Exception as e:
        cur.execute("ROLLBACK;")
        print(f"An error occurred while loading data: {str(e)}")
        raise e
    finally:
        cur.close()

@task
def create_forecast_table():
    """Creates a forecast table in Snowflake."""
    create_table_query = """
    CREATE OR REPLACE TABLE dev.raw_data.forecast_data (
        symbol VARCHAR,
        forecast_date DATE,
        predicted_open NUMBER,
        predicted_close NUMBER,
        predicted_min NUMBER,
        predicted_max NUMBER,
        predicted_volume NUMBER
    );
    """
    try:
        cur = return_snowflake_conn()
        cur.execute(create_table_query)
        print("Forecast table created successfully.")
    except Exception as e:
        print(f"Error creating forecast table: {str(e)}")
        raise e
    finally:
        cur.close()

@task
def create_forecasting_model():
    """Creates a forecasting model in Snowflake."""
    create_model_query = """
    CREATE OR REPLACE MODEL stock_price_forecasting
    AS
    SELECT
        symbol,
        date,
        close AS target,
        open, high, low, volume
    FROM
        dev.raw_data.stock_prices
    WHERE
        date < CURRENT_DATE();  -- Use historical data only
    """
    try:
        cur = return_snowflake_conn()
        cur.execute(create_model_query)
        print("Forecasting model created successfully.")
    except Exception as e:
        print(f"Error creating forecasting model: {str(e)}")
        raise e
    finally:
        cur.close()

@task
def insert_forecast_data():
    """Inserts forecast data into Snowflake."""
    insert_data_query = """
    INSERT INTO dev.raw_data.forecast_data (symbol, forecast_date, predicted_open, predicted_close, predicted_min, predicted_max, predicted_volume)
    SELECT
        symbol,
        DATEADD(day, seq4(), CURRENT_DATE()) AS forecast_date,
        forecast.open AS predicted_open,
        forecast.close AS predicted_close,
        forecast.low AS predicted_min,
        forecast.high AS predicted_max,
        forecast.volume AS predicted_volume
    FROM
        TABLE(FORECAST(
            MODEL => 'stock_price_forecasting',
            TIME_COLUMN => 'date',
            TARGET_COLUMN => 'close',
            MAX_FORECAST_DAYS => 7
        ));
    """
    try:
        cur = return_snowflake_conn()
        cur.execute(insert_data_query)
        print("Forecast data inserted successfully.")
    except Exception as e:
        print(f"Error inserting forecast data: {str(e)}")
        raise e
    finally:
        cur.close()

# Define the DAG
with DAG(
    dag_id='Airflow_dag4',
    start_date=datetime(2024, 10, 10),
    catchup=False,
    tags=['ETL', 'stock', 'pipeline'],
    schedule_interval='@daily'
) as dag:

    # Task pipeline for Apple (AAPL)
    apple_data = extract('AAPL')
    transformed_apple_data = transform(apple_data)

    # Task pipeline for Google (GOOG)
    google_data = extract('GOOG')
    transformed_google_data = transform(google_data)

    # Load transformed data from both symbols into Snowflake
    load_task = load(transformed_apple_data, transformed_google_data)

    # Create the forecast table
    create_table_task = create_forecast_table()

    # Create the forecasting model
    create_model_task = create_forecasting_model()

    # Insert forecast data
    insert_data_task = insert_forecast_data()

    # Set task dependencies
    load_task >> create_table_task >> create_model_task >> insert_data_task