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

    Db2 11.5 (default):
        REGION=us-east-1 ./db2-driver.sh

    Db2 12.1:
        DB2_VER=12.1 REGION=us-east-1 ./db2-driver.sh

    DB2_VER defaults to 11.5 if not set.
    On completion, the script prints the next command to run.

Step 3 — Configure DSN entries (run as db2inst1):

    sudo su - db2inst1
    REGION=us-east-1 source db2client-configure.sh

    Optional — target a specific instance:
        DB_INSTANCE_ID=my-db2-instance REGION=us-east-1 source db2client-configure.sh

    Optional — provide database names directly (required for Kerberos setups
    where RDSADMIN is not accessible to the AD user):
        DB_NAMES=DB2DB,MYDB REGION=us-east-1 source db2client-configure.sh

    Optional — custom RDS API endpoint (e.g. PrivateLink, GovCloud, Site-B):
        E_URL="--endpoint-url https://<endpointURLAddress> --no-verify-ssl" \
        REGION=us-east-1 source db2client-configure.sh

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

    Db2 11.5 (default):
        ./db2client-airgap.sh --mode download --region <region>

    Db2 12.1:
        DB2_VER=12.1 ./db2client-airgap.sh --mode download --region <region>

    Downloads everything to ./db2client-artifacts/ including:
        scripts/  — functions.sh, db2client-configure.sh, db211.5.9-tools.zip
                    (or db212.1-tools.zip for Db2 12.1), jq
        drivers/  — v11.5.9_linuxx64_rtcl.tar  (or v12.1.4_linuxx64_rtcl.tar.gz)
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

    Db2 11.5 (default):
        ./db2-driver.sh

    Db2 12.1:
        DB2_VER=12.1 ./db2-driver.sh

    On completion, the script prints the next command to run.

Step 4 — Configure DSN entries (run as db2inst1):

    sudo su - db2inst1
    BUCKET=db2client-artifacts-<account>-<region> REGION=<region> source db2client-configure.sh

    With custom endpoint:
        E_URL="--endpoint-url https://<endpointURLAddress> --no-verify-ssl" \
        BUCKET=db2client-artifacts-<account>-<region> REGION=<region> source db2client-configure.sh

    On completion, the script prints the connect commands and next steps.

Step 5 — Activate helper functions in the current session:

    source ~/.bashrc
    db2_help


--------------------------------------------------------------------------------
  WHAT db2client-configure.sh CREATES
--------------------------------------------------------------------------------

DSN names created by db2client-configure.sh:

  Admin database (RDSADMIN):
    RDSAT    — TCP,  local auth (SERVER_ENCRYPT)
    RDSAS    — SSL,  local auth
    RDSAKS   — SSL,  Kerberos  (domain-joined hosts only)

  User databases (e.g. DB2DB):
    <DB>T    — TCP,  local auth       (e.g. DB2DBT)
    <DB>S    — SSL,  local auth       (e.g. DB2DBS)
    <DB>SK   — SSL,  Kerberos         (e.g. DB2DBSK, domain-joined only)

  Multi-instance: a numeric index is inserted before the type suffix,
  e.g. RDSAT0 / RDSAT1, DB2DB0T / DB2DB0S / DB2DB0SK

Which DSN types are written depends on the instance parameter group
and whether the host is domain-joined:

    db2comm = TCPIP           → RDSAT,  <DB>T
    db2comm = SSL             → RDSAS,  <DB>S
                                + RDSAKS, <DB>SK  (when domain-joined)
    db2comm = TCPIP,SSL       → all of the above
    db2comm not set           → treated as TCPIP

When db2comm = SSL, the configure script uses an SSL connection for the
internal RDSADMIN bootstrap query (to list user databases) instead of TCP.
This means no TCP DSN is needed even to discover database names.

