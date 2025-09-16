#!/usr/bin/env bash
#

# Retrieves the master user password for a specified DB instance.
# 
# This function attempts to obtain the master user password for the provided
# DB instance ID. It first checks if the password can be retrieved from the 
# AWS Secrets Manager. If a valid secret is not found, it prompts the user 
# to manually enter the password.
# 
# Args:
#     DB_INSTANCE_ID (str): The database instance identifier.
# 
# Environment Variables:
#     REGION: The AWS region where the DB instance is located.
# 
# Exports:
#     MASTER_USER_PASSWORD: The retrieved or entered master user password.
# 
# Returns:
#     int: Returns 1 if the password retrieval fails, otherwise 0.

get_master_password() {
  DB_INSTANCE_ID=$1
  SECRET_ARN=$(aws rds describe-db-instances \
  --db-instance-identifier "$DB_INSTANCE_ID" \
  --region $REGION \
  --query "DBInstances[0].MasterUserSecret.SecretArn" \
  --output text)
  if [[ -z "$SECRET_ARN" || "$SECRET_ARN" == "None" ]]; then
    read -rsp "Enter Master User password: " MASTER_USER_PASSWORD
    echo
  else
    SECRET_JSON=$(aws secretsmanager get-secret-value \
      --secret-id "$SECRET_ARN" \
      --query "SecretString" \
      --region $REGION \
      --output text)
    MASTER_USER_PASSWORD=$(jq -r '.password' <<< "$SECRET_JSON") 
    if [[ -z "$MASTER_USER_PASSWORD" ]]; then
      echo "Failed to get password from secret manager '$SECRET_ARN'. Exiting..."
      return 1
    fi
    export MASTER_USER_PASSWORD=$MASTER_USER_PASSWORD
  fi
}

# Retrieves the master user name for a specified DB instance.
#
# This function queries AWS RDS to obtain the master user name for the provided
# DB instance identifier. If the master user name is not found, it returns an
# error message.
#
# Environment Variables:
#     DB_INSTANCE_IDENTIFIER: The database instance identifier.
#     REGION: The AWS region where the DB instance is located.
#
# Exports:
#     MASTER_USER_NAME: The retrieved master user name.
#
# Returns:
#     int: Returns 1 if the master user name is not found, otherwise 0.

get_master_user_name() {
  local master_user_name=($(aws rds describe-db-instances \
    --db-instance-identifier "$DB_INSTANCE_IDENTIFIER" \
    --region $REGION \
    --query "DBInstances[0].MasterUsername" \
    --output text))

  if [ "$master_user_name" = "None" ]; then
    echo "Not found"
    return 1
  else
    export MASTER_USER_NAME=$master_user_name
  fi
}

# Retrieves the database address for a specified DB instance.
#
# This function queries AWS RDS to obtain the database endpoint address for the
# provided DB instance identifier. If the address is not found, it returns an
# error message.
#
# Environment Variables:
#     DB_INSTANCE_IDENTIFIER: The database instance identifier.
#     REGION: The AWS region where the DB instance is located.
#
# Exports:
#     DB_ADDRESS: The retrieved database endpoint address.
#
# Returns:
#     int: Returns 1 if the database address is not found, otherwise 0.
get_db_address() {
  local db_address=($(aws rds describe-db-instances \
    --db-instance-identifier "$DB_INSTANCE_IDENTIFIER" \
    --region $REGION \
    --query "DBInstances[0].Endpoint.Address" \
    --output text))

  if [ -z "$db_address" ]; then
    echo "Not found"
    return 1
  else
    export DB_ADDRESS=$db_address
  fi
}

