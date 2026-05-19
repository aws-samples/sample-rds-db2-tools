terraform {
  required_version = ">= 1.0"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
    null = {
      source  = "hashicorp/null"
      version = "~> 3.0"
    }
  }
}

# Health check script that runs locally
resource "null_resource" "health_check" {
  triggers = {
    always_run = timestamp()
  }

  provisioner "local-exec" {
    command = <<-EOT
      #!/bin/bash
      set -e
      
      echo "=========================================="
      echo "RDS DB2 Proxy Health Check"
      echo "=========================================="
      echo ""
      
      # Get infrastructure outputs
      cd ../2-infrastructure
      EC2_ID=$(terraform output -raw ec2_instance_id 2>/dev/null || echo "")
      NLB_ARN=$(terraform output -raw nlb_arn 2>/dev/null || echo "")
      NLB_DNS=$(terraform output -raw nlb_dns_name 2>/dev/null || echo "")
      
      if [ -z "$EC2_ID" ] || [ -z "$NLB_ARN" ]; then
        echo "❌ ERROR: Infrastructure not deployed"
        exit 1
      fi
      
      echo "✓ Infrastructure outputs found"
      echo "  EC2 Instance: $EC2_ID"
      echo "  NLB ARN: $NLB_ARN"

      echo ""
      # Check EC2 instance state
      echo "Checking EC2 instance state..."      
      EC2_STATE=$(aws ec2 describe-instances --instance-ids "$EC2_ID" --query 'Reservations[0].Instances[0].State.Name' --output text)
      if [ "$EC2_STATE" = "running" ]; then
        echo "✓ EC2 instance is running"
      else
        echo "❌ EC2 instance state: $EC2_STATE"
        exit 1
      fi
      echo ""
      
      # Check SSM connectivity
      echo "Checking SSM connectivity..."
      if aws ssm describe-instance-information --instance-information-filter-list key=InstanceIds,valueSet="$EC2_ID" --query 'InstanceInformationList[0].PingStatus' --output text | grep -q "Online"; then
        echo "✓ SSM agent online"
      else
        echo "❌ SSM agent offline"
        exit 1
      fi
      echo ""

      # Check SSM parameter
      DOMAIN_NAME=$(cd ../1-prerequisites && terraform output -raw domain_name 2>/dev/null || echo "")
      if [ -z "$DOMAIN_NAME" ]; then
        echo "❌ ERROR: Could not get domain name from prerequisites"
        exit 1
      fi
      
      echo "Checking SSM parameter /rds/proxy/mappings/$DOMAIN_NAME..."
      MAPPING=$(aws ssm get-parameter --name /rds/proxy/mappings/$DOMAIN_NAME --query Parameter.Value --output text 2>/dev/null || echo "{}")
      MAPPING_COUNT=$(echo "$MAPPING" | jq 'length' 2>/dev/null || echo "0")
      if [ "$MAPPING_COUNT" -gt 0 ]; then
        echo "✓ RDS mappings: $MAPPING_COUNT entries"
        echo "$MAPPING" | jq -r 'to_entries[] | "  - \(.key) -> \(.value)"'
      else
        echo "⚠ No RDS mappings"
      fi
      echo ""
      
      # Check certificates
      echo "Checking certificates..."
      CERT_CMD=$(aws ssm send-command --instance-ids "$EC2_ID" --document-name "AWS-RunShellScript" --parameters 'commands=["ls /etc/openresty/certs/proxy-*.pem 2>/dev/null | wc -l"]' --query 'Command.CommandId' --output text)
      sleep 3
      CERT_COUNT=$(aws ssm get-command-invocation --command-id "$CERT_CMD" --instance-id "$EC2_ID" --query 'StandardOutputContent' --output text | tr -d '[:space:]')
      if [ "$CERT_COUNT" -ge 2 ]; then
        echo "✓ Certificates are present"
      else
        echo "❌ Certificates are missing"
      fi
      echo ""
      
      # Check OpenResty service
      echo "Checking OpenResty service..."      
      OPENRESTY_CMD=$(aws ssm send-command --instance-ids "$EC2_ID" --document-name "AWS-RunShellScript" --parameters 'commands=["systemctl is-active openresty"]' --query 'Command.CommandId' --output text)
      sleep 3
      OPENRESTY_RESULT=$(aws ssm get-command-invocation --command-id "$OPENRESTY_CMD" --instance-id "$EC2_ID" --query 'StandardOutputContent' --output text)
      if echo "$OPENRESTY_RESULT" | grep -q "active"; then
        echo "✓ OpenResty is active"
      else
        echo "❌ OpenResty not active: $OPENRESTY_RESULT"
        exit 1
      fi
      echo ""
      
      # Check nginx config
      echo "Checking nginx configuration..."
      NGINX_CMD=$(aws ssm send-command --instance-ids "$EC2_ID" --document-name "AWS-RunShellScript" --parameters 'commands=["openresty -t -c /etc/openresty/proxy.conf 2>&1"]' --query 'Command.CommandId' --output text)
      sleep 3
      NGINX_TEST=$(aws ssm get-command-invocation --command-id "$NGINX_CMD" --instance-id "$EC2_ID" --query 'StandardOutputContent' --output text)
      if echo "$NGINX_TEST" | grep -q "syntax is ok"; then
        echo "✓ Nginx config valid"
      else
        echo "❌ Nginx config errors"
      fi
      echo ""
      
      # Check cron job
      echo "Checking cron job..."
      CRON_CMD=$(aws ssm send-command --instance-ids "$EC2_ID" --document-name "AWS-RunShellScript" --parameters 'commands=["test -f /etc/cron.d/nginx-update && echo yes || echo no"]' --query 'Command.CommandId' --output text)
      sleep 3
      CRON_EXISTS=$(aws ssm get-command-invocation --command-id "$CRON_CMD" --instance-id "$EC2_ID" --query 'StandardOutputContent' --output text | tr -d '[:space:]')
      if [ "$CRON_EXISTS" = "yes" ]; then
        echo "✓ Cron job configured"
      else
        echo "❌ Cron job missing"
      fi
      echo ""

      # Check listening ports against target group ports
      echo "Checking listening ports..."
      TG_PORTS=$(aws elbv2 describe-target-groups --load-balancer-arn "$NLB_ARN" --query 'TargetGroups[*].Port' --output text)
      
      if [ -n "$TG_PORTS" ]; then
        for PORT in $TG_PORTS; do
          PORT_CMD=$(aws ssm send-command --instance-ids "$EC2_ID" --document-name "AWS-RunShellScript" --parameters "commands=[\"netstat -tlnp | grep -E ':$PORT ' | wc -l\"]" --query 'Command.CommandId' --output text)
          sleep 2
          PORT_COUNT=$(aws ssm get-command-invocation --command-id "$PORT_CMD" --instance-id "$EC2_ID" --query 'StandardOutputContent' --output text | tr -d '[:space:]')
          
          if [ "$PORT_COUNT" -gt 0 ]; then
            echo "  ✓ Port $PORT is listening"
          else
            echo "  ❌ Port $PORT is NOT listening"
          fi
        done
      else
        echo "  ⚠ No target groups found"
      fi
      echo ""
      
      echo "Checking NLB target health..."
      if [ -n "$TG_PORTS" ]; then
        for PORT in $TG_PORTS; do
          TG_ARN=$(aws elbv2 describe-target-groups --load-balancer-arn "$NLB_ARN" --query "TargetGroups[?Port==\`$PORT\`].TargetGroupArn" --output text)
          HEALTH=$(aws elbv2 describe-target-health --target-group-arn "$TG_ARN" --query 'TargetHealthDescriptions[0].TargetHealth.State' --output text)
          if [ "$HEALTH" = "healthy" ]; then
            echo "  ✓ Port $PORT: healthy"
          else
            echo "  ❌ Port $PORT: $HEALTH"
          fi
        done
      else
        echo "  ⚠ No target groups"
      fi
      echo ""
      
      echo "=========================================="
      echo "Health Check Complete"
      echo "=========================================="
    EOT
    
    interpreter = ["bash", "-c"]
  }
}