Database name resolution order (first match wins):
  1. DB_NAMES env var        — set before running, e.g. DB_NAMES=DB2DB,MYDB
  2. DBName field on instance — returned by describe-db-instances (single DB)
  3. RDSADMIN bootstrap query — requires CONNECT privilege on RDSADMIN
  4. Interactive prompt       — if all above fail or return nothing

When Kerberos is active, the connecting AD user may not have CONNECT on
RDSADMIN (it is protected and not normally granted to AD users). In that
case the script falls through to the interactive prompt or the DB_NAMES
env var — see KERBEROS / DOMAIN-JOINED HOSTS for details.

On domain-joined hosts (Active Directory / Kerberos), SSL DSN entries
automatically include Kerberos authentication parameters — see the
KERBEROS / DOMAIN-JOINED HOSTS section below.

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

    db2 "connect to RDSAT  user admin using '$MASTER_USER_PASSWORD'"   # TCP local
    db2 "connect to RDSAS  user admin using '$MASTER_USER_PASSWORD'"   # SSL local
    db2 "connect to RDSAKS"                                             # SSL Kerberos
    db2 "connect to DB2DBT user admin using '$MASTER_USER_PASSWORD'"   # user DB TCP
    db2 "connect to DB2DBS user admin using '$MASTER_USER_PASSWORD'"   # user DB SSL
    db2 "connect to DB2DBSK"                                            # user DB Kerberos

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

    Commercial regions (us-east-1, us-west-2, eu-west-1, etc.):
        curl -sL https://truststore.pki.rds.amazonaws.com/<region>/<region>-bundle.pem \
             -o ~/<region>-bundle.pem

    GovCloud regions (us-gov-east-1, us-gov-west-1):
        curl -sL https://truststore.pki.<region>.rds.amazonaws.com/<region>/<region>-bundle.pem \
             -o ~/<region>-bundle.pem

    Airgap:
        aws s3 cp s3://<bucket>/ssl/<region>-bundle.pem ~/<region>-bundle.pem

Note: the truststore URL is partition-specific. GovCloud uses a region-scoped
hostname. db2client-configure.sh and db2-driver.sh both resolve this
automatically — the manual curl above is for reference only.

To verify SSL is working:

    db2_test_connection RDSDBSSL


--------------------------------------------------------------------------------
  KERBEROS / DOMAIN-JOINED HOSTS
--------------------------------------------------------------------------------

When the EC2 instance is joined to an Active Directory domain (via realmd /
sssd), db2client-configure.sh automatically detects this and adds Kerberos
authentication parameters to every SSL DSN entry:

    Authentication=KERBEROS
    KRBPlugin=IBMkrb5

The resulting db2dsdriver.cfg entry looks like:

    <dsn alias="RDSDBSSL" host="<endpoint>" name="RDSADMIN" port="50443">
      <parameter name="Authentication"         value="KERBEROS"/>
      <parameter name="KRBPlugin"              value="IBMkrb5"/>
      <parameter name="SSLServerCertificate"   value="/home/db2inst1/<region>-bundle.pem"/>
      <parameter name="SecurityTransportMode"  value="SSL"/>
      <parameter name="TLSVersion"             value="TLSV12"/>
    </dsn>

Detection method (first match wins):
  1. 'realm list' shows "configured: kerberos-member"  — realmd + sssd (AL2/AL2023)
  2. /etc/krb5.conf contains default_realm             — any kerberos setup

