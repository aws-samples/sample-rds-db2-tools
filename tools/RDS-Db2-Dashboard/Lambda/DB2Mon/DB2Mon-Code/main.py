import DB2Monitor as db2mon


def main(event, context):
    """This is the main entry point

    Args:
        event (List): Describe the payload for the Lambda
    """
    monitor = db2mon.DB2Monitor(event, context)
    monitor.run()
 
if __name__ == "__main__":
    publishToS3 = True
    publishToCW = False
    if publishToCW:
        # Sample event for publishToCW
        event = { 
            "monitoringIntervalSeconds": 6, 
            "cloudWatchNamespace": "RDS-DB2-MON-S3", 
            "cloudWatchLogGroupPrefix":"DB2MonLG", 
            "secretName": "SM-database-1-QA1", 
            "topicName": "DB2Mon-Failed-Executions-database-1-dashboard-QA1", 
            "monitoredInstanceType": "rds",
            "s3KeyPrefix": "tables/db2",
            "debugMode": "False",
            "publishToS3": "False",
            "publishToCW": "True"
        }
    if publishToS3:
        # Sample event for publishToS3
        event = { 
            "monitoringIntervalSeconds": 6, 
            "secretName": "SM-database-1-QA1", 
            "topicName": "DB2Mon-Failed-Executions-database-1-dashboard-QA1", 
            "monitoredInstanceType": "rds",
            "s3KeyPrefix": "tables/db2",
            "debugMode": "False",
            "publishToS3": "True",
            "publishToCW": "False"
        }
    main(event, None)