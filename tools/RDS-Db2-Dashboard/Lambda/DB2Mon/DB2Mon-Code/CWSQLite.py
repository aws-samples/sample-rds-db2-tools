import sqlite3
import pandas as pd
import logging 
import json
from datetime import datetime, timezone

# Allowlist of valid monitoring table names.
# Table names used in SQL queries are validated against this set before use.
_VALID_TABLE_NAMES = frozenset([
    "BPL_WRITE","BPL_HITRA","BPL_STATS","BPL_SIZES","BPL_READS",
    "BPL_RDSYNC","BPL_RDASYNC","BPL_WRASYNC","BPL_WRSYNC",
    "DB_TIMEB","DB_WAITT","DB_PROCT","DB_SORT","DB_SYSRE",
    "DB_LOGWR","DB_LOGRE","DB_LOGST","DB_DLCKS","DB_MEMST",
    "DB_MEMPL","DB_CLACT","DB_SIZE","DB_THRUP",
    "TSP_DSKIO","TSP_DSKIOSYNC","TSP_DSKIOASYNC","TSP_SIZE",
    "TSP_USAGE","TSP_PRFST",
    "CON_WAITT","CON_STATS","CON_PAGRW","LTC_WAITT",
    "SQL_TOPEXECT","SQL_TOPSORT","SQL_TOPROWS","SQL_TOPIOSTA",
    "SQL_TOPWAITW","SQL_TOPWAITT","SQL_TOPEXECP",
])

def _validate_table_name(table_name: str) -> str:
    """Validate a table name against the monitoring allowlist.

    Raises ValueError if the name is not in the allowlist, preventing
    unsanitised strings from being interpolated into SQL queries.
    """
    normalised = table_name.strip().upper()
    if normalised not in _VALID_TABLE_NAMES:
        raise ValueError(f"Invalid table name rejected: {table_name!r}")
    return normalised

