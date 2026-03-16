================================================================================
  RDS DB2 Client — Quick Reference Manual
  For use after db2-driver.sh installation
================================================================================

This file is your offline reference. It lives in ~/README.txt on the db2inst1
home directory and is downloaded automatically during installation.


--------------------------------------------------------------------------------
  INSTALLATION SUMMARY — ONLINE MODE
  (EC2 or CloudShell with internet access)
--------------------------------------------------------------------------------

Step 1 — Download scripts (run on any machine with internet):

    curl -sL https://bit.ly/getdb2driver | bash

    This downloads two files to the current directory:
        ./db2-driver.sh          — RT client installer
        ./db2client-airgap.sh    — airgap bundle tool (for offline deployments)

Step 2 — Install the RT client (run as root or ec2-user on the target machine):

    REGION=us-east-1 ./db2-driver.sh

    On completion, the script prints the next command to run.

Step 3 — Configure DSN entries (run as db2inst1):

    sudo su - db2inst1
    REGION=us-east-1 source db2client-configure.sh

    Optional — target a specific instance:
        DB_INSTANCE_ID=my-db2-instance REGION=us-east-1 source db2client-configure.sh

    On completion, the script prints the connect commands and next steps.

Step 4 — Activate helper functions in the current session:

    source ~/.bashrc
    db2_help

    'source ~/functions.sh' is added automatically to ~/.bashrc during configure.
    After that, functions are available on every new login without any manual step.


--------------------------------------------------------------------------------
  INSTALLATION SUMMARY — AIRGAP MODE
  (private subnet, no internet — artifacts served from S3)
--------------------------------------------------------------------------------

Step 1 — On any machine WITH internet, download all artifacts:

    curl -sL https://bit.ly/getdb2driver | bash
    ./db2client-airgap.sh --mode download --region <region>

    Downloads everything to ./db2client-artifacts/ including:
        scripts/  — functions.sh, db2client-configure.sh, db2exfmt, db2advis, jq
        drivers/  — v11.5.9_linuxx64_rtcl.tar
        ssl/      — <region>-bundle.pem

Step 2 — On a machine WITH AWS configured, upload to S3:

    ./db2client-airgap.sh --mode upload --region <region>

    This creates (or reuses) a bucket named:
        db2client-artifacts-<account-id>-<region>
    Uploads all artifacts and both scripts to the bucket.
    Verifies every file after upload.
    Prints the exact commands to run on the target machine.

    NOTE: If AWS credentials are already exported in the environment
    (AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY), those are used as-is.
    This avoids picking up an assumed role from a cloud desktop.

Step 3 — On the target machine (private subnet, AWS configured):

    aws s3 cp s3://db2client-artifacts-<account>-<region>/db2-driver.sh . && chmod +x db2-driver.sh
    export BUCKET=db2client-artifacts-<account>-<region> REGION=<region>
    ./db2-driver.sh

    On completion, the script prints the next command to run.

Step 4 — Configure DSN entries (run as db2inst1):

    sudo su - db2inst1
    BUCKET=db2client-artifacts-<account>-<region> REGION=<region> source db2client-configure.sh

    On completion, the script prints the connect commands and next steps.

Step 5 — Activate helper functions in the current session:

    source ~/.bashrc
    db2_help


--------------------------------------------------------------------------------
  WHAT db2client-configure.sh CREATES
--------------------------------------------------------------------------------

DSN names created by db2client-configure.sh:

    RDSADMIN    — TCP connection to the RDSADMIN system database
    RDSDBSSL    — SSL connection to the RDSADMIN system database
    <DBNAME>    — TCP connection to each user database  (e.g. DB2DB)
    <DBNAMESSL> — SSL connection to each user database  (e.g. DB2DBS)

To switch to a different RDS DB2 instance, re-run configure:

    REGION=us-east-1 source db2client-configure.sh

    Then use db2_use to switch the active instance in your session:

    db2_use end-to-end-trust
    db2_use trp-test-by-ibm

Files written:
    ~/sqllib/cfg/db2dsdriver.cfg   — DSN configuration
    ~/.db2env                      — active instance credentials (chmod 600)
    ~/.db2instances                — instance registry, no passwords (chmod 600)
    ~/CONN_HELP_README.txt         — ready-to-run connect commands
    ~/<region>-bundle.pem          — RDS SSL certificate


--------------------------------------------------------------------------------
  CONNECTING TO THE DATABASE
--------------------------------------------------------------------------------

After configure, run 'db2 terminate' to clear the DSN cache, then connect:

    db2 terminate
    cat ~/CONN_HELP_README.txt

General form:

    db2 "connect to <DSN> user <user> using '$MASTER_USER_PASSWORD'"

Examples:

    db2 "connect to RDSADMIN user admin using '$MASTER_USER_PASSWORD'"
    db2 "connect to DB2DB    user admin using '$MASTER_USER_PASSWORD'"
    db2 "connect to RDSDBSSL user admin using '$MASTER_USER_PASSWORD'"

