"""
Lab 0 — Your first agent (the "3-line agent" claim, proven).

WHAT THIS PROVES (Article Part 1):
  - An agent is model + tools + prompt, run in a loop.
  - You never tell it *when* to use a tool. The model decides. That's
    "model-driven".

Run:
    python3 lab0_hello_agent.py

Watch: the agent is asked a math question. Instead of guessing, it chooses to
call the `calculator` tool, reads the result, and answers. That choice IS the
agentic loop.
"""

from strands import Agent
from strands_tools import calculator  # a prebuilt example tool

from common import build_model, banner


def main():
    banner("Lab 0: Hello, Agent")

    # The whole agent: a model, one tool, and (implicitly) your question as the prompt.
    agent = Agent(
        model=build_model(),
        tools=[calculator],
    )

    # Calling the agent kicks off the loop until the task is done.
    question = "What is the square root of 1764?"
    print(f"\nUser: {question}\n")

    result = agent(question)

    print("\n--- Final answer ---")
    print(result)

    print(
        "\nNotice: you did not write 'if math question -> call calculator'. "
        "The model decided to use the tool on its own."
    )

    # EXERCISE: change the question to a two-step one and watch multiple tool calls:
    #   question = "What is the square root of 1764, then multiply that by 3?"


if __name__ == "__main__":
    main()
