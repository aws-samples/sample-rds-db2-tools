#!/bin/bash
# Setup Terraform plugin cache to avoid re-downloading providers
# Run this once before deploying modules

set -e

CACHE_DIR="$HOME/.terraform.d/plugin-cache"

echo "=========================================="
echo "Terraform Plugin Cache Setup"
echo "=========================================="
echo ""

# Create cache directory
if [ ! -d "$CACHE_DIR" ]; then
    mkdir -p "$CACHE_DIR"
    echo "✓ Created cache directory: $CACHE_DIR"
else
    echo "✓ Cache directory exists: $CACHE_DIR"
fi

# Create or update .terraformrc
TERRAFORMRC="$HOME/.terraformrc"
if [ -f "$TERRAFORMRC" ]; then
    if grep -q "plugin_cache_dir" "$TERRAFORMRC"; then
        echo "✓ Plugin cache already configured in $TERRAFORMRC"
    else
        echo "" >> "$TERRAFORMRC"
        echo "plugin_cache_dir = \"$CACHE_DIR\"" >> "$TERRAFORMRC"
        echo "✓ Added plugin cache to existing $TERRAFORMRC"
    fi
else
    cat > "$TERRAFORMRC" << EOF
# Terraform plugin cache configuration
plugin_cache_dir = "$CACHE_DIR"
EOF
    echo "✓ Created $TERRAFORMRC with plugin cache"
fi

echo ""
echo "=========================================="
echo "Setup Complete!"
echo "=========================================="
echo ""
echo "Benefits:"
echo "  - Providers downloaded once and reused"
echo "  - Faster 'terraform init' in all modules"
echo "  - Saves bandwidth and time"
echo ""
echo "Cache location: $CACHE_DIR"
echo ""
echo "To clear cache (if needed):"
echo "  rm -rf $CACHE_DIR/*"
echo ""