Note: single quotes around $MASTER_USER_PASSWORD protect special characters
      (!, >, <, $) in the password. The outer double quotes let the shell
      expand the variable before passing it to db2.

Disconnect:

    db2 connect reset
    db2 terminate


--------------------------------------------------------------------------------
  HELPER FUNCTIONS  (source ~/functions.sh)
--------------------------------------------------------------------------------

  db2_help
      Print this function summary in the terminal.

      Example:
          db2_help


  db2_use [instance-id]
      Switch the active instance. Reads ~/.db2instances (written by configure),
      fetches a fresh password from Secrets Manager each time (handles rotation),
      falls back to ~/.need_password or interactive prompt, then rewrites
      ~/.db2env with the new active instance.

      To register a new instance, re-run db2client-configure.sh.

      No argument — shows a menu with the currently active instance marked.
      With argument — switches directly by instance identifier.

      Examples:
          db2_use
          db2_use end-to-end-trust
          db2_use trp-test-by-ibm

      Output:
          [SUCCESS] Active instance: end-to-end-trust | TCP: RDSADMIN | SSL: RDSDBSSL
          [   INFO] Connect: db2 "connect to RDSADMIN user admin using '$MASTER_USER_PASSWORD'"
          [   INFO] SSL:     db2 "connect to RDSDBSSL user admin using '$MASTER_USER_PASSWORD'"

      Password priority:
          1. AWS Secrets Manager  (automatic, handles rotation)
          2. ~/.need_password     (manual password file, see below)
          3. Interactive prompt


  db2_connect [DSN]
      Connect using credentials stored in ~/.db2env.
      DSN fallback order: argument → DB_DSN (TCP) → DB_SSL_DSN (SSL) → RDSADMIN.
      If the instance is SSL-only, DB_DSN will be empty and DB_SSL_DSN is used
      automatically — no argument needed.

      Examples:
          db2_connect
          db2_connect RDSDBSSL
          db2_connect DB2DB


  db2_disconnect
      Reset the current connection and terminate the db2 agent.

      Example:
          db2_disconnect


  db2_test_connection [DSN]
      Diagnose connection problems step by step:
        1. Checks DSN exists in db2dsdriver.cfg
        2. Tests TCP reachability to host:port
        3. Attempts db2 connect and diagnoses the error code

      Examples:
          db2_test_connection
          db2_test_connection RDSDBSSL

      Common errors diagnosed:
          SQL30082N  — wrong username or password
          SQL08001N  — database not found
          SQL01013N  — network/TCP error
          GSKit/SSL  — certificate problem


  db2_list_dsns
      List all DSNs currently configured in db2dsdriver.cfg.

      Example:
          db2_list_dsns


  db2_show_env
      Print the currently active instance, DSN, user, and whether a password
      is set (password value is never printed).

      Example:
          db2_show_env

      Output:
          REGION             : us-east-1
          DB_INSTANCE_ID     : end-to-end-trust
          DB_DSN             : RDSADMIN
          DB_SSL_DSN         : RDSDBSSL
          MASTER_USER_NAME   : admin
          MASTER_USER_PASSWORD: <set>


  db2_load_env
      Reload credentials from ~/.db2env into the current shell session.
      Useful if you opened a new terminal and ~/.bashrc does not source
      functions.sh automatically.

      Example:
          db2_load_env


  db2_save_env
      Save the current shell environment variables (REGION, DB_INSTANCE_ID,
      DB_DSN, MASTER_USER_NAME, MASTER_USER_PASSWORD) to ~/.db2env.

      Example:
          export DB_DSN=RDSDBSSL
          db2_save_env


  get_task_status
      Show the status of all RDS background tasks (backup, restore, upgrade,
      etc.) by querying rdsadmin.get_task_status(). Connects to RDSADMIN,
      runs the query, then disconnects.

      Example:
          get_task_status

      Output columns:
          TASK_TYPE   LIFECYCLE   CREATED_AT   COMPLETED_WORK_BYTES


  get_task_elapsed
      Show elapsed time in seconds for each RDS task.

      Example:
          get_task_elapsed

      Output columns:
          TASK_ID   TASK_TYPE   LIFECYCLE   ELAPSED_SECONDS


  get_task_output
      Show full details of the most recent RDS task — including input parameters
      and task output. Connects to RDSADMIN, runs the query, then disconnects.

      Example:
          get_task_output

      Output columns:
          TASK_TYPE   LIFECYCLE   CREATED_AT   COMPLETED_WORK_BYTES
          INPUT_PARAMS (up to 256 chars)   TASK_OUTPUT (up to 1024 chars)


  monitor_db_instance_creation
      Poll the RDS instance status every 30 seconds until it reaches
      "available". Useful after creating a new instance.
      Requires DB_INSTANCE_ID to be set (via db2_use or db2_set_instance).

      Example:
          db2_use my-new-instance
          monitor_db_instance_creation


