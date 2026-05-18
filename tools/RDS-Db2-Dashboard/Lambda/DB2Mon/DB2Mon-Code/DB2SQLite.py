import ibm_db_dbi
import sqlite3
import pandas as pd
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
import datetime
from decimal import Decimal, getcontext
import logging
import warnings
from CWSQLite import _validate_table_name

# pandas emits a UserWarning every time read_sql* is called with a non-SQLAlchemy
# DBAPI connection (ibm_db_dbi). Suppress just that specific warning — behavior
# is unaffected and the message floods Lambda logs.
warnings.filterwarnings(
    "ignore",
    message="pandas only supports SQLAlchemy connectable.*",
    category=UserWarning,
)

class DB2SQLiteHandler:
    def __init__(self, sqlite_file,secret_data,instancetype,boto3_clients,debugMode=False):
        self.debugMode=debugMode
        self.logger = logging.getLogger()
        self.logger.setLevel(logging.DEBUG) if self.debugMode else self.logger.setLevel(logging.INFO)
        
        self.sqlite_file = sqlite_file
        self.db2_conn = None
        self.sqlite_conn = None
        self.secret_data = secret_data
        self.boto3_clients = boto3_clients
        self.instance_type = instancetype
        self.db2_config = {
            'database': self.secret_data['database'],
            'hostname': self.secret_data['host'],
            'port': self.secret_data['port'],
            'uid': self.secret_data['username'],
            'pwd': self.secret_data['password'],
            'ssl': str(self.secret_data.get('ssl', 'false')).lower() == 'true',
            'sslCertLocation': self.secret_data.get('sslCertLocation', ''),
            'char':'utf-8'
        }
        
        if(self.instance_type.lower() == 'ec2'):
            self.InstanceIdentifierColumn = {"InstanceID" : self.get_instance_id(privateIPAddress=self.secret_data['host'])}
        elif(self.instance_type.lower() == 'rds'):
            self.InstanceIdentifierColumn = {"InstanceID" : self.secret_data['dbInstanceIdentifier']}
        self.InstanceIdentifierColumn=list(self.InstanceIdentifierColumn.items())
        self.select_query="select {0} from {1} where 1=1"
        self.insert_query="INSERT INTO {0} VALUES ({1})'"
        self.get_column_value_query_with_function = "SELECT {0} as {1} FROM {2}"
        self.get_column_value_query_without_function = "SELECT {0} FROM {1}"

        self.sqlite_master_table_query="SELECT name FROM sqlite_master WHERE type='table'"

    def get_instance_info(self,): 
        response = self.boto3_clients['rds'].describe_db_instances(DBInstanceIdentifier=self.InstanceIdentifierColumn[0][1])
        db_instance = response['DBInstances'][0]
        self.instance_info['InstanceClass'] = db_instance['DBInstanceClass']
        self.instance_info['AllocatedStorage'] = db_instance['AllocatedStorage']
        if 'Iops' in db_instance:
            self.instance_info['IOPS'] = db_instance['Iops']

        if 'Throughput' in db_instance:
            self.instance_info['Throughput'] = db_instance['Throughput']

        return self.instance_info
        
    def get_instance_id(self,privateIPAddress):
        response = self.boto3_clients['ec2'].describe_instances(
            Filters=[
                {
                    'Name': 'private-ip-address',
                    'Values': [
                        privateIPAddress,
                    ]
                },
            ]
        )
        return response['Reservations'][0]['Instances'][0]['InstanceId']
        
    def connect_db2(self):
        ssl_enabled = self.db2_config['ssl']
        cert_location = self.db2_config['sslCertLocation']

        if ssl_enabled and not cert_location:
            self.logger.warning("ssl=true but sslCertLocation is empty — falling back to TCP")
            ssl_enabled = False

        if ssl_enabled:
            cert_path = cert_location
            if cert_path.startswith('s3://'):
                cert_path = self._download_cert_from_s3(cert_path)
            conn_str = (f"DATABASE={self.db2_config['database']};"
                        f"HOSTNAME={self.db2_config['hostname']};"
                        f"PORT={self.db2_config['port']};"
                        f"PROTOCOL=TCPIP;"
                        f"UID={self.db2_config['uid']};"
                        f"PWD={self.db2_config['pwd']};"
                        f"Security=SSL;"
                        f"SSLServerCertificate={cert_path};")
            self.logger.info("Connecting to DB2 using SSL")
        else:
            conn_str = (f"DATABASE={self.db2_config['database']};"
                        f"HOSTNAME={self.db2_config['hostname']};"
                        f"PORT={self.db2_config['port']};"
                        f"PROTOCOL=TCPIP;"
                        f"UID={self.db2_config['uid']};"
                        f"PWD={self.db2_config['pwd']};")
            self.logger.info("Connecting to DB2 using TCP")
        self.db2_conn = ibm_db_dbi.connect(conn_str, "", "")

    def _download_cert_from_s3(self, s3_uri):
        """Download PEM cert from S3 to /tmp and return local path."""
        # s3_uri format: s3://bucket/key
        parts = s3_uri[5:].split('/', 1)
        bucket, key = parts[0], parts[1]
        local_path = f"/tmp/{key.split('/')[-1]}"
        self.logger.info(f"Downloading SSL cert from {s3_uri} to {local_path}")
        self.boto3_clients['s3'].download_file(bucket, key, local_path)
        return local_path
    def connect_sqlite(self):
        self.sqlite_conn = sqlite3.connect(self.sqlite_file)
        def julian_day(input):
            if input is None:
                return None
            else:
                return int(datetime.datetime.strptime(input,'%Y-%m-%d %H:%M:%S.%f').strftime('%j'))
        def hour(input):
            if input is None:
                return None
            else:
                return int(datetime.datetime.strptime(input,'%Y-%m-%d %H:%M:%S.%f').strftime('%H'))
        def minute(input):
            if input is None:
                return None
            else:
                return int(datetime.datetime.strptime(input,'%Y-%m-%d %H:%M:%S.%f').strftime('%M'))
        def second(input):
            if input is None:
                return None
            else:
                return int(datetime.datetime.strptime(input,'%Y-%m-%d %H:%M:%S.%f').strftime('%S'))
        def dec(f, precision, scale):
            if f is None:
                return None
            else:
                getcontext().prec = precision
                getcontext().rounding = 'ROUND_HALF_UP'
                d = Decimal(str(f)).quantize(Decimal(str(10 ** -scale)))
                return float(d)
        def integer(input):
            if input is None:
                return None
            else:
                return int(input)
        def smallint(input):
            if input is None:
                return None
            else:
                return int(input)
        def bigint(input):
            if input is None:
                return None
            else:
                return int(input)
        def floating(input):
            if input is None:
                return None
            else:
                return float(input)           
        def character(input):
            if input is None:
                return None
            else:
                return chr(input)      
        self.sqlite_conn.create_function("julian_day", 1, julian_day)
        self.sqlite_conn.create_function("hour", 1, hour)
        self.sqlite_conn.create_function("minute", 1, minute)
        self.sqlite_conn.create_function("second", 1, second)
        self.sqlite_conn.create_function("decimal", 3, dec)
        self.sqlite_conn.create_function("float", 1, floating)
        self.sqlite_conn.create_function("integer", 1, integer)
        self.sqlite_conn.create_function("smallint", 1, smallint)
        self.sqlite_conn.create_function("bigint", 1, bigint)
        self.sqlite_conn.create_function("double", 1, floating)
        self.sqlite_conn.create_function("chr", 1, character)

    def create_table(self, table_name, columns):
        column_str = ', '.join([f'{col} {columns[col]}' for col in columns])
        query = f'CREATE TABLE {table_name} ({column_str})'
        self.execute_sqlite_query(query)

    def insert_record(self, table_name, record):
        values_str = ', '.join(['?' for _ in range(len(record))])
        query=self.insert_query.format(table_name,values_str)
        self.execute_sqlite_query(query, record)

    def select_all_records(self, table_name):
        query = self.select_query("*",table_name)
        return self.execute_sqlite_query(query).fetchall()

    def select_all_records_df(self, table_name):
        query = self.select_query("*",table_name)
        return pd.read_sql(query, self.sqlite_conn)

    def insert_query_result(self, query):
        # find table name from SQL statement comment
        table_name = None
        match = re.search(r'^\s{0,}\/\*([0-9a-zA-Z_]+)\*\/', query, re.IGNORECASE)
        if match:
            table_name = match.group(1)
        result = pd.DataFrame()
        result = self.execute_db2_query(query)
        if 'EXECUTABLE_ID' in result.columns:
            result['EXECUTABLE_ID'] = result['EXECUTABLE_ID'].astype('object') 
        # write DataFrame to SQLite table
        if not self.sqlite_conn:
            self.connect_sqlite()
        result.to_sql(table_name, self.sqlite_conn, if_exists="append", index=False)
        self.logger.debug(f"Table: {table_name}")
        try:
            self.logger.debug(result.to_json(orient="records"))
        except Exception as e:
            self.logger.debug(f"Unable to print output of Table: {table_name}")
            self.logger.debug(e)
        return table_name

    def insert_sqlite_query_result(self, query):
        try:
            table_name = None
            match = re.search(r'^\s{0,}\/\*([0-9a-zA-Z_]+)\*\/', query, re.IGNORECASE)
            if match:
                table_name = match.group(1)
            if table_name is not None:
                result = pd.DataFrame()
                result = self.execute_sqlite_query(query)
                # write DataFrame to SQLite table
                if not self.sqlite_conn:
                    self.connect_sqlite() 
                self.logger.debug(f"Table: {table_name}")
                result.to_sql(table_name, self.sqlite_conn, if_exists='append', index=False)
                
                
                try:
                    self.logger.debug(result.to_json(orient="records"))
                except Exception as e:
                    self.logger.debug(f"Unable to print output of Table: {table_name}",e)
            
            return table_name
        except sqlite3.OperationalError as e:
            self.logger.error(query)
            self.logger.error(e)
            self.logger.error(e.args[0])
            self.logger.error(e.args[1]) 


    def execute_queries_in_parallel(self, queries, max_workers=5, max_chunksize=10):
        results = []
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = [executor.submit(self.insert_query_result, query) for query in queries]
            for i in range(0, len(futures), max_chunksize):
                chunk_futures = futures[i:i+max_chunksize]
                for future in as_completed(chunk_futures):
                    results.append(future.result())
        return results

        
    def execute_db2_query(self, query, params=None):
        if not self.db2_conn:
            self.connect_db2()
        return pd.read_sql(query, self.db2_conn)

    def execute_sqlite_query(self, query, params=None):
        if not self.sqlite_conn:
            self.connect_sqlite()
        return pd.read_sql(query,self.sqlite_conn)
    def run_query_on_sqlite(self, query):
            cursor = self.sqlite_conn.cursor()
            cursor.execute(query)
            rows = cursor.fetchall()
            return rows    
    def list_tables_and_columns(self):
        with self.sqlite_conn:
            cursor = self.sqlite_conn.cursor()
            cursor.execute(self.sqlite_master_table_query)
            tables = cursor.fetchall()
            for table in tables:
                validated = _validate_table_name(table[0])
                query=f"PRAGMA table_info('{validated.lower()}')"
                df = pd.read_sql_query(query, self.sqlite_conn)
                r, c = df.shape
                self.logger.debug(f'Table: {validated} Columns#: {r}')
                self.logger.debug(df.to_json(orient="records",))

    def list_tables(self):
        with self.sqlite_conn:
            df = pd.read_sql_query(self.sqlite_master_table_query, self.sqlite_conn)
            self.logger.debug(df.to_json(orient="records"))
                
    def find_table_columns_and_datatypes(self, table_name=None):
        with self.sqlite_conn:
            cursor = self.sqlite_conn.cursor()
            if table_name:
                validated = _validate_table_name(table_name[0])
                df = pd.read_sql_query(f"PRAGMA table_info('{validated.lower()}')", self.sqlite_conn)
                r, c = df.shape
                self.logger.debug(f'Table: {validated} Columns#: {r}')
                self.logger.debug(df.to_json(orient="records"))
            else:
                cursor.execute(self.sqlite_master_table_query)
                tables = cursor.fetchall()
                for table in tables:
                    validated = _validate_table_name(table[0])
                    df = pd.read_sql_query(f"PRAGMA table_info('{validated.lower()}')", self.sqlite_conn)
                    r, c = df.shape
                    self.logger.debug(f'Table: {validated} Columns#: {r}')
                    self.logger.debug(df.to_json(orient="records"))

    def get_column_value(self, table_name, column_name,function=None):
        if function:
            query = self.get_column_value_query_with_function.format(function,column_name,table_name)
        else:
            query = self.get_column_value_query_without_function(column_name,table_name)
        df = pd.read_sql_query(query, self.sqlite_conn)
        column_value = df[column_name][0]
        return column_value

    def execute_sqlite_ddl_dml(self, ddl):
        if not self.sqlite_conn:
            self.connect_sqlite()
        try:
            cursor = self.sqlite_conn.cursor()
            cursor.execute(ddl)
            self.sqlite_conn.commit()
        except sqlite3.Error as e:
            self.logger.error(f"Error executing DDL statement: {str(e)}",ddl)

    def __del__(self):
        if self.db2_conn:
            self.db2_conn.close()
        if self.sqlite_conn:
            self.sqlite_conn.close()
