#!/bin/bash
# Interactive helper to populate terraform.tfvars for 2-infrastructure
# Uses Terraform data sources (runs with service account credentials)

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "=========================================="
echo "Infrastructure Configuration Helper"
echo "=========================================="
echo ""
echo "This script uses Terraform to discover AWS resources."
echo "It runs with the same service account credentials as deployment."
echo ""

# Get region from backend or prompt
REGION=$(grep 'region' backend.tf 2>/dev/null | grep -v '#' | sed 's/.*"\(.*\)".*/\1/' | head -1)
if [ -z "$REGION" ] || [ "$REGION" = "REPLACE_REGION" ]; then
    read -p "AWS Region [us-east-1]: " REGION
    REGION=${REGION:-us-east-1}
fi

echo "Using region: $REGION"
echo ""

# Run Terraform discovery
echo "Discovering AWS resources... (May take a while on first run)"
cd helpers
echo "aws_region = \"$REGION\"" > terraform.tfvars
terraform init -upgrade > /dev/null 2>&1
terraform apply -auto-approve > /dev/null 2>&1

# Get outputs
VPC_LIST=$(terraform output -json vpc_list | jq -r '.[]')
SUBNET_LIST=$(terraform output -json subnet_list | jq -r '.[]')
SG_LIST=$(terraform output -json sg_list | jq -r '.[]')

cd ..

# Display VPCs with index
echo "=========================================="
echo "Available VPCs"
echo "=========================================="
printf "%-5s %-20s %-18s %s\n" "Index" "VPC ID" "CIDR Block" "Name"
echo "----------------------------------------------------------"
VPC_ARRAY=()
index=0
while IFS='|' read -r vpc_id cidr name; do
    VPC_ARRAY+=("$vpc_id|$cidr|$name")
    printf "%-5s %-20s %-18s %s\n" "$index" "$vpc_id" "$cidr" "$name"
    ((index++))
done <<< "$VPC_LIST"
echo ""

# Step 1: Select VPC by index
read -p "Enter VPC index [0]: " VPC_INDEX
VPC_INDEX=${VPC_INDEX:-0}
while [[ ! "$VPC_INDEX" =~ ^[0-9]+$ ]] || [ "$VPC_INDEX" -lt 0 ] || [ "$VPC_INDEX" -ge "${#VPC_ARRAY[@]}" ]; do
    echo "Invalid index. Please enter 0-$((${#VPC_ARRAY[@]}-1))"
    read -p "Enter VPC index: " VPC_INDEX
done
VPC_ID=$(echo "${VPC_ARRAY[$VPC_INDEX]}" | cut -d'|' -f1)
echo "Selected VPC: $VPC_ID"

# Display subnets for selected VPC with index
echo ""
echo "=========================================="
echo "Subnets in VPC: $VPC_ID"
echo "=========================================="
printf "%-5s %-24s %-15s %-18s %s\n" "Index" "Subnet ID" "AZ" "CIDR Block" "Name"
echo "--------------------------------------------------------------------------------"
SUBNET_ARRAY=()
index=0
while IFS='|' read -r subnet_id vpc_id az cidr name; do
    if [ "$vpc_id" = "$VPC_ID" ]; then
        SUBNET_ARRAY+=("$subnet_id|$az|$cidr|$name")
        printf "%-5s %-24s %-15s %-18s %s\n" "$index" "$subnet_id" "$az" "$cidr" "$name"
        ((index++))
    fi
done <<< "$SUBNET_LIST"
echo ""

# Step 2: Select NLB subnets by index
echo "NLB requires 2+ subnets in different Availability Zones"
read -p "Enter NLB subnet indices (comma-separated, e.g., 0,1): " NLB_INDICES
if [ -z "$NLB_INDICES" ]; then
    echo "Error: At least 2 subnets required for NLB"
    exit 1
fi

# Parse and validate indices
IFS=',' read -ra INDICES <<< "$NLB_INDICES"
NLB_SUBNETS_ARR=()
for idx in "${INDICES[@]}"; do
    idx=$(echo "$idx" | tr -d ' ')
    if [[ ! "$idx" =~ ^[0-9]+$ ]] || [ "$idx" -lt 0 ] || [ "$idx" -ge "${#SUBNET_ARRAY[@]}" ]; then
        echo "Invalid index: $idx"
        exit 1
    fi
    subnet_id=$(echo "${SUBNET_ARRAY[$idx]}" | cut -d'|' -f1)
    NLB_SUBNETS_ARR+=("$subnet_id")
done
NLB_SUBNET_ARRAY=$(printf '"%s",' "${NLB_SUBNETS_ARR[@]}" | sed 's/,$//' | sed 's/^/[/' | sed 's/$/]/')
echo "Selected NLB subnets: ${NLB_SUBNETS_ARR[*]}"

