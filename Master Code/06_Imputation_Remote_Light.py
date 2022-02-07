# -*- coding: utf-8 -*-
"""
Created on Sat Dec 12 12:32:26 2020

@author: saulg
"""



import pandas as pd
import numpy as np
import utils_machine_learning
import warnings
from scipy.spatial.distance import cdist
from tqdm import tqdm

from sklearn.model_selection import train_test_split
from sklearn.preprocessing import MinMaxScaler, StandardScaler
from sklearn.metrics import r2_score
from sklearn.decomposition import PCA

from tensorflow.random import set_seed
from tensorflow.keras import callbacks
from tensorflow.keras.models import Sequential
from tensorflow.keras.layers import Dense, Dropout
from tensorflow.keras.optimizers import Adam
from tensorflow.keras.regularizers import L2
from tensorflow.keras.metrics import RootMeanSquaredError, mean_absolute_error

warnings.simplefilter(action='ignore')

np.random.seed(42)
set_seed(seed=42)

#Data Settings
aquifer_name = 'Escalante-Beryl, UT'
data_root =    './Datasets/'
figures_root = './Figures Imputed'
test_set = True
if test_set: val_split = 0.30
else: val_split = 0.30

###### Model Setup
imputation = utils_machine_learning.imputation(data_root, figures_root)

###### Measured Well Data
Original_Raw_Points = pd.read_hdf(data_root + '03_Original_Points.h5')
Well_Data = imputation.read_pickle('Well_Data', data_root)
PDSI_Data = imputation.read_pickle('PDSI_Data_EEMD', data_root)
GLDAS_Data = imputation.read_pickle('GLDAS_EEMD', data_root)

###### Getting Well Dates
Feature_Index = GLDAS_Data[list(GLDAS_Data.keys())[0]].index

###### Importing Metrics and Creating Error DataFrame
Summary_Metrics = pd.DataFrame(columns=['Train MSE','Train RMSE', 'Train MAE', 'Train Points',
                                        'Validation MSE','Validation RMSE', 'Validation MAE', 'Validation Points',
                                        'Test MSE','Test RMSE', 'Test MAE', 'Test Points', 'Test R2', 'R2'])
###### Feature importance Tracker
Feature_Importance = pd.DataFrame()
###### Creating Empty Imputed DataFrame
Imputed_Data = pd.DataFrame(index=Feature_Index)

loop = tqdm(total = len(Well_Data['Data'].columns), position = 0, leave = False)

