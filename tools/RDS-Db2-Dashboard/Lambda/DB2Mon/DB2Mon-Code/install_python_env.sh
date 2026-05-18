#!/bin/bash
#
HOMEDIR="$PWD"
cd "$HOMEDIR"
python3 -m venv db2s3env
source "$HOMEDIR/db2s3env/bin/activate"
pip3 install -r requirements.txt