--------------------------------------------------------------------------------
  MANUAL PASSWORD FILE  (~/.need_password)
--------------------------------------------------------------------------------

If your instance does NOT use AWS Secrets Manager for password management,
create this file before running db2client-configure.sh or db2_use:

    vi ~/.need_password

Format — one line per instance:

    <instance-identifier> <password>

Example:

    end-to-end-trust  MyP@ssw0rd!
    trp-test-by-ibm   An0therP@ss#

Permissions:

    chmod 600 ~/.need_password

Password lookup priority (both configure and db2_use):
    1. AWS Secrets Manager
    2. ~/.need_password
    3. Interactive prompt


--------------------------------------------------------------------------------
  MULTI-INSTANCE WORKFLOW
--------------------------------------------------------------------------------

Configure runs one instance at a time. To work with multiple instances:

    # Configure instance 1
    REGION=us-east-1 source db2client-configure.sh
    # Select: end-to-end-trust

    # Switch to instance 2 (re-run configure, select different instance)
    REGION=us-east-1 source db2client-configure.sh
    # Select: trp-test-by-ibm

    # Use db2_use to switch active credentials between already-configured instances
    db2_use end-to-end-trust
    db2 "connect to RDSADMIN user admin using '$MASTER_USER_PASSWORD'"
    db2 "select * from sysibm.sysdummy1"
    db2 connect reset

    db2_use trp-test-by-ibm
    db2 "connect to RDSADMIN user admin using '$MASTER_USER_PASSWORD'"
    db2 "select * from sysibm.sysdummy1"
    db2 connect reset

    # Check which instance is active
    db2_show_env


--------------------------------------------------------------------------------
  SSL CONNECTION NOTES
--------------------------------------------------------------------------------

SSL DSNs (RDSDBSSL) require:
  - SSL to be enabled on the RDS instance parameter group (ssl_svcename set)
  - The RDS SSL certificate bundle present at ~/<region>-bundle.pem

The certificate is downloaded automatically by db2client-configure.sh.
To re-download manually:

    Online:
        curl -sL https://truststore.pki.rds.amazonaws.com/us-east-1/us-east-1-bundle.pem \
             -o ~/us-east-1-bundle.pem

    Airgap:
        aws s3 cp s3://<bucket>/ssl/us-east-1-bundle.pem ~/us-east-1-bundle.pem

To verify SSL is working:

    db2_test_connection RDSDBSSL


--------------------------------------------------------------------------------
  FILES REFERENCE
--------------------------------------------------------------------------------

    ~/functions.sh                 Helper functions (source this on login)
    ~/db2client-configure.sh       DSN configuration script (re-run to refresh)
    ~/README.txt                   This file
    ~/CONN_HELP_README.txt         Ready-to-run connect commands (last configure run)
    ~/.db2env                      Active instance credentials  (chmod 600)
    ~/.db2instances                Instance registry, DSN mapping (chmod 600)
    ~/.need_password               Manual passwords if not using Secrets Manager
    ~/<region>-bundle.pem          RDS SSL certificate bundle
    ~/sqllib/cfg/db2dsdriver.cfg   DB2 DSN configuration file
    ~/sqllib/cfg/db2cli.ini        DB2 CLI configuration file


--------------------------------------------------------------------------------
  TROUBLESHOOTING
--------------------------------------------------------------------------------

  Problem: db2_use or db2_connect says "No instance registry found"
  Fix:     Re-run db2client-configure.sh — it writes ~/.db2instances

  Problem: SQL30082N — USERNAME AND/OR PASSWORD INVALID
  Fix:     Password may have rotated. Run:  db2_use <instance>
           This fetches a fresh password from Secrets Manager.

  Problem: SQL1531N — DSN not found
  Fix:     Run 'db2 terminate' to clear the cache, then retry.
           If still failing, re-run db2client-configure.sh to rebuild db2dsdriver.cfg

  Problem: Cannot reach host:port (TCP failure)
  Fix:     Check RDS security group allows inbound on port 50000 (TCP)
           and port 50443 (SSL) from this host/VPC.

  Problem: GSKit / SSL certificate error
  Fix:     Re-download the certificate:
               curl -sL https://truststore.pki.rds.amazonaws.com/<region>/<region>-bundle.pem \
                    -o ~/<region>-bundle.pem
           Then re-run db2client-configure.sh

  Problem: db2icrt failed / sqllib not found
  Fix:     Re-run db2-driver.sh as root. The installer uses a clean
           environment (env -i) to avoid symbol conflicts.

  Run full diagnostics:
      db2_test_connection
      db2_test_connection RDSDBSSL


================================================================================
  END OF MANUAL
================================================================================