# Step 3: Select EC2 subnet by index
echo ""
read -p "Enter EC2 subnet index (can be same as one of NLB subnets) [0]: " EC2_INDEX
EC2_INDEX=${EC2_INDEX:-0}
while [[ ! "$EC2_INDEX" =~ ^[0-9]+$ ]] || [ "$EC2_INDEX" -lt 0 ] || [ "$EC2_INDEX" -ge "${#SUBNET_ARRAY[@]}" ]; do
    echo "Invalid index. Please enter 0-$((${#SUBNET_ARRAY[@]}-1))"
    read -p "Enter EC2 subnet index: " EC2_INDEX
done
EC2_SUBNET=$(echo "${SUBNET_ARRAY[$EC2_INDEX]}" | cut -d'|' -f1)
echo "Selected EC2 subnet: $EC2_SUBNET"

# Display security groups for selected VPC with index
echo ""
echo "=========================================="
echo "Security Groups in VPC: $VPC_ID"
echo "=========================================="
printf "%-5s %-24s %-30s %s\n" "Index" "Security Group ID" "Name" "Description"
echo "--------------------------------------------------------------------------------"
SG_ARRAY=()
index=0
while IFS='|' read -r sg_id vpc_id name desc; do
    if [ "$vpc_id" = "$VPC_ID" ]; then
        SG_ARRAY+=("$sg_id|$name|$desc")
        printf "%-5s %-24s %-30s %s\n" "$index" "$sg_id" "$name" "$desc"
        ((index++))
    fi
done <<< "$SG_LIST"
echo ""

# Step 4: Select security groups by index
read -p "Enter security group indices for EC2 (comma-separated, e.g., 0,1) [0]: " SG_INDICES
SG_INDICES=${SG_INDICES:-0}

# Parse and validate indices
IFS=',' read -ra INDICES <<< "$SG_INDICES"
SG_ARR=()
for idx in "${INDICES[@]}"; do
    idx=$(echo "$idx" | tr -d ' ')
    if [[ ! "$idx" =~ ^[0-9]+$ ]] || [ "$idx" -lt 0 ] || [ "$idx" -ge "${#SG_ARRAY[@]}" ]; then
        echo "Invalid index: $idx"
        exit 1
    fi
    sg_id=$(echo "${SG_ARRAY[$idx]}" | cut -d'|' -f1)
    SG_ARR+=("$sg_id")
done
SG_ARRAY_STR=$(printf '"%s",' "${SG_ARR[@]}" | sed 's/,$//' | sed 's/^/[/' | sed 's/$/]/')
echo "Selected security groups: ${SG_ARR[*]}"

# Step 5: Other configurations
echo ""
read -p "EC2 instance type [t3.small]: " INSTANCE_TYPE
INSTANCE_TYPE=${INSTANCE_TYPE:-t3.small}

read -p "NLB scheme (internal/internet-facing) [internal]: " NLB_SCHEME
NLB_SCHEME=${NLB_SCHEME:-internal}

# Get VPC CIDR
VPC_CIDR=$(echo "${VPC_ARRAY[$VPC_INDEX]}" | cut -d'|' -f2)
read -p "NLB CIDR block [${VPC_CIDR}]: " NLB_CIDR
NLB_CIDR=${NLB_CIDR:-$VPC_CIDR}

read -p "Listener ports (comma-separated) [1443,50443]: " LISTENER_PORTS
LISTENER_PORTS=${LISTENER_PORTS:-1443,50443}
PORTS_ARRAY=$(echo "$LISTENER_PORTS" | sed 's/,/, /g' | sed 's/^/[/' | sed 's/$/]/')

read -p "Project tag [rdsdb2-proxy]: " PROJECT_TAG
PROJECT_TAG=${PROJECT_TAG:-rdsdb2-proxy}

# Generate terraform.tfvars
echo ""
echo "=========================================="
echo "Generating terraform.tfvars"
echo "=========================================="

cat > terraform.tfvars << EOF
# AWS Configuration (auto-populated from 0-backend-setup)
# Only uncomment if you need to override
# aws_region = "$REGION"

# VPC Configuration
vpc_id     = "$VPC_ID"
subnet_ids = $NLB_SUBNET_ARRAY

# EC2 Configuration
ec2_subnet_id      = "$EC2_SUBNET"
security_group_ids = $SG_ARRAY_STR
ec2_instance_type  = "$INSTANCE_TYPE"

# Certificate Configuration (auto-populated from prerequisites)
# certificate_secret_arn = ""  # Leave empty to auto-fetch
# certificate_arn        = ""  # Leave empty to auto-fetch
# domain_name            = ""  # Leave empty to auto-fetch

# NLB Configuration
nlb_scheme = "$NLB_SCHEME"
nlb_cidr   = "$NLB_CIDR"

# Listener Ports - Add all ports your clients will use
# These ports will be opened on NLB and EC2 proxy
listener_ports = $PORTS_ARRAY

# Tagging
project_tag = "$PROJECT_TAG"
EOF

echo "✓ Created terraform.tfvars"
echo ""
echo "=========================================="
echo "Configuration Summary"
echo "=========================================="
cat terraform.tfvars
echo ""
echo "=========================================="
echo "Next Steps"
echo "=========================================="
echo ""
echo "1. Review terraform.tfvars (edit if needed)"
echo "2. Run: terraform init"
echo "3. Run: terraform apply --auto-approve"
echo ""