Important — RDS for Db2 limitation:
  When Kerberos is enabled on the RDS instance, local user authentication is
  NOT supported. This means even the internal RDSADMIN bootstrap query (used
  to discover database names) requires a valid Kerberos ticket.

  db2client-configure.sh enforces this by checking for a TGT immediately
  after detecting a domain-joined host. If no ticket is found, the script
  exits with a clear message rather than proceeding and producing incomplete
  DSN entries:

      [ERROR] No Kerberos ticket found in the cache. Obtain one first:
      [ERROR]   kinit user@COMPANY.COM
      [ERROR]   klist
      [ERROR]   REGION=us-east-1 source db2client-configure.sh

  RDSADMIN access and database discovery:
  RDSADMIN is a protected system database. AD users are not granted CONNECT
  on it by default, so the RDSADMIN bootstrap query that discovers user
  database names will fail silently for Kerberos-authenticated users. The
  script handles this gracefully — it falls back to an interactive prompt.

  To provide database names non-interactively (recommended for Kerberos setups):

      DB_NAMES=DB2DB,MYDB REGION=us-east-1 source db2client-configure.sh

  Multiple databases:

      DB_NAMES=DB2DB,APPDB,REPORTDB REGION=us-east-1 source db2client-configure.sh

  The RDSDBSSL admin DSN is always created regardless of whether user
  database names are discovered or provided.

Before connecting with Kerberos you must have a valid ticket:

    kinit user@COMPANY.COM
    klist                       # confirm ticket is present

Then connect — no user or password, Kerberos ticket is used automatically:

    db2 terminate
    db2 "connect to RDSDBSSL"

For TCP DSNs (when db2comm includes TCPIP), standard password auth is used:

    db2 "connect to RDSADMIN user admin using '$MASTER_USER_PASSWORD'"

If the machine is not domain-joined, SSL DSNs use SERVER_ENCRYPT (standard
password authentication) and no Kerberos parameters are added.

Prerequisites on AL2023:
    sudo dnf install -y sssd realmd adcli oddjob oddjob-mkhomedir samba-common-tools
    sudo realm join <domain>    # requires domain admin credentials


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
           If db2comm=SSL, the instance does not listen on TCP at all —
           use the RDSDBSSL DSN instead of RDSADMIN.

  Problem: GSKit / SSL certificate error
  Fix:     Re-download the certificate using the correct URL for your partition:
               # Commercial:
               curl -sL https://truststore.pki.rds.amazonaws.com/<region>/<region>-bundle.pem \
                    -o ~/<region>-bundle.pem
               # GovCloud:
               curl -sL https://truststore.pki.<region>.rds.amazonaws.com/<region>/<region>-bundle.pem \
                    -o ~/<region>-bundle.pem
           Then re-run db2client-configure.sh

  Problem: SSL connect works, but db2client-configure.sh registered no user databases
  Fix:     This happens when db2comm=SSL — the old TCP bootstrap query failed
           silently. Re-run db2client-configure.sh with the updated script;
           it now detects db2comm and uses an SSL DSN for the bootstrap query.

  Problem: Kerberos connection fails — SQL30082N or GSKit error
  Fix:     1. Confirm a valid ticket:  klist
              If expired:              kinit user@REALM.COM
           2. Confirm the host is domain-joined:  realm list
           3. Confirm IBMkrb5 plugin is present:
                  ls $HOME/sqllib/security64/plugin/client/IBMkrb5.*
           4. Re-run db2client-configure.sh if the DSN was created before the
              host was joined to the domain (Kerberos params are added only
              when domain join is detected at configure time).

  Problem: db2client-configure.sh exits with "No Kerberos ticket found"
  Fix:     The host is domain-joined. RDS for Db2 does not support local user
           authentication when Kerberos is enabled — a TGT is required even
           for the internal RDSADMIN bootstrap query.
           Obtain a ticket then re-run:
               kinit user@REALM.COM
               klist                          # confirm ticket is present
               REGION=<region> source db2client-configure.sh

  Problem: db2icrt failed / sqllib not found
  Fix:     Re-run db2-driver.sh as root. The installer uses a clean
           environment (env -i) to avoid symbol conflicts.

  Problem: Wrong Db2 version installed
  Fix:     Set DB2_VER before running the installer:
               DB2_VER=12.1 REGION=us-east-1 ./db2-driver.sh
           Valid values: 11.5 (default), 12.1

  Run full diagnostics:
      db2_test_connection
      db2_test_connection RDSDBSSL


================================================================================
  END OF MANUAL
================================================================================