output "health_check_complete" {
  value = "Health check executed. Review output above."
}

output "next_steps" {
  description = "Next steps for testing Db2 connectivity"
  value       = <<-EOT

==========================================
Next Steps: Test Db2 Connectivity
==========================================

1. Verify DNS Resolution (from Db2 client machine):
   nslookup <your-custom-domain>
   
   Expected: Should resolve to NLB private IPs
   If fails: Check VPC DNS settings (enable DNS hostnames/resolution)
             Add 169.254.169.253 to DNS search path

2. Download RDS SSL Certificates:
   wget https://truststore.pki.rds.amazonaws.com/<region>/<region>-bundle.pem
   
   Example: wget https://truststore.pki.rds.amazonaws.com/us-east-1/us-east-1-bundle.pem

3. Configure db2dsdriver.cfg:
   <dsn alias="PRODDB" host="<your-domain>" name="BLUDB" port="<your-port>">
     <parameter name="SSLServerCertificate" value="/path/to/<region>-bundle.pem"/>
     <parameter name="SecurityTransportMode" value="SSL"/>
     <parameter name="TLSVersion" value="TLSV12"/>
   </dsn>

4. Test Connection:
   db2 connect to PRODDB user <username> using <password>
   db2 "SELECT CURRENT SERVER FROM SYSIBM.SYSDUMMY1"

5. Verify Encryption:
   db2 "SELECT ENCRYPTION_TYPE FROM SYSIBMADM.APPLICATIONS WHERE AGENT_ID = APPLICATION_HANDLE()"
   
   Expected: Should show 'SSL' or 'TLS'

6. Test SSL Certificate Chain:
   openssl s_client -connect <your-domain>:<port> -showcerts
   
   Expected: Should show RDS certificate chain (confirms end-to-end encryption)

For detailed testing guide, see:
https://github.com/aws-samples/sample-rds-db2-tools/tree/main/tools/End-to-End-Trust#testing-end-to-end-encryption

==========================================

  EOT
}