for i, well in enumerate(Well_Data['Data']):
    try:
        ###### Get Well raw readings for single well
        Raw = Original_Raw_Points[well].fillna(limit=2, method='ffill')
        
        ###### Get Well readings for single well
        Well_set_original = pd.DataFrame(Well_Data['Data'][well], index = Feature_Index[:])
        well_scaler = MinMaxScaler()
        well_scaler.fit(Well_set_original)
        Well_set_temp = pd.DataFrame(well_scaler.transform(Well_set_original), index = Well_set_original.index, columns=([well]))
        ###### Create Test Set
        if test_set: Well_set_temp, y_test = imputation.test_range_split(df=Well_set_temp, 
            min_points = 1, Cut_left= 2000, gap_year=5, Random=True, max_tries = 15, max_gap = 12)
        
        ###### PDSI Selection
        (well_x, well_y) = Well_Data['Location'].loc[well]
        df_temp = pd.DataFrame(index=PDSI_Data['Location'].index, columns =(['Longitude', 'Latitude']))
        for j, cell in enumerate(PDSI_Data['Location'].index):
            df_temp.loc[cell] = PDSI_Data['Location'].loc[cell]
        pdsi_dist = pd.DataFrame(cdist(np.array(([well_x,well_y])).reshape((1,2)), df_temp, metric='euclidean'), columns=df_temp.index).T
        pdsi_key = pdsi_dist[0].idxmin()
        Feature_Data = PDSI_Data[pdsi_key][2:]
        feature_scaler_pdsi = StandardScaler()
        Feature_Data = pd.DataFrame(feature_scaler_pdsi.fit_transform(Feature_Data), index = Feature_Data.index, columns = Feature_Data.columns)
        
        
        ###### GLDAS Selection
        df_temp = pd.DataFrame(index=PDSI_Data['Location'].index, columns =(['Longitude', 'Latitude']))
        for j, cell in enumerate(GLDAS_Data['Location'].index):
            df_temp.loc[cell] = GLDAS_Data['Location'].loc[cell]
        gldas_dist = pd.DataFrame(cdist(np.array(([well_x,well_y])).reshape((1,2)), df_temp, metric='euclidean'), columns=df_temp.index).T
        gldas_key = gldas_dist[0].idxmin()
        
        feature_scaler = StandardScaler()
        components = 35
        fs = PCA(n_components=components)
        fs_data = GLDAS_Data[gldas_key]
        fs_data = pd.DataFrame(fs.fit_transform(feature_scaler.fit_transform(fs_data)), index = GLDAS_Data[gldas_key].index, columns = ['PCA '+ str(comp) for comp in range(components)])
        variance = np.cumsum(np.round(fs.explained_variance_ratio_, decimals=4)*100)
        Feature_Data = imputation.Data_Join(Feature_Data, fs_data).dropna()
                
        ###### Add Dumbies
        months = pd.get_dummies(Feature_Data.index.month_name())
        months.index = Feature_Data.index
        Feature_Data = imputation.Data_Join(Feature_Data, months)
        
        ###### Joining Features to Well Data
        Well_set = Well_set_temp.join(Feature_Data, how='outer')
        Well_set = Well_set[Well_set[Well_set.columns[1]].notnull()]
        Well_set_clean = Well_set.dropna()
        
        
        Y, X = imputation.Data_Split(Well_set_clean, well)
        x_train, x_val, y_train, y_val = train_test_split(X, Y, test_size = val_split, random_state = 42)


        ###### Model Initialization
        hidden_nodes = 50
        opt = Adam(learning_rate=0.001)
        model = Sequential()
        model.add(Dense(hidden_nodes, input_dim = X.shape[1], activation = 'relu', use_bias=True,
            kernel_initializer='glorot_uniform', kernel_regularizer= L2(l2=0.1))) #he_normal
        model.add(Dropout(rate=0.2))
        model.add(Dense(1))
        model.compile(optimizer = opt, loss='mse', metrics=['mse', RootMeanSquaredError(), mean_absolute_error])
        
        ###### Hyper Paramter Adjustments
        early_stopping = callbacks.EarlyStopping(monitor='val_loss', patience=5, min_delta=0.0, restore_best_weights=True)
        adaptive_lr = callbacks.ReduceLROnPlateau(monitor='val_loss', factor=0.20, min_lr=0)
        history = model.fit(x_train, y_train, epochs=700, validation_data = (x_val, y_val), verbose= 0, callbacks=[early_stopping, adaptive_lr])
        
        ###### Score and Tracking Metrics
        train_mse = model.evaluate(x_train, y_train)[1:]
        validation_mse = model.evaluate(x_val, y_val)[1:]
        train_points, val_points = [len(y_train)], [len(y_val)]
        df_metrics = pd.DataFrame(np.array([train_mse + train_points + validation_mse + val_points]).reshape((1,8)), 
                                  index=([str(well)]), columns=['Train MSE','Train RMSE', 'Train MAE', 'Train Points',
                                        'Validation MSE','Validation RMSE', 'Validation MAE', 'Validation Points'])
        Summary_Metrics = pd.concat(objs=[Summary_Metrics, df_metrics])

        ###### Model Prediction
        y_val_hat = model.predict(x_val)
        Prediction = pd.DataFrame(well_scaler.inverse_transform(model.predict(Feature_Data)), index=Feature_Data.index, columns = ['Prediction'])
        r2 = r2_score(well_scaler.inverse_transform(Well_set_temp.dropna()), well_scaler.inverse_transform(model.predict(X)))

        Summary_Metrics.loc[well,'R2'] = r2
        Gap_time_series = pd.DataFrame(Well_Data['Data'][well], index = Prediction.index)
        Filled_time_series = Gap_time_series[well].fillna(Prediction['Prediction'])
        Imputed_Data = pd.concat([Imputed_Data, Filled_time_series], join='inner', axis=1)
        
        ###### Model Plots
        imputation.Model_Training_Metrics_plot(history.history, str(well))
        imputation.Q_Q_plot(y_val_hat, y_val, str(well))
    
        ###### Inversing y train and y val for plotting
        y_train = pd.DataFrame(well_scaler.inverse_transform(y_train), index=y_train.index, 
                                  columns = ['Y Train']).sort_index(axis=0, ascending=True)
        y_val = pd.DataFrame(well_scaler.inverse_transform(y_val), index=y_val.index,
                                   columns = ['Y Val']).sort_index(axis=0, ascending=True)
        ###### More Plotting
        imputation.observeation_vs_prediction_plot(Prediction.index, Prediction['Prediction'], Well_set_original.index, Well_set_original, str(well), Summary_Metrics.loc[well], error_on = True)
        imputation.residual_plot(Prediction.index, Prediction['Prediction'], Well_set_original.index, Well_set_original, str(well))
        imputation.observeation_vs_imputation_plot(Imputed_Data.index, Imputed_Data[well], Well_set_original.index, Well_set_original, str(well))
        imputation.raw_observation_vs_prediction(Prediction, Raw, str(well), aquifer_name, Summary_Metrics.loc[well], error_on = True)
        imputation.raw_observation_vs_imputation(Filled_time_series, Raw, str(well), aquifer_name)
        imputation.observeation_vs_prediction_scatter_plot(Prediction['Prediction'], y_train, y_val, str(well), Summary_Metrics.loc[well], error_on = True)
        
        ###### Test Sets and Plots
        if test_set:
            try:
                data_test = pd.concat([y_test, Feature_Data], join='inner', axis=1).dropna()
                test_mse = model.evaluate(data_test.drop([well], axis=1), data_test[well])[1:]
                df_test_metrics = pd.DataFrame(np.array([test_mse]).reshape((1,3)),
                    index=([str(well)]), columns=['Test MSE','Test RMSE', 'Test MAE'])
                y_test = pd.DataFrame(well_scaler.inverse_transform(y_test), 
                        index=y_test.index, 
                        columns = ['Y Test']).sort_index(axis=0, ascending=True)
                Summary_Metrics.loc[well,['Test MSE','Test RMSE', 'Test MAE']] = df_test_metrics.loc[well]
                Summary_Metrics.loc[well, 'Test Points'] = len(y_test) 
                r2_test = r2_score(well_scaler.inverse_transform(model.predict(data_test.drop([well], axis=1))), 
                           well_scaler.inverse_transform(data_test[well].to_numpy().reshape(len(data_test[well]),1)))
                Summary_Metrics.loc[well,'Test R2'] = r2_test
                imputation.prediction_vs_test(Prediction['Prediction'], 
                    Well_set_original.drop(y_test.index, axis=0),  
                    y_test, str(well), Summary_Metrics.loc[well], error_on = True)
            except: 
                Summary_Metrics.loc[well,['Test MSE','Test RMSE', 'Test MAE']] = np.NAN
                Summary_Metrics.loc[well, 'Test Points'] = 0 
                Summary_Metrics.loc[well,'Test R2'] = np.NAN
                imputation.prediction_vs_test(Prediction['Prediction'], Well_set_original.drop(y_test.index, axis=0),y_test, str(well))

        loop.update(1)
        print('Next Well')
    except Exception as e:
        print(e)

loop.close()        
Well_Data['Data'] = Imputed_Data
Summary_Metrics.to_hdf(data_root  + '/' + '06_Metrics.h5', key='metrics', mode='w')
imputation.Save_Pickle(Well_Data, 'Well_Data_Imputed', data_root)
imputation.Aquifer_Plot(Well_Data['Data']) 