import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.preprocessing import FunctionTransformer
import xgboost as xgb
from xgboost import XGBRegressor

# Import the train and test sets
df_train = pd.read_parquet("../input/mdsb-2023/train.parquet")
df_test = pd.read_parquet("../input/mdsb-2023/final_test.parquet")

# Import external weather data
weather_data = pd.read_csv("../input/external-data/external_data.csv")

# Determine the target column name, implement X_train, y_train and X_test
_target_column_name = "log_bike_count"
y_train = df_train[_target_column_name]
X_train = df_train.drop(columns=[_target_column_name])
X_test = df_test

## A voir si au lieu de drop on peut pas juste laisser le select plus tard
X_train = X_train.drop(columns=[
    "counter_name", "site_name", "counter_technical_id", "coordinates", "counter_installation_date"
    , "bike_count"])
X_test = X_test.drop(columns=[
    "counter_name", "site_name", "counter_technical_id", "coordinates", "counter_installation_date"])

# Deal with the external weather data
weather_cols = ['date', 'rr3', 't']
weather_data = weather_data[weather_cols]

# Reshape the data index dealing with the dates
weather_data['date'] = pd.to_datetime(weather_data['date'], format='%Y-%m-%d %H:%M:%S')
weather_data = weather_data.drop_duplicates(subset='date')
new_index = pd.date_range(start=weather_data['date'].min(), end=weather_data['date'].max(), freq='H')
weather_data = weather_data.set_index('date').reindex(new_index).reset_index()

# Interpolate the rr3 and t columns
columns_to_interpolate = ['rr3', 't']
weather_data[columns_to_interpolate]= weather_data[columns_to_interpolate].interpolate(method='linear')
weather_data = weather_data.rename(columns={'index': 'date'})

# Merge the external data on the train and test sets
X_train = pd.merge(X_train, weather_data, how='left', on='date')
X_test = pd.merge(X_test, weather_data, how='left', on='date')

# Deal with the rain by encoding it
def encode_precipitation(value):
    if value < 2.5:
        return 0
    elif value >= 2.5:
        return 1
X_train['precipitations'] = X_train['rr3'].apply(encode_precipitation)
X_test['precipitations'] = X_test['rr3'].apply(encode_precipitation)

# Deal with temperatures by encoding them
def encode_temperature(value):
    if value < 5:
        return 0
    elif value >=5:
        return 1
X_train['temp'] = X_train['t'].apply(encode_temperature)
X_test['temp'] = X_test['t'].apply(encode_temperature)


# Create a column for holidays 
school_holidays = [
    ('2020-10-17', '2020-11-02'),  
    ('2020-12-19', '2021-01-04'),  
    ('2021-02-20', '2021-03-08'),  
    ('2021-04-10', '2021-04-26'), 
    ('2021-07-10', '2021-09-01'),  
    ('2021-10-23', '2021-11-08'),  
    ('2021-12-18', '2022-01-03'),  
]

for i, (start, end) in enumerate(school_holidays):
    school_holidays[i] = (pd.to_datetime(start), pd.to_datetime(end))

X_train['holidays'] = 0
X_test['holidays'] = 0

for start, end in school_holidays:
    X_train.loc[(X_train['date'] >= start) & (X_train['date'] <= end), 'holidays'] = 1
    X_test.loc[(X_test['date'] >= start) & (X_test['date'] <= end), 'holidays'] = 1

# Add a parameter based on COVID measures that were taken on that period
confinement_dates = pd.DataFrame({
    'start': ['2020-03-17', '2020-10-30', '2021-04-03'],
    'end': ['2020-05-11', '2020-12-15', '2021-05-03']
})

curfew_dates = pd.DataFrame({
    'start2': ['2020-10-17', '2020-12-15'],
    'end2': ['2020-12-15', '2021-06-01']
})

confinement_dates['start'] = pd.to_datetime(confinement_dates['start'])
confinement_dates['end'] = pd.to_datetime(confinement_dates['end'])

curfew_dates['start2'] = pd.to_datetime(curfew_dates['start2'])
curfew_dates['end2'] = pd.to_datetime(curfew_dates['end2'])

def add_covid_features(data, confinement_dates, curfew_dates):
    # Create a new column 'periode' initially set to 0
    data['periode'] = 0

    # Go through the confinement periods
    for _, row in confinement_dates.iterrows():
        data.loc[
            (data['date'] >= row['start']) & (data['date'] <= row['end']),
            'periode'
        ] = 2

    # Go through the curfew periods
    for _, row in curfew_dates.iterrows():
        if row['end2'] is not None:
            data.loc[
                (data['date'] >= row['start2']) & (data['date'] <= row['end2']) &
                (data['periode'] != 2), 
                'periode'
            ] = 1
        else:
            data.loc[
                (data['date'] >= row['start2']) &
                (data['periode'] != 2),  
                'periode'
            ] = 1

    # Check if a date is both in confinement and curfew and assign 2
    data['periode'] = data.groupby('date')['periode'].transform('max')

add_covid_features(X_train, confinement_dates, curfew_dates)
add_covid_features(X_test, confinement_dates, curfew_dates)

# Define the date encoder we want to use
def _encode_dates(X):
    X = X.copy()  # modify a copy of X
    # Encode the date information from the DateOfDeparture columns
    X.loc[:, "year"] = X["date"].dt.year
    X.loc[:, "month"] = X["date"].dt.month
    X.loc[:, "day"] = X["date"].dt.day
    X.loc[:, "weekday"] = X["date"].dt.weekday
    X.loc[:, "hour"] = X["date"].dt.hour

    # Finally we can drop the original columns from the dataframe
    return X.drop(columns=["date"])
date_encoder = FunctionTransformer(_encode_dates)

# Encode the dates
X_train = date_encoder.fit_transform(X_train)
X_test = date_encoder.fit_transform(X_test)

# Columns to be used in the model
selected_columns = ['counter_id', 'site_id', 'year', 'month', 'day', 'weekday', 'hour', 'vacances', 'periode', 'precipitations', 'temp']

X_train_selected = X_train[selected_columns]
X_test_selected = X_test[selected_columns]

X_train_selected['site_id'] = X_train_selected['site_id'].astype('category')
X_test_selected['site_id'] = X_test_selected['site_id'].astype('category')

X_train_selected.head()

# Create our regressor
regressor = XGBRegressor(learning_rate=0.2, n_estimators=900, enable_categorical=True)

regressor.fit(X_train_selected, y_train)

# Compute the predictions and shaping it into the good format
y_pred = regressor.predict(X_test_selected)
results = pd.DataFrame(
    dict(
        Id=np.arange(y_pred.shape[0]),
        log_bike_count=y_pred,
    )
)
results.to_csv("submission.csv", index=False)