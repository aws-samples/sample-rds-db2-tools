"""
Preflight check — run this FIRST, before Lab 0.

It verifies your environment and, importantly, prints the exact model-provider
class names your installed Strands version exposes. If any import path in
common.py differs from your version, this tells you what to use — so you never
get stuck guessing.

Run:
    python3 preflight.py
"""

import importlib
import os
import sys


def check(label, fn):
    try:
        result = fn()
        print(f"  [OK]   {label}: {result}")
        return True
    except Exception as e:
        print(f"  [WARN] {label}: {e}")
        return False


def main():
    print("=" * 70)
    print("  Strands tutorial — preflight check")
    print("=" * 70)

    # 1) Python version
    print(f"\nPython: {sys.version.split()[0]}  (need 3.10+)")

    # 2) Core imports
    print("\nCore imports:")
    ok_core = check("from strands import Agent, tool",
                    lambda: __import__("strands", fromlist=["Agent", "tool"]) and "imported")
    check("import strands_tools",
          lambda: importlib.import_module("strands_tools") and "imported")

    # 3) Discover the actual model-provider class names in your version.
    print("\nModel providers available in your strands.models:")
    try:
        models = importlib.import_module("strands.models")
        names = [n for n in dir(models) if n[0].isupper()]
        print(f"  strands.models exposes: {names}")
        print("  -> common.py expects 'BedrockModel' (and 'strands.models.ollama.OllamaModel').")
        print("     If the names above differ, edit build_model() in common.py to match.")
    except Exception as e:
        print(f"  [WARN] could not import strands.models: {e}")

    # 4) Which provider will this session use?
    print("\nProvider selection:")
    from common import use_ollama
    raw = os.environ.get("USE_OLLAMA")
    print(f"  USE_OLLAMA={raw!r}  (truthy values '1'/'true'/'yes'/'on' -> Ollama; else Bedrock)")
    if use_ollama():
        print("  -> Ollama (local).")
        # The Ollama provider needs the optional `ollama` client package.
        try:
            importlib.import_module("ollama")
            print("  [OK]   'ollama' client package is installed.")
        except ImportError:
            print("  [WARN] 'ollama' client package is NOT installed.")
            print("         Fix:  pip install 'strands-agents[ollama]'")
        print("  Also ensure the Ollama server is running and a model is pulled:")
        print("         ollama serve                  # in one shell")
        print("         ollama pull llama3.1:latest   # in another shell")
    else:
        print("  Default -> Amazon Bedrock. Ensure Bedrock model access + AWS creds.")
        print(f"  AWS_REGION={os.environ.get('AWS_REGION', '(unset -> defaults to us-east-1)')}")
        print(f"  AWS_PROFILE={os.environ.get('AWS_PROFILE', '(unset -> default profile)')}")
        pinned = os.environ.get("BEDROCK_MODEL_ID")
        if pinned:
            print(f"  BEDROCK_MODEL_ID={pinned!r} (pinned — auto-discovery skipped)")
        else:
            print("  BEDROCK_MODEL_ID unset -> will auto-discover the newest active Claude model.")
            try:
                from common import resolve_bedrock_model_id
                region = os.environ.get("AWS_REGION", "us-east-1")
                print(f"  [OK]   resolved model id: {resolve_bedrock_model_id(region)}")
            except Exception as e:
                print(f"  [INFO] could not auto-discover yet ({type(e).__name__}). "
                      "Fine if AWS creds aren't set for a local/Ollama run.")

    # 5) AWS creds (only needed for Labs 3-5)
    print("\nAWS credentials (needed for Labs 3-5):")
    try:
        import boto3
        ident = boto3.client("sts").get_caller_identity()
        print(f"  [OK]   Authenticated as: {ident.get('Arn')}")
    except Exception as e:
        print(f"  [INFO] Not configured yet ({type(e).__name__}). Fine for Labs 0-2.")

    # 6) Try actually building the model object
    print("\nBuilding the model object (build_model)...")
    try:
        from common import build_model
        m = build_model()
        print(f"  [OK]   build_model() returned: {type(m).__name__}")
    except Exception as e:
        print(f"  [WARN] build_model() failed: {e}")
        print("         Fix the import/name in common.py using the class list above.")

    print("\nDone. If core imports are OK, start with:  python3 lab0_hello_agent.py")


if __name__ == "__main__":
    main()
