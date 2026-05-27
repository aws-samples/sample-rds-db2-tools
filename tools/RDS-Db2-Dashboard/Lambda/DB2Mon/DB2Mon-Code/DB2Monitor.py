import json
import DB2SQLite as d
import SQLReader as s
import SecretManager as sm
import warnings
import time
import CWSQLite

import logging
import boto3
import os
import sys
import traceback

warnings.filterwarnings("ignore", category=DeprecationWarning, module="ibm_db_dbi")

class DB2Monitor:

    @staticmethod
    def _parse_bool(value, default=False):
        """Safely parse a boolean-like value without using eval().
        Accepts actual bools, or strings like 'true'/'false'/'1'/'yes'.
        """
        if value is None:
            return default
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in ('true', '1', 'yes')

    def __init__(self, event, context=None):
        """To initialize the class

        Args:
            event (List): Pass arguments through Lambda payload
            context (_type_, optional): Not used currently. Defaults to None.
        """
        self.event = event
        self.context = context
        # Configure logging to write to standard output (console)
        logging.basicConfig(stream=sys.stdout, level=logging.INFO, 
                            format='%(asctime)s - %(levelname)s - %(message)s')
        self.logger = logging.getLogger()

        self.logger.info('Initializing Monitoring.')
        self.db2mon_init_sqlite = s.SQLFileReader('db2_init_sqlite.sql').generateStatement()
        self.db2_start_snap = s.SQLFileReader('db2_start_snap.sql').generateStatement()
        self.db2_end_snap = s.SQLFileReader('db2_end_snap.sql').generateStatement()
        self.db2_diff_snap = s.SQLFileReader('db2_diff_snap.sql').generateStatement()
        self.db2_post_sqlite = s.SQLFileReader('db2_post_sqlite.sql').generateStatement()
        self.db2_output = s.SQLFileReader('db2_output.sql').generateStatement()

        session = boto3.Session()
        required_boto3_client=['sns','logs','cloudwatch','secretsmanager','rds','ec2','s3']
        boto3_clients_endpoints = {'rds': 'https://rds-qa.amazon.com'}
        boto3_clients_endpoints = {}
        self.boto3_clients={}
        self.region = os.environ.get('AWS_DEFAULT_REGION') or os.environ.get('REGION') or session.region_name or "us-east-1"
        self.expectedPayloadKeys=[   "monitoringIntervalSeconds",
                        "monitorMetricGroup",
                        "monitorNonMetricGroup",
                        "skipColumns",
                        "cloudWatchBatchSize", 
                        "cloudWatchNamespace", 
                        "cloudWatchLogGroup",
                        "secretName",
                        "topicName", 
                        "monitoredInstanceType",
                        "publishToS3",
                        "publishToCW",
                        "bucketName",
                        "ExportGroupsList",
                        "debugMode"
                    ]
        self.expectedPayload={key: None for key in self.expectedPayloadKeys}
        
        for client in required_boto3_client:
            if boto3_clients_endpoints.get(client, None) is None:
                self.boto3_clients[client]=boto3.client(client,region_name=self.region)
            else:
                self.boto3_clients[client]=boto3.client(client,region_name=self.region,endpoint_url=boto3_clients_endpoints.get(client, None))
        self.WaitDurationSec = event['monitoringIntervalSeconds'] if event.get('monitoringIntervalSeconds') else 60
        self.metricTableMonitored = event['monitorMetricGroup'] if event.get('monitorMetricGroup') else [
                "BPL_WRITE","BPL_HITRA","BPL_STATS","BPL_SIZES",
                "BPL_HITRA","BPL_READS","BPL_RDSYNC","BPL_RDASYNC",
                "BPL_WRASYNC","BPL_WRSYNC","DB_TIMEB","DB_WAITT",
                "DB_TIMEB","DB_PROCT","DB_SORT","DB_SYSRE",
                "DB_LOGWR","DB_LOGRE","DB_LOGST","DB_DLCKS",
                "DB_MEMST","DB_MEMPL","DB_CLACT","DB_SIZE",
                "DB_THRUP","TSP_DSKIO","TSP_DSKIOSYNC","TSP_DSKIOASYNC",
                "TSP_SIZE","TSP_USAGE","TSP_PRFST","CON_WAITT",
                "CON_STATS","CON_PAGRW","LTC_WAITT"
        ]
        self.initskipColumns = event['skipColumns'] if event.get('skipColumns') else ["TABLE_NAME","HOST_NAME",
                "DATABASE_NAME","DB_NAME","DBNAME", "OS", "OS_VER", "OS_REL"
        ]
        self.BatchSize = event['cloudWatchBatchSize'] if event.get('cloudWatchBatchSize') else 1000
        self.CWNamespace = event['cloudWatchNamespace'] if event.get('cloudWatchNamespace') else "DB2-CUSTOM-MONITOR"
        self.secretName = event['secretName'] if event.get('secretName') else ""
        self.monitoredInstanceType = event['monitoredInstanceType'] if event.get('monitoredInstanceType') else "ec2"
        self.nonmetricTableMonitored = event['monitorNonMetricGroup'] if event.get('monitorNonMetricGroup') else ['SQL_TOPEXECT',
                'SQL_TOPSORT','SQL_TOPROWS','SQL_TOPIOSTA','SQL_TOPWAITW','SQL_TOPWAITT','SQL_TOPEXECP'
        ]
        self.cloudWatchLogGroupPrefix = event['cloudWatchLogGroupPrefix'] if event.get('cloudWatchLogGroupPrefix') else "DB2MonLG"
            
        self.allTablesMonitored=self.metricTableMonitored + self.nonmetricTableMonitored  
        self.ExportGroupsList = event['ExportGroupsList'] if event.get('ExportGroupsList') else self.allTablesMonitored
        self.debugMode = self._parse_bool(event.get('debugMode'))
        self.topicName = event.get('topicName') or event.get('topicArn', '').split(':')[-1] or "NOT_DEFINED"
        self.TopicArn = None  # resolved lazily on first error
        self.ExecutionLogStream = os.environ.get('AWS_LAMBDA_LOG_STREAM_NAME', "PLACEHOLDER")
        
        # Initialize the SecretManager and fetch secrets
        self.logger.info(f"Fetching secret: name={self.secretName} region={self.region}")
        self.secretManager = sm.SecretManager(self.logger, self.secretName, self.boto3_clients['secretsmanager'])
        self.secret_data = self.secretManager.get_secret()
        # publishToS3, publishToCW and BucketName can be defined in payload or in secret manager
        # The values in payload will override the values defined in secret manager
        # Extract non-sensitive config flags from secret separately to avoid taint-flow
        # from the secret dict into log statements (CodeQL CWE-312 / CWE-359).
        secret_publish_cw  = self.secret_data.get('publishToCW')
        secret_publish_s3  = self.secret_data.get('publishToS3')
        secret_bucket_name = self.secret_data.get('bucketName') or ""
        self.publishToCW = self._parse_bool(secret_publish_cw)
        self.publishToS3 = self._parse_bool(secret_publish_s3)
        self.BucketName  = secret_bucket_name
        self.publishToS3 = self._parse_bool(event.get('publishToS3'), self.publishToS3)
        self.publishToCW = self._parse_bool(event.get('publishToCW'), self.publishToCW)
        self.BucketName  = event['bucketName'] if event.get('bucketName') else self.BucketName
        self.s3KeyPrefix = event['s3KeyPrefix'] if event.get('s3KeyPrefix') else "tables/db2"

        # Log only non-sensitive config flags; bucket name at debug level only
        publish_to_cw = bool(self.publishToCW)
        publish_to_s3 = bool(self.publishToS3)
        self.logger.info(f"publishToCW={publish_to_cw} publishToS3={publish_to_s3}")
        self.logger.debug(f"bucketName={self.BucketName}")
            
    def get_topic_arn(self, sns, topic_name):
        """Get Topic ARN to send SNS message

        Args:
            sns (boto3 sns): Pass sns client
            topic_name (String): Pass the topic name to get the ARN

        Returns:
            String: Returns topic ARN
        """
        
        response = sns.list_topics()
        topics = response['Topics']
        matching_topics = [t for t in topics if t['TopicArn'].split(':')[-1] == topic_name]

        if len(matching_topics) == 1:
            topic_arn = matching_topics[0]['TopicArn']
            self.logger.info(f"SNS Topic Name = {topic_name} ARN = {topic_arn}")
            return topic_arn
        raise RuntimeError(f"SNS topic '{topic_name}' {'not found' if len(matching_topics) == 0 else 'matched multiple topics'}")

    def run(self):
        
        TopicArn=None
        ExecutionLogStream=None
        try:
            
            self.expectedPayload.update({key: self.event[key] for key in self.expectedPayloadKeys if key in self.event})
            self.logger.setLevel(logging.DEBUG) if self.debugMode else self.logger.setLevel(logging.INFO)
            self.logger.debug("Event Parameters passed to function:")
            self.logger.debug({k: v for k, v in self.event.items() if k != 'password'})
            self.logger.debug("Event Parameters processed by Lambda function:")
            safe_secret = {k: v for k, v in self.secret_data.items()
                           if k in ('database', 'dbInstanceIdentifier', 'tag', 'engine', 'engineVersion',
                                    'instanceType', 'multiAZ', 'storageType', 'ssl')}
            self.logger.debug(f"""WaitDurationSec={self.WaitDurationSec},metricTableMonitored={self.metricTableMonitored},
                              nonmetricTableMonitored={self.nonmetricTableMonitored},initskipColumns={self.initskipColumns},
                              BatchSize={self.BatchSize},CWNamespace={self.CWNamespace},secret_data={safe_secret},
                              region={self.region},monitoredInstanceType={self.monitoredInstanceType},
                              TopicArn={TopicArn},ExecutionLogStream={ExecutionLogStream},
                              cloudWatchLogGroupPrefix={self.cloudWatchLogGroupPrefix},
                              publishToCW={self.publishToCW},publishToS3={self.publishToS3},
                              ExportGroupsList={self.ExportGroupsList}""")
            self.logger.debug("Setting additional field")
            self.logger.debug("Initializing SQLite In-memory database.")
            sqlite_file = ':memory:'
            handler = d.DB2SQLiteHandler(
                                            sqlite_file,
                                            secret_data=self.secret_data,
                                            instancetype=self.monitoredInstanceType,
                                            boto3_clients=self.boto3_clients,
                                            debugMode=self.debugMode
                                        )  
            self.logger.debug("Creating tables in  SQLite In-memory database")
            for statement in self.db2mon_init_sqlite:
                _ =handler.execute_sqlite_ddl_dml(statement)
            
            self.logger.debug("List of In-memory Tables from Db2 Mon Lambda SQLite Database:")
            handler.list_tables() if self.debugMode else None
            
            self.logger.debug("In-memory Tables details from Db2 Mon Lambda SQLite Database:")
            handler.find_table_columns_and_datatypes(table_name=None) if self.debugMode else None
            
            self.logger.info("Collecting metrics from Database.")
            self.logger.debug("Creating Start Snap.")
            for statement in self.db2_start_snap:
                _ =handler.insert_query_result(statement)    
                
            self.logger.debug(f'Sleep for {self.WaitDurationSec}(s)')
            time.sleep(int(self.WaitDurationSec))
            
            self.logger.debug("Creating End Snap.")
            for statement in self.db2_end_snap:
                _ =handler.insert_query_result(statement)   
            
            self.logger.debug("Creating Delta from Start and End Snap.")
            for statement in self.db2_diff_snap:
                _ =handler.insert_sqlite_query_result(statement)
            
            #   Update null Timestamps in Delta
            self.logger.debug('Update null values Timestamps in Delta')
            for statement in self.db2_post_sqlite:
                _ =handler.execute_sqlite_ddl_dml(statement)
            
            #   Create Reports from Delta
            self.logger.debug('Create Reports from Delta')
            for statement in self.db2_output:
                _ =handler.insert_sqlite_query_result(statement)
                
            #   Capture Monitoring End Time
            self.logger.debug('Capturing Monitoring End Time.')
            montoring_end_time=handler.get_column_value('MONITOR_END_TIME','TS','strftime("%s", datetime(MONITOR_END_TIME, "utc"))')
            self.logger.debug(montoring_end_time)

            sqlite_to_cws3 = CWSQLite.CWS3Uploader(dbconn=handler.sqlite_conn, 
                secret_data=self.secret_data,
                metric_table_names=self.metricTableMonitored,
                non_metric_table_names=self.nonmetricTableMonitored,
                skip_columns=self.initskipColumns, 
                cloudwatch_batch_size=self.BatchSize,
                namespace=self.CWNamespace,
                montoring_end_time=montoring_end_time,
                boto3_clients=self.boto3_clients,
                InstanceIdentifierColumn=handler.InstanceIdentifierColumn,
                cloudWatchLogGroupPrefix=self.cloudWatchLogGroupPrefix,
                publishToCW=self.publishToCW,
                publishToS3=self.publishToS3,
                s3KeyPrefix=self.s3KeyPrefix,
                bucketName=self.BucketName,                
                debugMode=self.debugMode
            )
            if self.publishToCW: sqlite_to_cws3.extract_and_upload() 
            if self.publishToS3: sqlite_to_cws3.extract_and_upload_to_s3()
            del sqlite_to_cws3             
            self.logger.info('Processing Completed.')
            del handler
            return {
                'statusCode': 200,
                'body': json.dumps('Processing Completed.')
            }        
        except Exception as e:
            tb_msg = traceback.format_exc()
            self.logger.critical(tb_msg)
            try:
                if self.TopicArn is None:
                    self.TopicArn = self.get_topic_arn(self.boto3_clients['sns'], self.topicName)
                # Send only exception type + message via SNS — full traceback stays in CloudWatch Logs
                self.boto3_clients['sns'].publish(
                    TopicArn=self.TopicArn,
                    Message=(
                        f"Error in Db2 Monitoring Execution.\n"
                        f"Exception: {type(e).__name__}: {e}\n"
                        f"Full details: CloudWatch LogStream: {ExecutionLogStream}"
                    )
                )
            except Exception:
                self.logger.error('Failed to publish SNS notification')
            return {
                'statusCode': 500,
                'body': e
            }