# Retrieves the SSL port number for a specified DB instance.
#
# This function queries AWS RDS to obtain the parameter group name associated
# with the provided DB instance identifier, and then queries the parameter
# group to obtain the SSL port number. If the SSL port is not found, it returns
# an error message.
#
# Environment Variables:
#     DB_INSTANCE_IDENTIFIER: The database instance identifier.
#     REGION: The AWS region where the DB instance is located.
#
# Exports:
#     SSL_PORT: The retrieved SSL port number.
#
# Returns:
#     int: Returns 1 if the SSL port is not found, otherwise 0.
get_ssl_port() {
  SSL_PORT=""
  DB_PARAM_GROUP_NAME=$(aws rds describe-db-instances \
      --db-instance-identifier "$DB_INSTANCE_IDENTIFIER" \
      --region $REGION \
      --query "DBInstances[0].DBParameterGroups[0].DBParameterGroupName" \
      --output text)
  if [ "$DB_PARAM_GROUP_NAME" != "" ]; then
    SSL_PORT=$(aws rds describe-db-parameters \
        --db-parameter-group-name "$DB_PARAM_GROUP_NAME" \
        --region $REGION \
        --query "Parameters[?ParameterName=='ssl_svcename'].ParameterValue" \
        --output text)
    if [ "$SSL_PORT" = "None" ]; then
      SSL_PORT=""
      return 1
    fi
  fi
  export SSL_PORT=$SSL_PORT
  return 0
}

# Main entry point for the script.
#
# This function compiles a Java program, downloads the SSL certificate, retrieves
# the master user name, master password, database address, and SSL port from AWS
# RDS, and then runs the Java program with the retrieved parameters.
#
# Exports:
#     None
#
# Returns:
#     int: Returns 0 if the program runs successfully, otherwise 1.
main () {
  DB_INSTANCE_IDENTIFIER="viz-demo"
  CL_PATH=.:$HOME/sqllib/java/db2jcc4.jar
  REGION="us-east-1"
  PROG_NAME=Db2SSLTest
  JAVA_FILE=${PROG_NAME}.java
  DBNAME="TEST"

  if ! command -v javac &>/dev/null; then
    echo "javac is not installed. Please install Java Development Kit (JDK) to compile Java programs."
    exit 1
  fi

  echo "Compile Java program $JAVA_FILE"
  javac -cp $CL_PATH $JAVA_FILE

  echo "Downloading SSL certificate..."

  CERTCHAIN="/home/db2inst1/us-east-1-bundle.pem"

  if [ -f "$CERTCHAIN" ]; then
    echo "Certificate already exists. Skipping download."
  else
    echo "Certificate does not exist. Downloading..."
    if ! curl -sL "https://truststore.pki.rds.amazonaws.com/us-east-1/$REGION-bundle.pem" -o $REGION-bundle.pem; then
      echo "Failed to download SSL certificate. Please check your network connection or the URL."
      exit 1
    fi
  fi

  if get_master_user_name "$DB_INSTANCE_IDENTIFIER"; then
    echo "Master user name: $MASTER_USER_NAME"
    USER="$MASTER_USER_NAME"
  else
    echo "Failed to retrieve master user name. Exiting..."
    exit 1
  fi

  if get_master_password "$DB_INSTANCE_IDENTIFIER"; then
    PASSWORD=$MASTER_USER_PASSWORD
  else
    echo "Failed to retrieve master password. Exiting..."
    exit 1
  fi

  if get_db_address "$DB_INSTANCE_IDENTIFIER"; then
    echo "DB Address: $DB_ADDRESS"
    HOST="$DB_ADDRESS"
  else
    echo "Failed to retrieve DB address. Exiting..."
    exit 1
  fi

  if get_ssl_port "$DB_INSTANCE_IDENTIFIER"; then
    echo "SSL Port: $SSL_PORT"
    PORT="$SSL_PORT"
  else
    echo "Failed to retrieve SSL port. Exiting..."
    exit 1
  fi

  # Use -Djavax.net.debug=ssl:handshake:verbose to debug SSL issues
  echo "Running Java program..."
  java \
  -cp "$CL_PATH" $PROG_NAME $CERTCHAIN $HOST $PORT $DBNAME $USER $PASSWORD
}

main "$@"