class CWS3Uploader:
    def __init__(self, 
                dbconn, 
                secret_data, 
                metric_table_names,
                non_metric_table_names, 
                skip_columns, 
                cloudwatch_batch_size,
                namespace,
                montoring_end_time,
                boto3_clients,
                InstanceIdentifierColumn,
                cloudWatchLogGroupPrefix,
                publishToCW,
                publishToS3,
                s3KeyPrefix,
                bucketName=None,                
                debugMode=False):
        """Initialize cloudwatch or S3 uploader class

        Args:
            dbconn (Any): Connection object
            secret_data (Dict): Contains data from secret manager
            metric_table_names (Any): List of tables for CW metrics tables
            non_metric_table_names (Any): List of non-cloud watch tables
            skip_columns (String): Columns to skip for metrics
            cloudwatch_batch_size (int): The max size is 1000 to minimize the CW API calls
            namespace (String): Namespace name for cloudwatch to send metrics
            montoring_end_time (Timestamp): When db2 monitoring ends
            boto3_clients (boto3 clients): List of boto3 clients for different services
            InstanceIdentifierColumn (String): The database identifier in case of RDS of instance-id for EC2
            cloudwatch_loggroup_prefix (Striung): Prefix for cloudwatch log group
            publishToCW (Boolean): To publish metrics to cloud watch or not
            publishToS3 (Boolena): To publish data to S3 or not
            bucketName (String, optional): Name of S3 bucker. Define it either in SM or in Lambda Payload. Defaults to None.
            debugMode (bool, optional): Turn debug on or off_. Defaults to False.
        """
        self.secret_data = secret_data
        self.dbconn = dbconn
        self.debugMode = debugMode
        self.logger = logging.getLogger()
        self.logger.setLevel(logging.DEBUG) if self.debugMode else self.logger.setLevel(logging.INFO)
        self.metric_table_names = metric_table_names
        self.non_metric_table_names = non_metric_table_names
        self.monitoring_end_time = montoring_end_time
        self.skip_columns = skip_columns
        self.cloudwatch_batch_size = cloudwatch_batch_size
        self.cloudWatchNamespace=namespace
        self.InstanceIdentifierColumn = InstanceIdentifierColumn
        self.database = secret_data['database']
        self.cloudWatchLogGroupPrefix = cloudWatchLogGroupPrefix
        self.cloudWatchLogGroup = cloudWatchLogGroupPrefix+"_"+str(self.InstanceIdentifierColumn[0][1])+"_"+str(self.database)
        self.cloudwatch_client = boto3_clients['cloudwatch']
        self.cloudwatchlog = boto3_clients['logs']
        self.s3client = boto3_clients['s3']
        self.default_timestamp_column='TS'
        self.cloudWatchMetrics=[]
        self.cloudWatchStreams={}
        self.s3Streams={}
        self._create_group(self.cloudWatchLogGroup)
        self.create_query = "SELECT {0} FROM {1} where 1=1 ORDER BY ROWID"
        self.bucketName = secret_data['bucketName'] if bucketName is None else bucketName
        self.publishToCW = publishToCW
        self.publishToS3 = publishToS3
        self.s3KeyPrefix = s3KeyPrefix
        tag = (secret_data.get('tag') or '').strip().upper()
        if tag:
            self.s3KeyPrefix = f"{self.s3KeyPrefix}/{tag}"
        self.dbInstanceIdentifier = secret_data['dbInstanceIdentifier'] if secret_data['dbInstanceIdentifier'] is not None else "dbInstanceIdentifier"
        if self.publishToCW == True and self.bucketName is None:
            self.logger.info("bucketName parameter is not defined either in the Lambda Payload or in the Secret Manager {self.secretName}")
            self.publishToCW = False

    def _log_stream_exists(self, log_group_name, log_stream_name):
        """Check if log stream exists or not

        Args:
            log_group_name (String): Name of the log group
            log_stream_name (String): Name of the log stream name

        Returns:
            Bool: If log stream exists or not
        """

        streamExists = False
        logStreamsInfo = self.cloudwatchlog.describe_log_streams(logGroupName=log_group_name,\
                                                            logStreamNamePrefix=log_stream_name)
        for lstream in logStreamsInfo['logStreams'] :
            if lstream['logStreamName'] == log_stream_name :
                streamExists = True

        return streamExists

    def _create_log_stream (self, group_name, stream_name) :
        """Create logstream in cloudwatch

        Args:
            group_name (String): Name of the group 
            stream_name (String): Name of the stream
        """

        if not self._log_stream_exists ( group_name, stream_name ) :
            response = self.cloudwatchlog.create_log_stream(logGroupName=group_name,logStreamName=stream_name)
            self.logger.debug( response )

    def _log_group_exists(self, log_group_name):
        """Check if log group already exists

        Args:
            log_group_name (String): Log group name

        Returns:
            Bool: Returns true if it exists
        """
        groupExists = False
        paginator = self.cloudwatchlog.get_paginator('describe_log_groups')
        for page in paginator.paginate():
            for group in page['logGroups']:
                if group['logGroupName'].lower() == log_group_name.lower():
                        groupExists = True
        return groupExists

    def _create_group (self,group_name) :
        """Create log group

        Args:
            group_name (String): Name of the group
        """

        if not self._log_group_exists ( group_name ) :
            response = self.cloudwatchlog.create_log_group(logGroupName=group_name)
            self.logger.debug( response )
                
    
    def upload_to_cloudwatch(self, metrics, streams):
        """Uploads cloudwatch metrics of Db2

        Args:
            metrics (Dict): Metrics in dict to send in a batch size
            streams (Dict): Log data to be put to cloudwatch log
        """
        self.logger.debug(f"Uploading Metrics in Batches of Batch Size: {self.cloudwatch_batch_size}")
        _cnt=0
        for i in range(0, len(metrics), self.cloudwatch_batch_size):
            _cnt=_cnt+1
            self.logger.debug(f"Processing Batch#: {_cnt}")
            batch = metrics[i:i+self.cloudwatch_batch_size]
            try:
                response = self.cloudwatch_client.put_metric_data(Namespace=self.cloudWatchNamespace, MetricData=tuple(batch))
                if int(response['ResponseMetadata']['HTTPStatusCode']) == 200:
                    self.logger.debug(f"Batch Processed and uploaded to CloudWatch Namespace:{self.cloudWatchNamespace}")
                else:
                    self.logger.error(f"Unable to upload Batch to CloudWatch Namespace:{self.cloudWatchNamespace} due to errors.")
            except Exception as e:
                self.logger.error(f"Unable to upload Batch to CloudWatch Namespace:{self.cloudWatchNamespace} due to errors.")
                self.logger.error(e)
        self.logger.info(f"Uploading Streams to CloudWatch Log Group: {self.cloudWatchLogGroup}") 
        for streamName, Events in streams.items():
            # Streams are created with lowercase names (see extract_and_upload) but the dict
            # can be keyed with mixed case. Normalize here so put_log_events targets the
            # actual stream that exists. Also include the real exception reason in the log.
            streamNameLower = streamName.lower()
            try:
                response = self.cloudwatchlog.put_log_events(
                    logGroupName=self.cloudWatchLogGroup,
                    logStreamName=streamNameLower,
                    logEvents=Events
                )
            except Exception as e:
                self.logger.error(
                    f"Unable to upload stream: {streamNameLower} to CloudWatch Log Group:"
                    f"{self.cloudWatchLogGroup} — {type(e).__name__}: {e}"
                )
                
    def upload_to_s3(self, s3Streams, bucketName):
        """Send Db2 mon and SQL table data to S3 for Athena and QuickInsight

        Args:
            s3Streams (Dict): Contains mon data
            bucketName (String): Bucket name to send data. Must be defined either in Lambda Payload or in Secret Manager
        """
        self.logger.debug(f"Uploading Metrics to S3: {bucketName}")
        ts_for_file = datetime.now(timezone.utc)
        
        for streamName, Events in s3Streams.items():
            s3Key = f"{self.s3KeyPrefix}/{streamName}/{self.dbInstanceIdentifier}_{streamName}_{ts_for_file.strftime('%Y%m%d_%H%M%S')}.json"                                            
            # Convert value to string if necessary
            if not isinstance(Events, str):
               value = "\n".join(Events)
               if len(Events) > 1:
                  self.logger.debug(f"s3Key={s3Key}")
            try:
                # Upload the content to S3
                response = self.s3client.put_object(Bucket=self.bucketName, Key=s3Key, Body=value)
            except Exception as e:
                self.logger.error(f"Unable to upload stream: {streamName} to S3 :{self.bucketName}/{s3Key} due to errors.")
                self.logger.error(str(e))
                    
    def get_tables(self):
        """Get list of table names from SQLite database.

        Returns:
            Dict: Returns metric and non-metric data
        """
        if self.dbconn is None:
            self.dbconn=sqlite3.connect(':memory:')
        metric_tables = []
        non_metric_tables = []
        cursor = self.dbconn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
        columns_list=cursor.fetchall()
        metric_tables = [row[0] for row in columns_list if row[0] in self.metric_table_names]
        non_metric_tables = [row[0] for row in columns_list if row[0] in self.non_metric_table_names]
        return metric_tables,non_metric_tables
    
    def extract_and_upload_to_s3(self):
        """Extract data from SQLite tables, write to payload table and upload to CloudWatch.
        """
        self.logger.info(f'Uploading monitoring data to S3')
        if self.bucketName is None and self.publishToS3:
            self.logger.info("publishToS3 is True but bucketName is not defined either in Lambda payload or in secret manager")
            return
        metric_tables, non_metric_tables = self.get_tables()

        for table_name in metric_tables:
            status_code,message = self.report_to_s3(table_name)
            if status_code == 200:
                self.logger.debug(message)
            else:
                self.logger.error(f"Errors while generating s3 stream for Table: {table_name}\n",message)
        for table_name in non_metric_tables:
            status_code,message = self.report_to_s3(table_name)
            if status_code == 200:
                self.logger.debug(message)
            else:
                self.logger.error(f"Errors while generating s3 stream for Table: {table_name}\n",message)

        self.upload_to_s3(self.s3Streams, self.bucketName)
        
    def extract_and_upload(self):
        """Extract data from SQLite tables, write to payload table and upload to CloudWatch.
        """
        if self.publishToCW:
           self.logger.info(f'Uploading Metrics Data to CloudWatch')
        else:
           return
        metric_tables, non_metric_tables = self.get_tables()
        for table_name in metric_tables:
            status_code,message = self.report_to_metrics(table_name)
            if status_code == 200:
                self.logger.debug(message)
            else:
                self.logger.error(f"Errors while generating metrics for Table: {table_name}\n",message)

        for table_name in non_metric_tables:
            self._create_log_stream(group_name=self.cloudWatchLogGroup,stream_name=table_name.lower())
            status_code,message = self.report_to_stream(table_name)
            if status_code == 200:
                self.logger.debug(message)
            else:
                self.logger.error(f"Errors while generating stream for Table: {table_name}\n",message)

        self.upload_to_cloudwatch(self.cloudWatchMetrics,self.cloudWatchStreams)
        
    def add_columns_from_dict(self, df, dimension_columns, kv_pairs):
        """Add additional fields to the data frame and to the dimension list

        Args:
            df (Panda Data Frame): Use Panda for storing and retrieving data
            dimension_columns (List): Contains lits of columns to be added to data frame
            kv_pairs (Dict): Key and values of the data to be added 

        Returns:
            Data Frame and List: Returns data frame and dimension list
        """
        for k, v in kv_pairs:
            df = df.assign(**{k: v})
            dimension_columns.append(k)
        return df,dimension_columns
        
    def add_columns_from_dict_for_s3(self, df, dimension_columns, kv_pairs):
        """Add data to the data frame and also to prepend additional fields to the dimension list

        Args:
            df (Data Frame): Panda data frame to hold data
            dimension_columns (List): Holds name of columns
            kv_pairs (Dict): Holds additional dimensions and their values to copy  to dataframe

        Returns:
            Data Frame and List: Returns data frame and dimension list
        """
        new_columns = []
        for k, v in kv_pairs:
            df = df.assign(**{k: v})
            # Add the key to the temporary list
            new_columns.append(k)
        # Prepend the new columns in the correct order to dimension_columns
        dimension_columns = new_columns + dimension_columns
        return df, dimension_columns
    
    def create_metrics_payload(self, df, dimension_columns, metric_columns, additional_dimensions):
        """Prepare data to be sent to cloudwatch metrics

        Args:
            df (Data Frame): Holds data 
            dimension_columns (List): Contains names of the dimension columns
            metric_columns (Dict): Contains cloudwatch metric data
            additional_dimensions (Dict): Additional dimensions mainly copied from Secret Manager for graphing

        Returns:
            Data Frame: Returns data frame that has all data
        """
        if not df.empty:
            df,dimension_columns=self.add_columns_from_dict(df,dimension_columns,additional_dimensions)
            dimensions_lambda = lambda x: [{'Name' : col, 'Value' : 'member_'+str(x[col])} if col == 'MEMBER' else {'Name' : col, 'Value' : x[col]} for col in dimension_columns if pd.notnull(x[col])]
            metrics_lambda = lambda x: [{"MetricName" : col, "Value" : x[col],"Unit" : "Count","Timestamp" : int(x[self.default_timestamp_column]), "Dimensions" : x['dimensions']} for col in metric_columns if pd.notnull(x[col])]
            df['dimensions'] = df.apply(dimensions_lambda, axis=1)
            df=df.drop(dimension_columns, axis=1)
            df['metrics']  = df.apply(metrics_lambda, axis=1)
            df=df.drop(metric_columns, axis=1)
            df=df.drop(['dimensions','TS'], axis=1)
            return df
        else:
            return pd.DataFrame()

    def create_stream_payload(self, df, stream_columns, additional_stream_columns):
        """Prepare cloudwatch log stream data for sending to cloudwatcg

        Args:
            df (Data Frame): Holds data
            stream_columns (Dict): Cloudwatch metrics data
            additional_stream_columns (Dict): Additional columns list

        Returns:
            Data Frame: Returns data frame containing data
        """
        if not df.empty:
            df,stream_columns=self.add_columns_from_dict(df,stream_columns,additional_stream_columns)
            stream_lambda = lambda x: [{"timestamp" : int(x[self.default_timestamp_column]) * 1000, "message": x[stream_columns].to_json()}]
            df['stream'] = df.apply(stream_lambda, axis=1)
            df=df['stream']
            return df
        else:
            return pd.DataFrame()

    def create_s3_payload(self, result, stream_columns, converted_list):
        """Prepare data to be sent to S3 for Athena tables

        Args:
            result (Json): Output in a josn format
            stream_columns (Dict): Db2 mon data
            converted_list (Dict): Additional data

        Returns:
            Json: Returns data in json format that Athena table understands
        """
        # Initialize an empty list to hold the final JSON objects
        json_output_list = []
        if not result.empty:
            # Get the current UTC timestamp in ISO 8601 format
            current_utc_timestamp = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
            # Iterate over each row in the DataFrame
            for index, row in result.iterrows():
                # Extract the stream_columns from the row and convert it to a dictionary
                stream_data = {col: row[col] for col in stream_columns}

                # Convert the list of tuples (converted_list) into a dictionary
                additional_data = dict(converted_list)

                # Combine the timestamp, stream data, and additional data into a single dictionary
                combined_data = {
                    "timestamp": current_utc_timestamp,  # Add the timestamp
                    **additional_data,                   # Add the additional fields
                    **stream_data                        # Add the data from stream_columns
                    
                }

                # Convert the combined data to JSON and add it to the list
                json_output_list.append(json.dumps(combined_data))
        return json_output_list

    def report_to_metrics(self, table_name):
        """Prepare dictionary containing data assembled from data frame and other dict for cloudwatch metrics

        Args:
            table_name (String): Name of the monitoring table

        Returns:
            Dict: Returns a dict of monitoring table data for cloudwatch metrics
        """
        table_name = _validate_table_name(table_name)
        cursor = self.dbconn.cursor()
        cursor.execute(f"PRAGMA table_info('{table_name}')")
        column_info = cursor.fetchall()
        metric_columns = []
        dimension_columns = []
        timestamp_columns = []
        for column in column_info:
            if ('INT' in column[2] or 'DOUBLE' in column[2] or 'FLOAT' in column[2] or 'REAL' in column[2] or 'NUMERIC' in column[2] or 'DECIMAL' in column[2] ) and column[1]!='MEMBER' and column[1] not in self.skip_columns:
                metric_columns.append(column[1]) 
            elif ('CHAR' in column[2] or 'TEXT' in column[2] or column[1]=='MEMBER') and column[1] not in self.skip_columns:
                dimension_columns.append(column[1])
            elif 'TIMESTAMP' in column[2] and column[1] == self.default_timestamp_column:
                timestamp_columns.append(column[1])
            if len(timestamp_columns) == 0:
                timestamp_columns.append(f"'{int(self.monitoring_end_time)}' as {self.default_timestamp_column}")
        cols = ','.join(metric_columns + dimension_columns+timestamp_columns)
        query = self.create_query.format(cols,table_name)
        try:
            metric_data=self.create_metrics_payload(pd.read_sql(query,self.dbconn),dimension_columns=dimension_columns,metric_columns=metric_columns,
                                                    additional_dimensions=[("TableName",table_name),
                                                                        self.InstanceIdentifierColumn[0],
                                                                        ("Database",self.database)
                                                                        ])
            if not metric_data.empty:
                metrics_list = metric_data.explode('metrics')['metrics'].tolist()
                self.cloudWatchMetrics=self.cloudWatchMetrics+metrics_list
                return 200,f"Successfully generated metrics for Table: {table_name}"
            else:
                return 200,f"No Data to Process for Table: {table_name}"
        except Exception as e:
            return 500,e
        
    def report_to_stream(self, Intablename):
        """Prepare dictionary containing data assembled from data frame and other dict for cloud watch logs

        Args:
            table_name (String): Name of the monitoring table

        Returns:
            Dict: Returns a dict of monitoring table data for cloudwatch logs
        """        
        table_name = _validate_table_name(Intablename)
        cursor = self.dbconn.cursor()
        query = f"PRAGMA table_info('{table_name.lower()}')"
        cursor.execute(query)
        column_info = cursor.fetchall()
        stream_columns = []
        timestamp_columns = []
        for column in column_info:
            if 'TIMESTAMP' in column[2] and column[1] == self.default_timestamp_column:
                timestamp_columns.append(column[1])
            else:
                stream_columns.append(column[1])
            if len(timestamp_columns) == 0:
                timestamp_columns.append(f"'{int(self.monitoring_end_time)}' as {self.default_timestamp_column}")
        cols = ','.join(stream_columns + timestamp_columns)
        query = self.create_query.format(cols, table_name.lower())
        try:
            stream_data=self.create_stream_payload(pd.read_sql(query,self.dbconn),stream_columns=stream_columns,
                                                    additional_stream_columns=[("tableName",table_name),
                                                                        self.InstanceIdentifierColumn[0],
                                                                        ("Database",self.database)
                                                                        ])
            if not stream_data.empty:
                self.cloudWatchStreams[table_name] = stream_data.explode('stream').tolist()
                return 200,f"Successfully generated metrics for Table: {table_name}"
            else:
                return 200,f"No Data to Process for Table: {table_name}"
        except Exception as e:
            return 500,e

    def report_to_s3(self, Intablename):
        """Prepare a dictionary of data to be sent to S3

        Args:
            Intablename (String): Db2 mon table name for which to prepare data

        Returns:
            Dict: Returns a dict having table name and the data
        """
        additional_fields = {}
        table_name = _validate_table_name(Intablename)
        table_name_lower = table_name.lower()
        additional_fields["tableName"] = table_name_lower

        # This is the data that must be in Secret Manager along with connection string data
        # This data is used as dimensions to draw graphs for comparison purposes
        desired_keys = ['tag', 'dbInstanceIdentifier', 'instanceType', 'database', 'multiAZ', 
                        'iops', 'allocatedSTorage', 'storageType', 'vCPU', 'Memory', 'az', 'Engine']
        
        # This is the data from secret manager that we do not want as dimension
        unwanted_keys = ['host', 'port', 'username', 'password', 
                         'currentSchema', 'vpcID', 'sgID', 'subnetGroupName',
                         'azSubnetID', 'engineVersion', 'bucketName'
                         'restoreDB', 'bucketPrefix', 'bkpTimestamp', 'workloadType'
                         'ops']
        
        if self.secret_data :
            # Check if the desired keys exist in the secret_data and populate additional_fields
            for key in desired_keys:
                if key in self.secret_data:
                    additional_fields[key] = self.secret_data[key]
            # Remove unwanted keys from additional_fields if they exist
            for key in unwanted_keys:
                additional_fields.pop(key, None)  # Use pop with default `None` to avoid KeyError
            # Convert all keys to uppercase
            additional_fields_upper = {key.upper(): value for key, value in additional_fields.items()}

        cursor = self.dbconn.cursor()
        query = f"PRAGMA table_info('{table_name_lower}')"
        cursor.execute(query)
        column_info = cursor.fetchall()
        stream_columns = []
        timestamp_columns = []
        for column in column_info:
            if 'TIMESTAMP' in column[2] and column[1] == self.default_timestamp_column:
                timestamp_columns.append(column[1])
            else:
                stream_columns.append(column[1])
            if len(timestamp_columns) == 0:
                timestamp_columns.append(f"'{int(self.monitoring_end_time)}' as {self.default_timestamp_column}")
        cols = ','.join(stream_columns + timestamp_columns)
        query = self.create_query.format(cols, table_name_lower)
        try:
            result = pd.read_sql(query,self.dbconn)
            self.logger.debug(f"result={result}")
            self.logger.debug(f"stream_columns={stream_columns}")
            self.logger.debug(f"additional_fields_upper={additional_fields_upper}")
            converted_list = [(key, value) for key, value in additional_fields_upper.items()]
            stream_data=self.create_s3_payload(result, stream_columns, converted_list)
            if stream_data is not None and len(stream_data) > 0:
                self.s3Streams[table_name_lower] = stream_data
                return 200,f"Successfully generated metrics for Table: {table_name_lower}"
            else:
                return 200,f"No Data to Process for Table: {table_name_lower}"
        except Exception as e:
            return 500,e